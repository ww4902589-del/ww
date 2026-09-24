"""作品库端点的真实 HTTP 行为。

为什么单独起一个服务：作品库的几条边界只在真实请求上才成立——

* **路径只能由 work_id 换。** 预览与原图两条路由都必须从数据库取路径；
  客户端一旦能传 ``path``，作品库就成了"读任意文件"的入口。
* **移除不是删除。** 移除记录后磁盘上的图必须一字不变，且能在「显示已移除」里恢复。
* **重新索引不能复活已移除的记录**，也不能清掉收藏与标签。
* **失败要有答复。** 未知 work_id、非法排序、非法维护动作都必须是 400 而不是挂起。

状态每次都从一个临时数据目录开始，因此断言是确定的。
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import install_static_assets  # noqa: E402

from PIL import Image  # noqa: E402

import comfybatch_v2_app as app_module  # noqa: E402
from comfybatch_library import LibraryStore  # noqa: E402
from comfybatch_v2_app import Application, Handler  # noqa: E402
from comfybatch_v2_core import ResultReviewStore  # noqa: E402

RESPONSE_TIMEOUT = 20


class LibraryRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._temp = tempfile.TemporaryDirectory(prefix="comfybatch-library-routes-")
        cls.root = pathlib.Path(cls._temp.name)
        cls.images = cls.root / "output" / "run-a"
        cls.images.mkdir(parents=True)

        app = Application(settings_path=cls.root / "settings.json", backup_settings_path=None)
        app.reviews = ResultReviewStore(cls.root / "reviews")
        app.library = LibraryStore(cls.root / "library.db")
        app.library_sync_error = ""
        app_module.APP = app
        install_static_assets(app_module)

        cls.app = app
        #: 六条记录：一条待确认、一条已通过、一条需重做、一条没有成图、一条在另一批次、
        #: 以及一条文件已被挪走的记录。
        for index in range(1, 6):
            image = cls.images / f"{index:03d}_00001_.png"
            Image.new("RGB", (32 + index, 22), (index * 20, 30, 40)).save(image, "PNG")
        cls.document = {
            "run_id": "run-a",
            "output_dir": str(cls.root / "output"),
            "items": {
                str(index): {
                    "index": index,
                    "title": f"作品{index}",
                    "status": "completed" if index != 4 else "error",
                    "error": "节点报错" if index == 4 else "",
                    "copied_to": str(cls.images / f"{index:03d}_00001_.png") if index != 4 else "",
                    "prompt": f"蓝裙 编号{index}",
                    "compiled_prompt": f"compiled {index}",
                    "review_status": "已通过" if index == 2 else ("需重做" if index == 3 else "待确认"),
                    "note": "",
                    "elapsed": 1.0,
                    "generation": {
                        "model": "krea2_turbo_int8_convrot.safetensors",
                        "workflow_path": "Krea2.json",
                        "seeds": {"305": index * 100},
                    },
                    "attempts": [{"attempt": 1, "prompt_id": f"p-{index}"}],
                }
                for index in range(1, 6)
            },
        }
        app.reviews.save("run-a", cls.document)

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls._temp.cleanup()

    # ------------------------------------------------------------- helpers

    def setUp(self):
        """每个用例从干净的库开始。

        「移除」是墓碑：上一次用例删掉的记录不会再被重新索引进回来，所以共享
        一个库会让断言互相串味（表现为 total 莫名少一条），而不是暴露真实缺陷。
        """
        for suffix in ("", "-wal", "-shm"):
            leftover = self.root / f"library.db{suffix}"
            if leftover.exists():
                leftover.unlink()
        self.app.library = LibraryStore(self.root / "library.db")
        self.app.library_sync_error = ""
        self.reindex()

    @classmethod
    def url(cls, path: str) -> str:
        return f"http://127.0.0.1:{cls.port}{path}"

    @classmethod
    def request(cls, path: str, payload=None) -> tuple[int, str]:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if payload is None:
            request = urllib.request.Request(cls.url(path), method="GET")
        else:
            request = urllib.request.Request(
                cls.url(path), data=json.dumps(payload).encode("utf-8"),
                method="POST", headers={"Content-Type": "application/json"},
            )
        try:
            with opener.open(request, timeout=RESPONSE_TIMEOUT) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    @classmethod
    def library(cls, query: str = "") -> dict:
        status, body = cls.request("/api/library" + query)
        if status != 200:
            raise AssertionError(f"/api/library{query} returned {status}: {body!r}")
        return json.loads(body)["library"]

    def reindex(self) -> None:
        status, body = self.request("/api/library/reindex", {})
        self.assertEqual(200, status, body)
        # 每个用例都从同一份来源重建，避免兄弟用例的删除/收藏串味。
        self.assertEqual(json.loads(body)["ok"], True)

    # --------------------------------------------------------------- 索引

    def test_the_index_is_built_from_the_review_documents(self):
        self.reindex()
        page = self.library()
        self.assertEqual(page["total"], 5)
        self.assertEqual(page["stats"]["works"], 5)
        self.assertEqual(page["stats"]["runs"], 1)
        first = next(item for item in page["items"] if item["index"] == 1)
        self.assertEqual(first["title"], "作品1")
        self.assertEqual(first["seed"], "100")
        self.assertEqual(first["work_id"], "run-a:0001")
        self.assertEqual(first["review_status"], "待确认")
        self.assertTrue(first["available"])

    def test_keyword_and_status_filters_reach_the_endpoint(self):
        self.reindex()
        self.assertEqual(1, self.library("?q=" + urllib.parse.quote("编号3"))["total"])
        self.assertEqual(1, self.library("?review_status=" + urllib.parse.quote("已通过"))["total"])
        self.assertEqual(1, self.library("?q=" + urllib.parse.quote("作品5") + "&sort=oldest")["total"])
        self.assertEqual(2, len(self.library("?limit=2")["items"]))

    def test_an_invalid_sort_or_status_is_a_clean_400(self):
        for query in ("?sort=%3B%20DROP%20TABLE%20works", "?review_status=" + urllib.parse.quote("乱写")):
            with self.subTest(query=query):
                status, body = self.request("/api/library" + query)
                self.assertEqual(400, status, body)
                self.assertFalse(json.loads(body)["ok"])
        self.reindex()
        self.assertEqual(5, self.library()["total"], "被拒绝的查询不得损坏数据")

    # ------------------------------------------------------------ 用户数据

    def test_favorite_round_trips_through_the_endpoint(self):
        self.reindex()
        status, body = self.request("/api/library/favorite", {"work_id": "run-a:0001", "favorite": True})
        self.assertEqual(200, status, body)
        self.assertTrue(json.loads(body)["item"]["favorite"])
        self.assertEqual(1, self.library("?favorite=1")["total"])
        status, body = self.request("/api/library/favorite", {"work_id": "run-a:0001", "favorite": False})
        self.assertEqual(200, status, body)
        self.assertEqual(0, self.library("?favorite=1")["total"])

    def test_tags_are_replaced_as_a_whole_set(self):
        self.reindex()
        status, body = self.request("/api/library/tags", {"work_id": "run-a:0002", "tags": ["服装", "冬装"]})
        self.assertEqual(200, status, body)
        self.assertEqual(["冬装", "服装"], sorted(json.loads(body)["item"]["tags"]))
        status, body = self.request("/api/library/tags", {"work_id": "run-a:0002", "tags": []})
        self.assertEqual(200, status, body)
        self.assertEqual([], json.loads(body)["item"]["tags"])

    def test_single_tag_add_and_remove_still_work(self):
        self.reindex()
        self.request("/api/library/tags", {"work_id": "run-a:0002", "tag": "参考"})
        self.assertEqual(1, self.library("?q=" + urllib.parse.quote("参考"))["total"])
        status, body = self.request("/api/library/tags", {"work_id": "run-a:0002", "tag": "参考", "action": "remove"})
        self.assertEqual(200, status, body)
        self.assertEqual([], json.loads(body)["item"]["tags"])

    def test_a_non_list_tags_payload_is_rejected(self):
        status, body = self.request("/api/library/tags", {"work_id": "run-a:0002", "tags": "服装"})
        self.assertEqual(400, status, body)

    def test_tags_on_an_unknown_record_are_a_400(self):
        status, body = self.request("/api/library/tags", {"work_id": "run-z:0009", "tags": ["甲"]})
        self.assertEqual(400, status, body)
        self.assertFalse(json.loads(body)["ok"])

    # -------------------------------------------------------- 移除与恢复

    def test_removing_a_record_keeps_the_image_and_can_be_undone(self):
        self.reindex()
        image = self.images / "001_00001_.png"
        before = image.read_bytes()
        status, body = self.request("/api/library/delete", {"work_id": "run-a:0001", "reason": "重复"})
        self.assertEqual(200, status, body)
        self.assertTrue(json.loads(body)["deleted"]["deleted"])
        self.assertEqual(4, self.library()["total"])
        self.assertEqual(5, self.library("?include_deleted=1")["total"])
        self.assertEqual(before, image.read_bytes(), "移除记录不得动磁盘上的图片")
        self.assertEqual(1, self.library()["stats"]["deleted"])

        status, body = self.request("/api/library/restore", {"work_id": "run-a:0001"})
        self.assertEqual(200, status, body)
        self.assertEqual(5, self.library()["total"])
        self.assertEqual(before, image.read_bytes())

    def test_a_reindex_does_not_resurrect_a_removed_record(self):
        self.reindex()
        self.request("/api/library/delete", {"work_id": "run-a:0005"})
        self.reindex()
        self.assertEqual(4, self.library()["total"])
        self.assertEqual(1, self.library()["stats"]["deleted"])

    def test_maintain_actions_are_whitelisted(self):
        self.reindex()
        status, body = self.request("/api/library/maintain", {"action": "vacuum"})
        self.assertEqual(200, status, body)
        self.assertIn("vacuum", json.loads(body))
        status, body = self.request("/api/library/maintain", {"action": "rm -rf"})
        self.assertEqual(400, status, body)
        status, body = self.request("/api/library/maintain", {"action": "clear-index"})
        self.assertEqual(200, status, body)
        self.assertEqual(0, self.library()["total"])

    # ------------------------------------------------------- 读图安全边界

    def test_the_preview_is_addressed_by_work_id_only(self):
        self.reindex()
        status, body = self.request("/api/library/preview?work_id=run-a:0001&size=120")
        self.assertEqual(200, status)
        self.assertEqual(b"\xff\xd8", body[:2], "预览必须是 JPEG 缩略图")
        # 只给路径、不给 work_id 不给读；给不存在的 work_id 也不给读。
        for query in ("?path=" + urllib.parse.quote(str(self.images / "001_00001_.png")),
                      "?work_id=run-z:0009", "?work_id=../../settings.json"):
            with self.subTest(query=query):
                status, _ = self.request("/api/library/preview" + query)
                self.assertEqual(400, status)

    def test_the_original_image_is_served_for_a_record_that_has_one(self):
        self.reindex()
        source = self.images / "001_00001_.png"
        status, body = self.request("/api/library/image?work_id=run-a:0001")
        self.assertEqual(200, status)
        self.assertEqual(source.read_bytes(), body)
        # 没有成图的记录（第 4 条是失败项）如实报错，不返回别的文件。
        status, _ = self.request("/api/library/image?work_id=run-a:0004")
        self.assertEqual(400, status)

    def test_a_record_whose_file_moved_reports_unavailable(self):
        self.reindex()
        image = self.images / "002_00001_.png"
        moved = image.with_suffix(".moved")
        image.rename(moved)
        try:
            item = next(row for row in self.library()["items"] if row["index"] == 2)
            self.assertFalse(item["available"])
            self.assertEqual("run-a:0002", item["work_id"], "文件挪走不等于记录消失")
            status, _ = self.request("/api/library/preview?work_id=run-a:0002")
            self.assertEqual(400, status)
        finally:
            moved.rename(image)

    def test_the_fifth_stage_tab_is_reachable_and_the_page_still_loads(self):
        status, body = self.request("/")
        self.assertEqual(200, status)
        page = body.decode("utf-8")
        self.assertIn('data-step-target="5"', page)
        self.assertIn('data-step="5"', page)


if __name__ == "__main__":
    unittest.main()
