"""Safe, local image extraction from public web pages.

The module owns URL validation, redirect checking, page parsing, candidate
selection and image verification. Callers only need :meth:`ImageExtractor.extract`
and receive ready-to-import image bytes plus provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import http.client
import ipaddress
import io
import json
import pathlib
import re
import socket
import ssl
import time
import zlib
from typing import Any, Callable
from urllib.parse import urljoin, urlparse, urlunparse

from PIL import Image


MAX_PAGE_BYTES = 1 * 1024 * 1024
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGES = 12
MAX_CANDIDATES = 36
MAX_REQUESTS = 16
MAX_TOTAL_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_EXTRACTION_SECONDS = 45
MAX_REDIRECTS = 5
REQUEST_TIMEOUT_SECONDS = 15
SUPPORTED_IMAGE_FORMATS = {"png": ".png", "jpeg": ".jpg", "jpg": ".jpg", "webp": ".webp"}
_BILIBILI_VIDEO = re.compile(r"/video/(BV[0-9A-Za-z]+|av\d+)", re.IGNORECASE)


class ImageExtractionError(ValueError):
    """A user-facing extraction failure that leaves no imported files behind."""


class ExtractionBudgetExceeded(ImageExtractionError):
    """The whole extraction reached a request, byte or wall-clock limit."""


@dataclass
class ExtractionBudget:
    """Shared limits for one pasted URL, including redirects and failed images."""

    deadline: float
    requests: int = 0
    downloaded_bytes: int = 0

    @classmethod
    def start(cls) -> "ExtractionBudget":
        return cls(time.monotonic() + MAX_EXTRACTION_SECONDS)

    def begin_request(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ExtractionBudgetExceeded("网页图片提取超过总耗时限制，已停止")
        if self.requests >= MAX_REQUESTS:
            raise ExtractionBudgetExceeded("网页包含过多图片请求，已停止继续下载")
        self.requests += 1
        return max(0.1, min(float(REQUEST_TIMEOUT_SECONDS), remaining))

    def consume(self, amount: int) -> None:
        self.downloaded_bytes += max(0, int(amount))
        if self.downloaded_bytes > MAX_TOTAL_DOWNLOAD_BYTES:
            raise ExtractionBudgetExceeded("网页图片下载总量超过 64MB，已停止")
        if time.monotonic() > self.deadline:
            raise ExtractionBudgetExceeded("网页图片提取超过总耗时限制，已停止")


@dataclass(frozen=True)
class FetchResponse:
    """The small response shape used at the fetcher seam."""

    url: str
    content_type: str
    body: bytes


@dataclass(frozen=True)
class ExtractedImage:
    """A verified image ready for ``Application.import_images``."""

    filename: str
    raw: bytes
    source_page: str
    source_url: str
    kind: str
    title: str
    dimensions: tuple[int, int]


class _CandidateParser(HTMLParser):
    """Collect page title, social-card images and sensible inline image URLs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._inside_title = False
        self._title_parts: list[str] = []
        self.meta: list[tuple[str, str]] = []
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {str(key).lower(): str(value or "").strip() for key, value in attrs}
        if tag.lower() == "title":
            self._inside_title = True
        elif tag.lower() == "meta":
            key = values.get("property") or values.get("name") or values.get("itemprop")
            content = values.get("content", "")
            if key and content:
                self.meta.append((key.lower(), content))
        elif tag.lower() == "link" and values.get("rel", "").lower() in {"image_src", "image"}:
            if values.get("href"):
                self.images.append(values["href"])
        elif tag.lower() in {"img", "source"}:
            for name in ("data-original", "data-src", "src"):
                if values.get(name):
                    self.images.append(values[name])
                    break
            if values.get("srcset"):
                self.images.extend(
                    item.strip().split(" ", 1)[0]
                    for item in values["srcset"].split(",")
                    if item.strip()
                )

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._inside_title = False
            if not self.title:
                self.title = " ".join(self._title_parts).strip()

    def handle_data(self, data: str) -> None:
        if self._inside_title:
            self._title_parts.append(data)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """Keep the HTTP Host header while connecting only to the checked address."""

    def __init__(self, host: str, address: str, port: int, timeout: float) -> None:
        super().__init__(host, port=port, timeout=timeout)
        self._address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), self.timeout, self.source_address)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Pin TCP to the checked IP while validating TLS for the original host."""

    def __init__(self, host: str, address: str, port: int, timeout: float) -> None:
        super().__init__(host, port=port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        raw = socket.create_connection((self._address, self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _decode_gzip_bounded(chunks: list[bytes], limit: int) -> bytes:
    """Decode every gzip member under one output cap; reject trailing garbage."""
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    decoded: list[bytes] = []
    total = 0
    try:
        for chunk in chunks:
            pending = chunk
            while pending:
                if decoder is None:
                    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
                part = decoder.decompress(pending, limit + 1 - total)
                total += len(part)
                if total > limit:
                    raise ImageExtractionError("链接解压后内容超过大小限制")
                decoded.append(part)
                if decoder.eof:
                    pending = decoder.unused_data
                    decoder = None
                else:
                    pending = decoder.unconsumed_tail
        if decoder is not None:
            raise ImageExtractionError("网页压缩内容不完整")
    except zlib.error as exc:
        raise ImageExtractionError("网页压缩内容损坏") from exc
    return b"".join(decoded)


class SafeUrlFetcher:
    """Production adapter for public HTTP(S) resources.

    It deliberately disables system proxies and validates the original target
    and every redirect. This keeps a pasted link from becoming a route to a
    local ComfyUI, router or cloud metadata address.
    """

    def __init__(self, resolver: Callable[..., Any] = socket.getaddrinfo) -> None:
        self._resolver = resolver

    def validate_url(self, value: str) -> tuple[Any, tuple[str, ...]]:
        parsed = urlparse(str(value).strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ImageExtractionError("只支持公开的 http 或 https 图片链接")
        if parsed.username or parsed.password:
            raise ImageExtractionError("链接不能包含用户名或密码")
        if parsed.port not in {None, 80, 443}:
            raise ImageExtractionError("链接只能使用标准网页端口 80 或 443")
        host = parsed.hostname.rstrip(".")
        if host.lower() in {"localhost", "localhost.localdomain"}:
            raise ImageExtractionError("不能访问本机地址")
        try:
            addresses = self._resolver(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ImageExtractionError("无法解析链接所在的网站") from exc
        resolved: set[str] = set()
        for row in addresses:
            try:
                address = str(row[4][0])
                parsed_ip = ipaddress.ip_address(address)
            except (IndexError, ValueError):
                continue
            resolved.add(address)
            if not parsed_ip.is_global:
                raise ImageExtractionError("链接指向本机或私有网络，已拒绝访问")
        if not resolved:
            raise ImageExtractionError("链接未解析到可访问的公网地址")
        return parsed, tuple(sorted(resolved))

    def fetch(
        self,
        url: str,
        *,
        accept: str,
        max_bytes: int,
        budget: ExtractionBudget,
        image_max_bytes: int | None = None,
    ) -> FetchResponse:
        current = url
        for redirect_count in range(MAX_REDIRECTS + 1):
            parsed, addresses = self.validate_url(current)
            timeout = budget.begin_request()
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            connection_type = _PinnedHTTPSConnection if parsed.scheme == "https" else _PinnedHTTPConnection
            connection = connection_type(str(parsed.hostname), addresses[0], port, timeout)
            target = parsed.path or "/"
            if parsed.params:
                target += ";" + parsed.params
            if parsed.query:
                target += "?" + parsed.query
            try:
                connection.request("GET", target, headers={
                    "Accept": accept,
                    "User-Agent": "ComfyBatch/2.21 image-extractor",
                    "Connection": "close",
                })
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = str(response.getheader("Location") or "").strip()
                    if not location:
                        raise ImageExtractionError("网站返回了无目标的重定向")
                    if redirect_count >= MAX_REDIRECTS:
                        raise ImageExtractionError("网页重定向次数过多，已停止")
                    current = urljoin(current, location)
                    continue
                if response.status >= 400:
                    raise ImageExtractionError(f"网站返回 HTTP {response.status}，无法读取")
                content_type = str(response.getheader("Content-Type") or "").split(";", 1)[0].lower()
                effective_max = (
                    int(image_max_bytes)
                    if image_max_bytes is not None and content_type.startswith("image/")
                    else max_bytes
                )
                declared = response.getheader("Content-Length")
                if declared and int(declared) > effective_max:
                    raise ImageExtractionError(f"链接内容超过 {effective_max // (1024 * 1024)}MB 限制")
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(min(64 * 1024, effective_max + 1 - total))
                    if not chunk:
                        break
                    budget.consume(len(chunk))
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > effective_max:
                        raise ImageExtractionError(f"链接内容超过 {effective_max // (1024 * 1024)}MB 限制")
                encoding = str(response.getheader("Content-Encoding") or "").strip().lower()
                if encoding == "gzip":
                    body = _decode_gzip_bounded(chunks, effective_max)
                elif encoding in {"", "identity"}:
                    body = b"".join(chunks)
                else:
                    raise ImageExtractionError("网站使用不支持的内容压缩格式")
                return FetchResponse(current, content_type, body)
            except (ExtractionBudgetExceeded, ImageExtractionError):
                raise
            except (OSError, http.client.HTTPException, ssl.SSLError, ValueError) as exc:
                raise ImageExtractionError("无法读取该链接，请确认网站可访问且未限制访问") from exc
            finally:
                connection.close()
        raise ImageExtractionError("网页重定向次数过多，已停止")


class ImageExtractor:
    """Extract cover and page images behind one compact interface.

    ``fetcher`` is an internal seam: production uses :class:`SafeUrlFetcher`,
    while tests use a scripted adapter. All discovery and verification remains
    inside this deep module.
    """

    def __init__(self, fetcher: Any | None = None, *, max_images: int = MAX_IMAGES) -> None:
        self.fetcher = fetcher or SafeUrlFetcher()
        self.max_images = max(1, min(int(max_images), MAX_IMAGES))

    def extract(self, url: str) -> list[ExtractedImage]:
        budget = ExtractionBudget.start()
        page_url = self._normalise_url(url)
        bilibili = self._bilibili_candidates(page_url, budget)
        if bilibili:
            images = self._download_candidates(page_url, bilibili, budget)
            if images:
                return images

        page = self.fetcher.fetch(
            page_url,
            accept="text/html,application/xhtml+xml,image/avif,image/webp,image/*;q=0.8,*/*;q=0.5",
            max_bytes=MAX_PAGE_BYTES,
            budget=budget,
            image_max_bytes=MAX_IMAGE_BYTES,
        )
        if page.content_type.startswith("image/"):
            return [self._verified_image(page, page_url, page.url, "direct-image", "")]
        candidates, title = self._page_candidates(page)
        images = self._download_candidates(page.url, candidates, budget, title=title)
        if images:
            return images
        raise ImageExtractionError("没有找到可读取的封面或图片；该网站可能限制访问或未提供公开图片")

    @staticmethod
    def _normalise_url(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            raise ImageExtractionError("请先粘贴网页或图片链接")
        parsed = urlparse(raw)
        if not parsed.scheme:
            raw = "https://" + raw
            parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ImageExtractionError("请输入完整的 http 或 https 链接")
        return urlunparse(parsed._replace(fragment=""))

    def _bilibili_candidates(self, page_url: str, budget: ExtractionBudget) -> list[tuple[str, str, str]]:
        host = (urlparse(page_url).hostname or "").lower()
        if not (host == "bilibili.com" or host.endswith(".bilibili.com")):
            return []
        match = _BILIBILI_VIDEO.search(urlparse(page_url).path)
        if not match:
            return []
        token = match.group(1)
        query = "bvid=" + token if token.upper().startswith("BV") else "aid=" + token[2:]
        endpoint = "https://api.bilibili.com/x/web-interface/view?" + query
        try:
            response = self.fetcher.fetch(endpoint, accept="application/json", max_bytes=MAX_PAGE_BYTES, budget=budget)
            payload = json.loads(response.body.decode("utf-8"))
            data = payload.get("data") if isinstance(payload, dict) else None
            cover = str(data.get("pic") or "") if isinstance(data, dict) else ""
            title = str(data.get("title") or "") if isinstance(data, dict) else ""
            return [(cover, "bilibili-cover", title)] if cover else []
        except (ImageExtractionError, UnicodeDecodeError, json.JSONDecodeError):
            return []

    def _page_candidates(self, page: FetchResponse) -> tuple[list[tuple[str, str, str]], str]:
        try:
            text = page.body.decode("utf-8")
        except UnicodeDecodeError:
            text = page.body.decode("utf-8", errors="replace")
        parser = _CandidateParser()
        parser.feed(text)
        parser.close()
        title = next((value for key, value in parser.meta if key in {"og:title", "twitter:title"}), parser.title)
        ordered: list[tuple[str, str, str]] = []
        for keys, kind in (
            ({"og:image", "og:image:url"}, "open-graph"),
            ({"twitter:image", "twitter:image:src"}, "twitter-card"),
        ):
            for key, value in parser.meta:
                if key in keys:
                    ordered.append((value, kind, title))
        ordered.extend((value, "page-image", title) for value in parser.images)
        unique: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for value, kind, candidate_title in ordered:
            absolute = urljoin(page.url, value.strip())
            parsed = urlparse(absolute)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
            key = urlunparse(parsed._replace(fragment=""))
            if key not in seen:
                seen.add(key)
                unique.append((key, kind, candidate_title))
                if len(unique) >= MAX_CANDIDATES:
                    break
        return unique, title

    def _download_candidates(
        self,
        page_url: str,
        candidates: list[tuple[str, str, str]],
        budget: ExtractionBudget,
        *,
        title: str = "",
    ) -> list[ExtractedImage]:
        result: list[ExtractedImage] = []
        for source_url, kind, candidate_title in candidates:
            if len(result) >= self.max_images:
                break
            try:
                response = self.fetcher.fetch(
                    source_url, accept="image/avif,image/webp,image/*,*/*;q=0.7",
                    max_bytes=MAX_IMAGE_BYTES, budget=budget,
                )
                image = self._verified_image(response, page_url, source_url, kind, candidate_title or title)
            except ExtractionBudgetExceeded:
                if result:
                    break
                raise
            except ImageExtractionError:
                continue
            if not any(item.raw == image.raw for item in result):
                result.append(image)
        return result

    @staticmethod
    def _verified_image(response: FetchResponse, page_url: str, source_url: str, kind: str, title: str) -> ExtractedImage:
        if response.content_type and not response.content_type.startswith("image/"):
            raise ImageExtractionError("候选链接不是图片")
        try:
            with Image.open(io.BytesIO(response.body)) as image:
                width, height = image.size
                if width * height > MAX_IMAGE_PIXELS:
                    raise ImageExtractionError("候选图片像素过高，已拒绝导入")
                image.verify()
            with Image.open(io.BytesIO(response.body)) as image:
                image_format = str(image.format or "").lower()
        except Exception as exc:  # Pillow raises several decoder-specific errors.
            raise ImageExtractionError("候选链接返回的内容不是可读取图片") from exc
        if width < 32 or height < 32:
            raise ImageExtractionError("候选图片尺寸过小")
        suffix = ImageExtractor._suffix_for(image_format)
        filename = ImageExtractor._filename(response.url, suffix)
        return ExtractedImage(filename, response.body, page_url, response.url, kind, title.strip(), (width, height))

    @staticmethod
    def _suffix_for(image_format: str) -> str:
        try:
            return SUPPORTED_IMAGE_FORMATS[image_format]
        except KeyError as exc:
            raise ImageExtractionError("仅支持 PNG、JPEG 和 WebP 图片") from exc

    @staticmethod
    def _filename(url: str, suffix: str) -> str:
        stem = pathlib.PurePosixPath(urlparse(url).path).stem or "extracted-image"
        stem = re.sub(r"[^0-9A-Za-z._-]+", "-", stem).strip(".-") or "extracted-image"
        return stem[:80] + suffix
