"""Tests for public-page image extraction without making network requests."""

from __future__ import annotations

import json
import io
import pathlib
import socket
import tempfile
import unittest
from unittest import mock

from PIL import Image

from fakes import make_comfy_tree, png_bytes

import comfybatch_image_extract as image_module
from comfybatch_image_extract import (
    MAX_CANDIDATES,
    MAX_REQUESTS,
    ExtractionBudget,
    ExtractionBudgetExceeded,
    ExtractedImage,
    FetchResponse,
    ImageExtractor,
    ImageExtractionError,
    SafeUrlFetcher,
)
from comfybatch_v2_app import Application, WRITE_ROUTES


class ScriptedFetcher:
    """A local adapter for the fetcher seam; it cannot reach the network."""

    def __init__(self, responses: dict[str, FetchResponse]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def fetch(
        self, url: str, *, accept: str, max_bytes: int, budget: ExtractionBudget,
        image_max_bytes: int | None = None,
    ) -> FetchResponse:
        budget.begin_request()
        self.calls.append(url)
        try:
            response = self.responses[url]
            effective_max = image_max_bytes if image_max_bytes and response.content_type.startswith("image/") else max_bytes
            if len(response.body) > effective_max:
                raise ImageExtractionError("链接内容超过限制")
            budget.consume(len(response.body))
            return response
        except KeyError as exc:
            raise ImageExtractionError(f"unexpected URL: {url}") from exc


class ImageExtractionTests(unittest.TestCase):
    def test_prefers_open_graph_cover_then_falls_back_to_page_images(self):
        page = "https://example.test/articles/look"
        cover = "https://cdn.example.test/cover.jpg"
        inline = "https://example.test/images/detail.png"
        fetcher = ScriptedFetcher({
            page: FetchResponse(page, "text/html", b'''<!doctype html><html><head>
              <title>Page title</title><meta property="og:title" content="Illustration reference">
              <meta property="og:image" content="https://cdn.example.test/cover.jpg"></head>
              <body><img data-src="/images/detail.png"></body></html>'''),
            cover: FetchResponse(cover, "image/jpeg", png_bytes((640, 360))),
            inline: FetchResponse(inline, "image/png", png_bytes((320, 480))),
        })

        images = ImageExtractor(fetcher).extract(page)

        self.assertEqual(2, len(images))
        self.assertEqual("open-graph", images[0].kind)
        self.assertEqual("Illustration reference", images[0].title)
        self.assertEqual((640, 360), images[0].dimensions)
        self.assertEqual("page-image", images[1].kind)

    def test_bilibili_video_uses_the_cover_metadata_first(self):
        page = "https://www.bilibili.com/video/BV1xx411c7mD"
        endpoint = "https://api.bilibili.com/x/web-interface/view?bvid=BV1xx411c7mD"
        cover = "https://i0.hdslb.com/bfs/archive/cover.jpg"
        fetcher = ScriptedFetcher({
            endpoint: FetchResponse(endpoint, "application/json", json.dumps({
                "code": 0, "data": {"title": "B 站测试封面", "pic": cover},
            }).encode("utf-8")),
            cover: FetchResponse(cover, "image/jpeg", png_bytes((1280, 720))),
        })

        images = ImageExtractor(fetcher).extract(page)

        self.assertEqual(1, len(images))
        self.assertEqual("bilibili-cover", images[0].kind)
        self.assertEqual("B 站测试封面", images[0].title)
        self.assertEqual([endpoint, cover], fetcher.calls)

    def test_direct_image_uses_the_image_limit_instead_of_the_page_limit(self):
        url = "https://cdn.example.test/large.png"
        raw = png_bytes((64, 64)) + b"\0" * (1024 * 1024)
        images = ImageExtractor(ScriptedFetcher({url: FetchResponse(url, "image/png", raw)})).extract(url)

        self.assertEqual(1, len(images))
        self.assertGreater(len(images[0].raw), 1024 * 1024)

    def test_actual_image_format_controls_the_saved_extension(self):
        url = "https://cdn.example.test/misleading.jpg"
        images = ImageExtractor(ScriptedFetcher({
            url: FetchResponse(url, "image/jpeg", png_bytes((64, 64))),
        })).extract(url)

        self.assertTrue(images[0].filename.endswith(".png"))

    def test_rejects_decodable_but_unsupported_image_formats(self):
        url = "https://cdn.example.test/fixture.bmp"
        output = io.BytesIO()
        Image.new("RGB", (64, 64), "white").save(output, format="BMP")

        with self.assertRaisesRegex(ImageExtractionError, "PNG"):
            ImageExtractor(ScriptedFetcher({
                url: FetchResponse(url, "image/bmp", output.getvalue()),
            })).extract(url)

    def test_private_network_targets_are_rejected_before_fetch(self):
        resolver = lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
        ]
        fetcher = SafeUrlFetcher(resolver=resolver)

        with self.assertRaisesRegex(ImageExtractionError, "私有网络"):
            fetcher.validate_url("http://private.example.test")

    def test_page_candidate_parsing_is_bounded(self):
        page = "https://example.test/gallery"
        html = "".join(f'<img src="/images/{index}.png">' for index in range(MAX_CANDIDATES + 20))
        fetcher = ScriptedFetcher({page: FetchResponse(page, "text/html", html.encode())})
        extractor = ImageExtractor(fetcher)

        response = fetcher.fetch(page, accept="text/html", max_bytes=1024 * 1024, budget=ExtractionBudget.start())
        candidates, _title = extractor._page_candidates(response)

        self.assertEqual(MAX_CANDIDATES, len(candidates))

    def test_failed_candidate_requests_stop_at_the_global_request_limit(self):
        page = "https://example.test/gallery"
        urls = [f"https://example.test/images/{index}.png" for index in range(MAX_CANDIDATES)]
        html = "".join(f'<img src="{url}">' for url in urls)
        fetcher = ScriptedFetcher({page: FetchResponse(page, "text/html", html.encode())})

        with self.assertRaisesRegex(ExtractionBudgetExceeded, "过多图片请求"):
            ImageExtractor(fetcher).extract(page)

        self.assertEqual(MAX_REQUESTS, len(fetcher.calls))

    def test_https_connection_is_pinned_to_the_single_validated_address(self):
        resolver_calls = []
        connection_calls = []
        raw = png_bytes((64, 64))

        def resolver(host, port, **_kwargs):
            resolver_calls.append((host, port))
            if len(resolver_calls) > 1:
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

        class Response:
            status = 200

            def __init__(self):
                self.remaining = raw

            def getheader(self, name):
                return {"Content-Type": "image/png", "Content-Length": str(len(raw))}.get(name)

            def read(self, _amount):
                chunk, self.remaining = self.remaining, b""
                return chunk

        class Connection:
            def __init__(self, host, address, port, timeout):
                connection_calls.append((host, address, port, timeout))

            def request(self, method, target, headers):
                connection_calls.append((method, target, headers["Host"] if "Host" in headers else None))

            def getresponse(self):
                return Response()

            def close(self):
                pass

        fetcher = SafeUrlFetcher(resolver=resolver)
        with mock.patch.object(image_module, "_PinnedHTTPSConnection", Connection):
            response = fetcher.fetch(
                "https://example.com/image.png", accept="image/*", max_bytes=1024 * 1024,
                budget=ExtractionBudget.start(),
            )

        self.assertEqual(raw, response.body)
        self.assertEqual([("example.com", 443)], resolver_calls)
        self.assertEqual(("example.com", "93.184.216.34", 443), connection_calls[0][:3])

    def test_redirect_target_is_revalidated_before_any_second_connection(self):
        resolver_calls = []
        connections = []

        def resolver(host, port, **_kwargs):
            resolver_calls.append((host, port))
            address = "93.184.216.34" if host == "example.com" else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

        class RedirectResponse:
            status = 302

            @staticmethod
            def getheader(name):
                return "http://private.example.test/image.png" if name == "Location" else None

        class Connection:
            def __init__(self, host, address, port, timeout):
                connections.append((host, address, port, timeout))

            def request(self, method, target, headers):
                pass

            def getresponse(self):
                return RedirectResponse()

            def close(self):
                pass

        fetcher = SafeUrlFetcher(resolver=resolver)
        with mock.patch.object(image_module, "_PinnedHTTPSConnection", Connection):
            with self.assertRaisesRegex(ImageExtractionError, "私有网络"):
                fetcher.fetch(
                    "https://example.com/start", accept="image/*", max_bytes=1024 * 1024,
                    budget=ExtractionBudget.start(),
                )

        self.assertEqual([("example.com", 443), ("private.example.test", 80)], resolver_calls)
        self.assertEqual(1, len(connections))

    def test_application_stages_extracted_images_through_the_normal_image_path(self):
        class Extractor:
            def extract(self, url: str):
                return [ExtractedImage(
                    filename="cover.jpg", raw=png_bytes((640, 360)), source_page=url,
                    source_url="https://cdn.example.test/cover.jpg", kind="open-graph",
                    title="网页封面", dimensions=(640, 360),
                )]

        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            app = Application(settings_path=root / "settings.json", backup_settings_path=None)
            app.comfy_root = make_comfy_tree(root)
            app.image_extractor = Extractor()

            bundle, extracted = app.extract_images("https://example.test/page", "outpaint")

            self.assertEqual(1, len(bundle.items))
            item = bundle.items[0]
            self.assertEqual("网页封面", item.title)
            self.assertEqual("outpaint", item.metadata["processing_mode"])
            self.assertEqual("open-graph", item.metadata["extraction"]["kind"])
            self.assertTrue((app.comfy_root / "input" / item.metadata["source_image"]).is_file())
            self.assertEqual("https://cdn.example.test/cover.jpg", extracted[0]["source_url"])

    def test_the_page_exposes_a_leased_write_route_for_link_extraction(self):
        source = (pathlib.Path(__file__).resolve().parents[1] / "src" / "app.js").read_text(encoding="utf-8")
        self.assertIn("/api/extract-images", WRITE_ROUTES)
        self.assertIn("/api/extract-images", source)
        self.assertIn("extractImageUrl", source)


if __name__ == "__main__":
    unittest.main()
