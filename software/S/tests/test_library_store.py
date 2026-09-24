"""SQLite 作品库：索引、检索、标签收藏与墓碑纪律。

覆盖范围（正常路径 + 失败路径 + 关键安全边界）：

* 建库与重建：首次建表、重复打开幂等、损坏文件不阻塞且旧字节保留；
* 同步：一次运行的复核文档入 INDEX、重复同步不产生重复行、复核状态跟随文档更新；
* 用户数据不可被同步覆盖：收藏、标签、删除标记在重新同步后原样保留（"删除不复活"）；
* 检索：关键词跨标题/提示词/标签命中、多条件组合、排序白名单、分页与总数；
* 失败路径：未知 work_id、非法排序、非法状态、对已删除记录打标签/收藏均如实报错；
* 文件事实：成图被替换（mtime 变化）时尺寸与字节数取实际值，文件消失时标记不可用；
* 注入：搜索词里的 %、_ 与引号按字面处理，不作为通配或 SQL 片段。

刻意不启动真实 ComfyUI：作品库是从复核文档派生的索引，测试直接构造文档即可。
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from PIL import Image  # noqa: E402

from comfybatch_library import (  # noqa: E402
    MAX_PAGE_SIZE,
    REVIEW_STATUSES,
    SCHEMA_VERSION,
    SORTS,
    LibraryStore,
    work_id_for,
)


class _TempLibrary(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="comfybatch-library-")
        self.root = pathlib.Path(self._tmp.name)
        self.images = self.root / "output"
        self.images.mkdir(parents=True, exist_ok=True)
        self.store = LibraryStore(self.root / "library.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ---------------------------------------------------------- helpers

    def make_image(self, name: str, size: tuple[int, int] = (64, 32)) -> pathlib.Path:
        path = self.images / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, (10, 120, 200)).save(path, "PNG")
        return path

    def document(self, run_id: str, entries: list[dict], output_dir: str = "") -> dict:
        return {
            "run_id": run_id,
            "output_dir": output_dir or str(self.images),
            "items": {str(entry.get("index")): entry for entry in entries},
        }

    def entry(self, index: int, title: str, image: pathlib.Path | None, **overrides) -> dict:
        base = {
            "index": index,
            "title": title,
            "preset_name": "",
            "status": "completed",
            "copied_to": str(image) if image else "",
            "prompt": f"prompt {title}",
            "negative_prompt": "",
            "compiled_prompt": f"compiled {title}",
            "review_status": "待确认",
            "note": "",
            "elapsed": 1.5,
            "generation": {
                "model": "krea2_turbo_int8_convrot.safetensors",
                "workflow_path": "Krea2.json",
                "workflow_variant": "默认",
                "seeds": {"305": index * 11},
            },
            "attempts": [{"attempt": 1, "prompt_id": f"p-{index}"}],
        }
        base.update(overrides)
        return base


class SchemaTests(_TempLibrary):
    def test_the_database_is_created_with_a_recorded_schema_version(self):
        self.assertTrue((self.root / "library.db").is_file())
        self.assertEqual(self.store.stats()["schema_version"], SCHEMA_VERSION)
        connection = sqlite3.connect(str(self.root / "library.db"))
        try:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        finally:
            connection.close()
        for table in ("works", "work_tags", "runs", "meta"):
            self.assertIn(table, tables)

    def test_reopening_the_same_file_does_not_duplicate_or_lose_rows(self):
        image = self.make_image("a_00001_.png")
        self.store.sync_run("run-a", self.document("run-a", [self.entry(1, "甲", image)]))
        reopened = LibraryStore(self.root / "library.db")
        self.assertEqual(reopened.stats()["works"], 1)

    def test_a_corrupt_file_is_moved_aside_instead_of_blocking_the_library(self):
        """作品库只是可重建的索引：坏文件不该让软件起不来，但字节必须留着。"""
        broken = self.root / "broken.db"
        broken.write_bytes(b"this is not a sqlite file" * 64)
        store = LibraryStore(broken)
        self.assertTrue(store.last_warning, "损坏文件必须留下可见提示")
        self.assertEqual(store.stats()["works"], 0)
        backups = list(self.root.glob("broken.db.corrupt-*"))
        self.assertEqual(len(backups), 1, "旧字节必须另存而不是丢弃")
        self.assertGreater(backups[0].stat().st_size, 0)


class SyncTests(_TempLibrary):
    def test_a_review_document_becomes_queryable_rows(self):
        image = self.make_image("001_00001_.png")
        counters = self.store.sync_run("run-a", self.document("run-a", [self.entry(1, "红衣", image)]))
        self.assertEqual(counters, {"inserted": 1, "updated": 0, "skipped": 0})
        page = self.store.query()
        self.assertEqual(page["total"], 1)
        item = page["items"][0]
        self.assertEqual(item["work_id"], "run-a:0001")
        self.assertEqual(item["title"], "红衣")
        self.assertEqual(item["seed"], "11", "种子必须取自 generation.seeds")
        self.assertEqual(item["width"], 64)
        self.assertEqual(item["height"], 32)
        self.assertTrue(item["available"])

    def test_resyncing_the_same_document_updates_instead_of_duplicating(self):
        image = self.make_image("001_00001_.png")
        document = self.document("run-a", [self.entry(1, "红衣", image)])
        self.store.sync_run("run-a", document)
        counters = self.store.sync_run("run-a", document)
        self.assertEqual(counters, {"inserted": 0, "updated": 1, "skipped": 0})
        self.assertEqual(self.store.query()["total"], 1)

    def test_a_review_decision_propagates_into_the_index(self):
        image = self.make_image("001_00001_.png")
        self.store.sync_run("run-a", self.document("run-a", [self.entry(1, "红衣", image)]))
        after = self.document("run-a", [self.entry(1, "红衣", image, review_status="已通过", note="构图正确")])
        self.store.sync_run("run-a", after)
        item = self.store.get("run-a:0001")
        self.assertEqual(item["review_status"], "已通过")
        self.assertEqual(item["note"], "构图正确")

    def test_the_same_index_in_two_runs_stays_two_records(self):
        first = self.make_image("run-a/001_00001_.png")
        second = self.make_image("run-b/001_00001_.png")
        self.store.sync_run("run-a", self.document("run-a", [self.entry(1, "甲", first)]))
        self.store.sync_run("run-b", self.document("run-b", [self.entry(1, "乙", second)]))
        self.assertEqual(self.store.query()["total"], 2)
        self.assertEqual({item["work_id"] for item in self.store.query()["items"]}, {"run-a:0001", "run-b:0001"})

    def test_a_resync_does_not_move_the_first_indexed_time(self):
        """``indexed_at`` 记的是"第一次进库"，再读一次来源不能把它改成现在。"""
        image = self.make_image("001_00001_.png")
        self.store.sync_run("run-a", self.document("run-a", [self.entry(1, "红衣", image)]))
        first = self.store.get("run-a:0001")["indexed_at"]
        time.sleep(0.02)
        self.store.sync_run("run-a", self.document("run-a", [self.entry(1, "红衣改名", image)]))
        item = self.store.get("run-a:0001")
        self.assertEqual(item["indexed_at"], first)
        self.assertEqual(item["title"], "红衣改名", "派生字段仍须跟随来源更新")
        self.assertGreaterEqual(item["updated_at"], first)

    def test_runs_are_listed_newest_first_with_their_totals(self):
        first = self.make_image("001_00001_.png")
        second = self.make_image("002_00001_.png")
        self.store.sync_run("run-a", self.document("run-a", [self.entry(1, "甲", first)]))
        time.sleep(0.02)
        self.store.sync_run(
            "run-b",
            self.document("run-b", [self.entry(1, "乙", second), self.entry(2, "丙", None, status="error")]),
        )
        runs = self.store.runs()
        self.assertEqual([row["run_id"] for row in runs], ["run-b", "run-a"])
        self.assertEqual(runs[0]["total"], 2)
        self.assertEqual(runs[0]["completed"], 1)
        self.assertEqual(runs[0]["errors"], 1)

    def test_a_failed_item_is_indexed_as_a_failure_without_an_image(self):
        document = self.document("run-a", [self.entry(1, "失败项", None, status="error", error="节点报错")])
        self.store.sync_run("run-a", document)
        item = self.store.query()["items"][0]
        self.assertEqual(item["status"], "error")
        self.assertEqual(item["error"], "节点报错")
        self.assertFalse(item["available"])
        self.assertEqual(self.store.stats()["by_status"], {"待确认": 1, "已通过": 0, "需重做": 0, "已替换": 0})

    def test_an_empty_or_malformed_document_is_ignored(self):
        self.assertEqual(self.store.sync_run("", {"items": {"1": {}}}), {"inserted": 0, "updated": 0, "skipped": 0})
        self.assertEqual(self.store.sync_run("run-a", {"items": []}), {"inserted": 0, "updated": 0, "skipped": 0})
        self.assertEqual(self.store.sync_run("run-a", {"items": {"1": "not-a-dict"}})["skipped"], 1)

    def test_sync_all_rebuilds_the_index_from_disk_and_skips_broken_files(self):
        image = self.make_image("001_00001_.png")

        class Reviews:
            def __init__(self, root: pathlib.Path):
                self.root = root

            def load(self, run_id: str) -> dict:
                return json.loads((self.root / f"{run_id}.json").read_text(encoding="utf-8"))

        reviews_root = self.root / "reviews"
        reviews_root.mkdir()
        (reviews_root / "run-a.json").write_text(
            json.dumps(self.document("run-a", [self.entry(1, "甲", image)]), ensure_ascii=False),
            encoding="utf-8",
        )
        (reviews_root / "run-broken.json").write_text("{not json", encoding="utf-8")
        totals = self.store.sync_all(Reviews(reviews_root))
        self.assertEqual(totals["runs"], 1)
        self.assertEqual(totals["failed"], 1, "坏文档必须计入失败而不是中断整次重建")
        self.assertEqual(self.store.query()["total"], 1)


class UserDataSurvivesSyncTests(_TempLibrary):
    def test_a_favourite_and_tags_survive_a_resync(self):
        image = self.make_image("001_00001_.png")
        document = self.document("run-a", [self.entry(1, "红衣", image)])
        self.store.sync_run("run-a", document)
        self.store.set_favorite("run-a:0001", True)
        self.store.add_tags("run-a:0001", ["服装", "红色"])
        self.store.sync_run("run-a", document)
        item = self.store.get("run-a:0001")
        self.assertTrue(item["favorite"])
        self.assertEqual(item["tags"], ["服装", "红色"])

    def test_a_deleted_record_is_not_resurrected_by_a_resync(self):
        """删除是墓碑，不是删行：再读一次来源不能把它带回来。"""
        image = self.make_image("001_00001_.png")
        document = self.document("run-a", [self.entry(1, "红衣", image)])
        self.store.sync_run("run-a", document)
        self.store.delete("run-a:0001", reason="重复")
        self.store.sync_run("run-a", document)
        self.assertEqual(self.store.query()["total"], 0, "已删除记录不得回到默认列表")
        self.assertEqual(self.store.stats()["deleted"], 1)
        self.assertEqual(self.store.query(include_deleted=True)["total"], 1)

    def test_a_deleted_record_can_be_restored(self):
        image = self.make_image("001_00001_.png")
        self.store.sync_run("run-a", self.document("run-a", [self.entry(1, "红衣", image)]))
        self.store.delete("run-a:0001")
        self.store.restore("run-a:0001")
        self.assertEqual(self.store.query()["total"], 1)
        self.assertEqual(self.store.stats()["deleted"], 0)

    def test_restoring_something_that_is_not_deleted_is_an_error(self):
        image = self.make_image("001_00001_.png")
        self.store.sync_run("run-a", self.document("run-a", [self.entry(1, "红衣", image)]))
        with self.assertRaises(ValueError):
            self.store.restore("run-a:0001")

    def test_a_redo_keeps_the_same_record_identity(self):
        """重做会换掉磁盘上的文件，但标签必须留在同一条记录上。"""
        first = self.make_image("001_00001_.png", size=(64, 32))
        self.store.sync_run("run-a", self.document("run-a", [self.entry(1, "红衣", first)]))
        self.store.add_tags("run-a:0001", ["保留"])
        redone = self.make_image("001_00001_.png", size=(128, 64))
        self.store.sync_run(
            "run-a",
            self.document(
                "run-a",
                [self.entry(1, "红衣", redone, review_status="已替换", attempts=[{"attempt": 1}, {"attempt": 2}])],
            ),
        )
        item = self.store.get("run-a:0001")
        self.assertEqual(item["tags"], ["保留"])
        self.assertEqual(item["attempt_count"], 2)
        self.assertEqual((item["width"], item["height"]), (128, 64), "尺寸必须反映重做后的文件")


class QueryTests(_TempLibrary):
    def setUp(self) -> None:
        super().setUp()
        rows = []
        for index in range(1, 8):
            image = self.make_image(f"001/{index:03d}_00001_.png", size=(10 + index, 10))
            rows.append(self.entry(index, f"作品{index}", image, prompt=f"蓝裙 编号{index}"))
        rows[0]["review_status"] = "已通过"
        rows[1]["review_status"] = "需重做"
        rows[2]["generation"]["model"] = "另一个模型.safetensors"
        self.store.sync_run("run-a", self.document("run-a", rows))
        second = self.make_image("002/001_00001_.png")
        self.store.sync_run(
            "run-b", self.document("run-b", [self.entry(1, "另一批", second, prompt="绿衣")])
        )
        self.store.set_favorite("run-a:0003", True)
        self.store.add_tags("run-a:0004", ["冬装", "参考"])

    def test_keyword_search_covers_prompt_title_and_tags(self):
        self.assertEqual(self.store.query(q="编号5")["total"], 1)
        self.assertEqual(self.store.query(q="作品7")["total"], 1)
        self.assertEqual(self.store.query(q="冬装")["total"], 1, "标签必须参与关键词检索")
        self.assertEqual(self.store.query(q="不存在的词")["total"], 0)

    def test_filters_combine_with_and(self):
        self.assertEqual(self.store.query(run_id="run-b")["total"], 1)
        self.assertEqual(self.store.query(review_status="已通过")["total"], 1)
        self.assertEqual(self.store.query(favorite=True)["total"], 1)
        self.assertEqual(self.store.query(model="另一个模型.safetensors")["total"], 1)
        self.assertEqual(self.store.query(run_id="run-a", review_status="需重做")["total"], 1)
        self.assertEqual(self.store.query(run_id="run-b", review_status="需重做")["total"], 0)
        self.assertEqual(self.store.query(tag="参考")["total"], 1)

    def test_newest_and_oldest_order_by_the_image_and_push_missing_files_last(self):
        """真机上撞到过：默认排序把"成图已不在原位"的记录排到最前面。

        排序键必须落在**图片自己的时间**上；没有图片的记录（失败项、文件被挪走的项）
        排到后面，而不是因为它们"刚进索引"就冒到用户真正有的作品之上。
        """
        import os

        run = "run-sort"
        fresh = self.make_image("sort/001_00001_.png")
        older = self.make_image("sort/002_00001_.png")
        rows = [
            self.entry(1, "有图", fresh),
            self.entry(2, "更早的图", older),
            self.entry(3, "没有图", None, status="error", error="节点报错"),
        ]
        os.utime(older, (1_600_000_000, 1_600_000_000))
        self.store.sync_run(run, self.document(run, rows))
        newest = [item["work_id"] for item in self.store.query(run_id=run, sort="newest")["items"]]
        oldest = [item["work_id"] for item in self.store.query(run_id=run, sort="oldest")["items"]]
        self.assertEqual(newest, [f"{run}:0001", f"{run}:0002", f"{run}:0003"])
        self.assertEqual(oldest, [f"{run}:0002", f"{run}:0001", f"{run}:0003"])

    def test_every_declared_sort_is_accepted_and_orders_results(self):
        for key in SORTS:
            with self.subTest(sort=key):
                page = self.store.query(sort=key)
                self.assertEqual(page["total"], 8)
                self.assertEqual(len(page["items"]), 8)

    def test_paging_reports_the_full_total_not_the_page_size(self):
        page = self.store.query(limit=3, offset=0)
        self.assertEqual(page["total"], 8)
        self.assertEqual(len(page["items"]), 3)
        second = self.store.query(limit=3, offset=3)
        self.assertEqual({item["work_id"] for item in page["items"]} & {item["work_id"] for item in second["items"]}, set())

    def test_the_page_size_is_clamped_to_a_sane_range(self):
        self.assertEqual(self.store.query(limit=0)["limit"], 1)
        self.assertEqual(self.store.query(limit=99999)["limit"], MAX_PAGE_SIZE)

    def test_facets_are_scoped_to_live_records(self):
        self.store.delete("run-a:0001")
        facets = self.store.query()["facets"]
        self.assertEqual(facets["total"], 7)
        self.assertEqual(facets["deleted"], 1)
        self.assertEqual(facets["favorite"], 1)
        self.assertEqual(facets["by_status"]["已通过"], 0)
        self.assertEqual([entry["tag"] for entry in facets["tags"]], ["冬装", "参考"])
        self.assertEqual(len(facets["runs"]), 2)

    def test_search_terms_are_literal_not_wildcards(self):
        """搜索 `%` 与 `_` 必须是找这两个字符本身，不能变成"匹配一切"。"""
        first = self.make_image("003/001_00001_.png")
        second = self.make_image("003/002_00001_.png")
        third = self.make_image("003/003_00001_.png")
        fourth = self.make_image("003/004_00001_.png")
        self.store.sync_run(
            "run-c",
            self.document(
                "run-c",
                [
                    self.entry(1, "50% 折扣", first, prompt="字面百分号"),
                    self.entry(2, "50 折", second, prompt="没有百分号"),
                    self.entry(3, "a_b", third, prompt="字面下划线"),
                    self.entry(4, "axb", fourth, prompt="没有下划线"),
                ],
            ),
        )
        self.assertEqual(self.store.query(q="50%")["total"], 1)
        self.assertEqual(self.store.query(q="a_b")["total"], 1)
        self.assertEqual(self.store.query(q="%")["total"], 1)

    def test_a_quote_in_the_search_term_is_not_a_sql_fragment(self):
        page = self.store.query(q="作品1' OR '1'='1")
        self.assertEqual(page["total"], 0)
        self.assertEqual(self.store.query(q="100%纯棉")["total"], 0)

    def test_an_unknown_sort_or_status_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.query(sort="; DROP TABLE works")
        with self.assertRaises(ValueError):
            self.store.query(review_status="随便写的状态")
        self.assertEqual(self.store.query()["total"], 8, "被拒绝的查询不得破坏数据")


class TagAndFavoriteTests(_TempLibrary):
    def setUp(self) -> None:
        super().setUp()
        image = self.make_image("001_00001_.png")
        self.store.sync_run("run-a", self.document("run-a", [self.entry(1, "红衣", image)]))

    def test_tags_are_trimmed_deduplicated_and_case_insensitive(self):
        item = self.store.add_tags("run-a:0001", ["  服装 ", "服装", "Clothing", "clothing", ""])
        self.assertEqual(item["tags"], ["Clothing", "服装"])

    def test_set_tags_replaces_the_whole_set(self):
        self.store.add_tags("run-a:0001", ["甲", "乙"])
        self.assertEqual(self.store.set_tags("run-a:0001", ["丙"])["tags"], ["丙"])

    def test_removing_a_tag_that_is_not_there_changes_nothing(self):
        self.store.add_tags("run-a:0001", ["甲"])
        self.assertEqual(self.store.remove_tag("run-a:0001", "不存在")["tags"], ["甲"])

    def test_favorite_toggles_both_ways(self):
        self.assertTrue(self.store.set_favorite("run-a:0001", True)["favorite"])
        self.assertFalse(self.store.set_favorite("run-a:0001", False)["favorite"])

    def test_operations_on_an_unknown_record_are_rejected(self):
        for call in (
            lambda: self.store.get("run-z:0009"),
            lambda: self.store.add_tags("run-z:0009", ["甲"]),
            lambda: self.store.remove_tag("run-z:0009", "甲"),
            lambda: self.store.set_tags("run-z:0009", ["甲"]),
            lambda: self.store.set_favorite("run-z:0009", True),
            lambda: self.store.delete("run-z:0009"),
        ):
            with self.subTest(call=call):
                with self.assertRaises(ValueError):
                    call()

    def test_a_deleted_record_rejects_tagging_until_it_is_restored(self):
        self.store.delete("run-a:0001")
        with self.assertRaises(ValueError):
            self.store.add_tags("run-a:0001", ["甲"])
        self.store.restore("run-a:0001")
        self.assertEqual(self.store.add_tags("run-a:0001", ["甲"])["tags"], ["甲"])

    def test_deleting_twice_is_an_error_rather_than_a_silent_no_op(self):
        self.store.delete("run-a:0001")
        with self.assertRaises(ValueError):
            self.store.delete("run-a:0001")


class FileFactTests(_TempLibrary):
    def test_a_missing_image_stays_indexed_but_reports_unavailable(self):
        image = self.make_image("001_00001_.png")
        self.store.sync_run("run-a", self.document("run-a", [self.entry(1, "红衣", image)]))
        image.unlink()
        item = self.store.query()["items"][0]
        self.assertFalse(item["available"])
        self.assertEqual(item["work_id"], "run-a:0001", "文件没了不等于记录没了")

    def test_a_never_written_image_is_still_recorded(self):
        self.store.sync_run(
            "run-a",
            self.document("run-a", [self.entry(1, "未落盘", None, status="completed")]),
        )
        item = self.store.query()["items"][0]
        self.assertFalse(item["available"])
        self.assertEqual(item["size_bytes"], 0)

    def test_stats_and_maintenance_do_not_touch_the_images(self):
        image = self.make_image("001_00001_.png")
        self.store.sync_run("run-a", self.document("run-a", [self.entry(1, "红衣", image)]))
        before = image.read_bytes()
        self.store.set_favorite("run-a:0001", True)
        stats = self.store.stats()
        self.assertEqual(stats["favorite"], 1)
        self.assertEqual(stats["image_bytes"], len(before))
        self.assertEqual(stats["favorite_bytes"], len(before))
        self.assertGreaterEqual(stats["db_bytes"], 0)
        self.assertEqual(set(stats["by_status"]), set(REVIEW_STATUSES))
        self.store.vacuum()
        self.assertEqual(image.read_bytes(), before)

    def test_clear_index_keeps_what_the_user_marked(self):
        first = self.make_image("001_00001_.png")
        second = self.make_image("002_00001_.png")
        third = self.make_image("003_00001_.png")
        self.store.sync_run(
            "run-a",
            self.document(
                "run-a",
                [self.entry(1, "甲", first), self.entry(2, "乙", second), self.entry(3, "丙", third)],
            ),
        )
        self.store.set_favorite("run-a:0001", True)
        self.store.delete("run-a:0002")
        removed = self.store.clear_index()
        self.assertEqual(removed["removed"], 1, "只有既未收藏也未删除的那条可以清掉")
        self.assertEqual(self.store.query()["total"], 1, "被收藏的那条必须留下")
        self.assertEqual(self.store.stats()["deleted"], 1, "删除标记不能随索引一起清掉")
        self.assertEqual(self.store.stats()["runs"], 0)


class IdentityTests(unittest.TestCase):
    def test_work_ids_are_padded_and_parseable(self):
        self.assertEqual(work_id_for("run-a", 7), "run-a:0007")
        self.assertEqual(work_id_for("run-a", "12"), "run-a:0012")
        self.assertEqual(work_id_for("run-a", None), "run-a:0000")


if __name__ == "__main__":
    unittest.main()
