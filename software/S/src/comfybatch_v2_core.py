from __future__ import annotations

import copy
import csv
import datetime
import hashlib
import io
import json
import math
import pathlib
import re
import shutil
import struct
import threading
import time
import unicodedata
import urllib.request
import uuid
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from typing import Any

from PIL import Image, ImageChops, ImageStat

from comfybatch_params import apply_params, registry_payload
from comfybatch_shared import FileLock, atomic_write_text, unique_temp_for, with_file_lock
from comfybatch_errors import (
    ErrorCategory,
    ComfyError,
    ErrorTranslator,
    Problem,
    summarise as summarise_problems,
)
from comfybatch_gateway import ProductionGateway
from comfybatch_nodeschema import (
    SEVERITY_BLOCKING,
    structural_fingerprint,
    apply_seed,
    executed_graph,
    graph_seeds,
    seed_plan,
    seed_report,
    SOURCE_INJECTED,
    CompiledGraphAudit,
    NodeSchemaRegistry,
    active_registry,
    convert_node,
)


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MODEL_EXTENSIONS = {".safetensors", ".ckpt", ".pt", ".pth", ".bin"}


class WorkflowFailure(RuntimeError):
    """A failure reported by ComfyUI that already carries structured Problems.

    Distinguished from generic exceptions so the batch loop can decide, from
    ``Problem.workflow_level``, whether the remaining items are worth submitting
    at all.
    """

    def __init__(self, problems: list[Problem], *, prompt_id: str = ""):
        self.problems = list(problems)
        self.prompt_id = prompt_id
        super().__init__(summarise_problems(self.problems) or "工作流执行失败")


def resolve_image_dimensions(aspect_ratio: str, megapixels: float, multiple: int = 8) -> tuple[int, int]:
    """Resolve the UI aspect/MP selection to concrete ComfyUI pixel dimensions."""
    match = re.search(r"(\d+)\s*:\s*(\d+)", str(aspect_ratio or ""))
    ratio_width, ratio_height = (int(match.group(1)), int(match.group(2))) if match else (1, 1)
    known = {
        (16, 9, 0.6): (1024, 576),
        (16, 9, 0.9): (1280, 720),
        (16, 9, 1.3): (1536, 864),
        (3, 2, 1.0): (1200, 800),
        (4, 3, 1.0): (1152, 864),
        (1, 1, 1.0): (1024, 1024),
        (9, 16, 0.6): (576, 1024),
        (9, 16, 0.9): (720, 1280),
        (9, 16, 1.3): (864, 1536),
    }
    preset = known.get((ratio_width, ratio_height, round(float(megapixels), 1)))
    if preset:
        return preset
    total_pixels = max(0.25, float(megapixels)) * 1_000_000
    width = math.sqrt(total_pixels * ratio_width / ratio_height)
    height = width * ratio_height / ratio_width
    snap = max(1, int(multiple))
    return max(snap, round(width / snap) * snap), max(snap, round(height / snap) * snap)


def _safe_name(value: str, fallback: str = "item", max_length: int = 80) -> str:
    """Return a bounded Windows-safe path component."""
    cleaned = unicodedata.normalize("NFKC", str(value or ""))
    cleaned = "".join("_" if unicodedata.category(char).startswith("C") else char for char in cleaned)
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip(" ._")
    cleaned = cleaned[:max_length].rstrip(" ._")
    if cleaned.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        cleaned = "_" + cleaned
    return cleaned or fallback


def _norm_parts(value: Any) -> list[str]:
    """Split a ComfyUI-reported path fragment into comparable components."""
    text = str(value or "").replace("\\", "/").strip("/")
    return [part for part in text.split("/") if part and part != "."]


def resolve_output_source(output_root: pathlib.Path, image: dict[str, Any]) -> pathlib.Path:
    """Absolute path of a ComfyUI output file, tolerating both report shapes.

    Why this cannot be a plain join
    -------------------------------
    ComfyUI reports ``subfolder`` and ``filename`` through two different code
    paths, and they disagree about who owns the folder:

    * the websocket/``/history`` shape reports ``subfolder="a/b"`` with
      ``filename="001_x_00001_.png"``;
    * the ``/view``-orientated shape reports ``subfolder=""`` with
      ``filename="a/b/001_x_00001_.png"`` -- the folder repeated inside the name.

    Blindly joining the two therefore produced
    ``output/a/b/a/b/001_x_00001_.png``, which does not exist. Every item then
    died in the copy step with ``[WinError 3] 系统找不到指定的路径``, and because
    that is a plain OSError rather than a workflow-level ``Problem`` it did not
    stop the batch -- it merely failed each image, so a finished run could end up
    with nothing saved. Reconciling the two shapes here fixes it at the source.
    """
    subfolder = _norm_parts(image.get("subfolder"))
    filename = _norm_parts(image.get("filename"))
    if not filename:
        raise ValueError("ComfyUI 没有报告输出文件名")
    # ``filename`` wins when it already carries the folder, because it is
    # relative to the output root either way.
    if subfolder and filename[: len(subfolder)] == subfolder:
        combined = filename
    else:
        combined = subfolder + filename
    path = pathlib.Path(output_root).joinpath(*combined)
    if path.exists():
        return path
    # A shape we have not seen: try the literal join before giving up, so the
    # caller still gets a precise "missing" error instead of a silently wrong hit.
    literal = pathlib.Path(output_root).joinpath(*(_norm_parts(image.get("subfolder")) + filename))
    return literal if literal.exists() else path


def copy_output_image(
    source: pathlib.Path,
    output_dir: pathlib.Path,
    image: dict[str, Any],
    *,
    exist_ok: bool = False,
    keep_structure: bool = True,
) -> pathlib.Path:
    """Copy a finished image into the batch output folder, without self-overwrite.

    Two defects lived in the old inline ``shutil.copy2(source, target)``:

    * ``target`` was ``output_dir / filename``. When the workflow's
      ``filename_prefix`` contains a slash -- ComfyUI resolves it relative to the
      output root, so ``ComfyBatch-V2/<run>/<title>`` means the file really is in
      that subfolder -- the app re-created the same tree inside the user's chosen
      folder, or, if the user pointed the output folder *inside* ComfyUI's output
      root, computed ``source == target`` and silently copied a file onto itself
      (``SameFileError``, no bytes transferred).
    * Redos had no collision handling at all in the batch path, so two items that
      resolved to the same name overwrote one another.

    Layout
    ------
    ``keep_structure=True`` (the default) preserves **the batch's own subfolder**
    under ``output_dir``: the prefix ``ComfyBatch-V2/<run>/`` is what makes a
    batch's images a set rather than a heap, so ``001-…`` from run A and ``001-…``
    from run B stay in separate folders instead of colliding on the same name.

    The leading ``ComfyBatch-V2`` tier is still not recreated verbatim -- it is
    dropped so the user's folder holds ``<run>/001-x.png`` rather than
    ``ComfyBatch-V2/<run>/001-x.png``. That tier belongs to ComfyUI's output root,
    not to the collection the user is building. Pass ``keep_structure=False`` for
    the old flatten-to-bare-name behaviour.

    Self-overwrite is still detected after the layout is computed, so pointing
    ``output_dir`` inside ComfyUI's output root remains a no-op.

    Collisions pick ``-v2``, ``-v3`` instead of clobbering.
    """
    output_dir = pathlib.Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    parts = _output_relative_parts(image, keep_structure=keep_structure)
    target = output_dir.joinpath(*parts) if parts else output_dir / source.name
    if not target.name:
        target = output_dir / source.name
    if target.parent != output_dir:
        target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if source.resolve() == target.resolve():
            # Already where it belongs; copying would raise SameFileError.
            return target
    except OSError:
        pass
    if exist_ok:
        target = _deduplicate_target(target)
    shutil.copy2(source, target)
    return target


#: The top tier ComfyUI's own ``filename_prefix`` adds. It names the app, not the
#: batch, so it is dropped when mirroring the prefix's structure into a user's
#: output folder -- see ``copy_output_image``.
_OUTPUT_PREFIX_ROOT = "ComfyBatch-V2"


def _output_relative_parts(image: dict[str, Any], *, keep_structure: bool) -> list[str]:
    """Where an image lands, relative to the user's output folder.

    ComfyUI reports the same file in two shapes (see
    ``resolve_output_source``): the folder may sit in ``subfolder``, or be
    repeated inside ``filename``. Both are normalised to one segment list first,
    so the layout does not depend on which shape the server happened to use.
    """
    filename = _norm_parts(image.get("filename"))
    if not filename:
        return []
    if not keep_structure:
        return [filename[-1]]
    subfolder = _norm_parts(image.get("subfolder"))
    combined = filename if (subfolder and filename[: len(subfolder)] == subfolder) else subfolder + filename
    # Drop the app-named tier, and any empty remainder, so the folder becomes
    # ``<run>/001-x.png`` rather than ``ComfyBatch-V2/<run>/001-x.png``.
    if combined and combined[0] == _OUTPUT_PREFIX_ROOT and len(combined) > 1:
        combined = combined[1:]
    return combined


def _deduplicate_target(target: pathlib.Path) -> pathlib.Path:
    """``name.png`` -> ``name-v2.png`` -> ``name-v3.png`` until one is free."""
    stem, suffix = target.stem, target.suffix
    counter = 2
    directory = target.parent
    while target.exists():
        target = directory / f"{stem}-v{counter}{suffix}"
        counter += 1
    return target


@dataclass
class PromptItem:
    title: str
    prompt: str
    metadata: dict[str, Any] = field(default_factory=dict)
    negative_prompt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PromptBundle:
    name: str
    items: list[PromptItem]
    source_format: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "source_format": self.source_format, "count": len(self.items), "items": [item.to_dict() for item in self.items]}


@dataclass
class BatchConfig:
    workflow_path: str
    model: str
    style_library: str = ""
    style_name: str = ""
    styles: list[dict[str, str]] = field(default_factory=list)
    loras: list[dict[str, Any]] = field(default_factory=list)
    aspect_ratio: str = "16:9 (Widescreen)"
    megapixels: float = 1.2
    output_dir: str = ""
    llm: dict[str, Any] = field(default_factory=dict)
    single_subject_guard: bool = False
    max_retries: int = 0
    negative_prompt: str = ""
    workflow_variant: str = ""
    style_application: str = "prompt"
    #: Free-form workflow parameter overrides, keyed by the plan's parameter
    #: names (``upscale_model``, ``upscale_by``, ``latent_upscale_by``, ...).
    #: Consumed by the declarative converters, so exposing a new knob is a
    #: one-line change there rather than a change to the compile loop.
    params: dict[str, Any] = field(default_factory=dict)
    #: Circuit breaker: stop the batch after this many consecutive task-level
    #: failures. Workflow-level failures stop immediately regardless.
    max_consecutive_failures: int = 3
    #: Run one item, then pause for confirmation before releasing the rest.
    #: Kept for compatibility: a value of ``True`` now means "one item per
    #: segment", which is the same promise this field always advertised.
    trial_first: bool = False
    #: Split the batch into segments of this many items and stop for
    #: confirmation at every boundary. ``0`` submits the whole batch in one go,
    #: which is what every version before V2.21 did. The queue still owns the
    #: slot while it waits, so nothing else can start in the gap.
    segment_size: int = 0
    #: User-chosen resource replacements for this batch, keyed by
    #: ``{node_id: {input_name: value}}``. Never applied silently: the audit
    #: tags these inputs as ``user_replaced``.
    resource_overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    #: Explicit seed to pin for this batch, when the user asks for one.
    seed: int | None = None
    #: What this batch is for, one of :data:`comfybatch_nodeschema.PURPOSE_IDS`.
    #: Empty means "no assertion": preflight checks the workflow as before and
    #: never refuses it for its purpose. A value that is not a known purpose is
    #: kept as-is rather than dropped, so a typo is refused instead of quietly
    #: turning into "no assertion".
    purpose: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "BatchConfig":
        segment_size = cls.parse_segment_size(value)
        return cls(
            workflow_path=str(value.get("workflow_path", "")),
            model=str(value.get("model", "")),
            workflow_variant=str(value.get("workflow_variant") or ""),
            style_application=str(value.get("style_application") or "native"),
            style_library=str(value.get("style_library", "")),
            style_name=str(value.get("style_name", "")),
            styles=list(value.get("styles") or []),
            loras=list(value.get("loras") or []),
            aspect_ratio=str(value.get("aspect_ratio") or "16:9 (Widescreen)"),
            megapixels=max(0.25, min(4.0, float(value.get("megapixels") or 1.2))),
            output_dir=str(value.get("output_dir") or ""),
            llm=dict(value.get("llm") or {}),
            single_subject_guard=bool(value.get("single_subject_guard", True)),
            max_retries=max(0, min(5, int(value.get("max_retries", 2)))),
            negative_prompt=str(value.get("negative_prompt") or "").strip(),
            params=dict(value.get("params") or {}),
            max_consecutive_failures=max(1, min(50, int(value.get("max_consecutive_failures", 3)))),
            trial_first=bool(value.get("trial_first", False)),
            resource_overrides=dict(value.get("resource_overrides") or {}),
            segment_size=segment_size,
            seed=int(value["seed"]) if value.get("seed") not in (None, "") else None,
            # Kept verbatim: an unrecognised purpose must reach preflight and be
            # refused there, not be normalised away into "no assertion".
            purpose=str(value.get("purpose") or "").strip(),
        )

    @staticmethod
    def parse_segment_size(value: dict[str, Any]) -> int:
        """Read ``segment_size``, falling back to the older ``trial_first`` flag.

        ``trial_first`` was declared long ago and never read by anything, so
        honouring it here is what finally makes it mean what its comment says.
        An unusable value raises with the offending input in the message: a
        silently clamped segment size would look like it worked and then stop
        the batch somewhere the user did not ask for.
        """
        raw = value.get("segment_size")
        if raw in (None, ""):
            return 1 if value.get("trial_first") else 0
        try:
            size = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"每段条数必须是整数，收到 {raw!r}") from exc
        if size < 0:
            raise ValueError(f"每段条数不能为负数，收到 {size}")
        # 1000 is far above any batch this tool has ever been given; the ceiling
        # exists so a typo cannot ask for a segment longer than the whole run.
        return min(1000, size)


class PromptBundleParser:
    """Normalises prompt collections and documents behind one interface."""

    COLLECTION_KEYS = ("items", "prompts", "tasks", "records", "entries", "data", "outputs", "results")
    PROMPT_KEYS = ("positive_prompt", "prompt", "positive", "text", "description", "content")
    NEGATIVE_KEYS = ("negative_prompt", "negative", "negativeprompt", "负面提示词", "反向提示词", "负向提示词")
    XLSX_TITLE_HEADERS = {
        "标题", "名称", "任务名", "任务名称", "页面标题", "图片标题", "作品标题", "方案名称",
        "title", "name", "taskname", "pagetitle", "imagetitle",
    }
    XLSX_POSITIVE_HEADERS = {
        "提示词", "完整提示词", "提示词内容", "正向提示词", "正面提示词", "正向prompt", "正面prompt",
        "独立正向提示词", "独立正面提示词", "独立正向prompt", "独立正面prompt",
        "完整正向提示词", "完整正面提示词", "完整正向prompt", "完整正面prompt",
        "生图提示词", "图像提示词", "绘图提示词", "prompt", "positive", "positiveprompt", "fullprompt",
    }
    XLSX_NEGATIVE_HEADERS = {
        "负面提示词", "负向提示词", "反向提示词", "负面prompt", "负向prompt", "反向prompt",
        "独立负面提示词", "独立负向提示词", "独立反向提示词", "独立负面prompt", "独立负向prompt", "独立反向prompt",
        "完整负面提示词", "完整负向提示词", "完整反向提示词", "完整负面prompt", "完整负向prompt", "完整反向prompt",
        "排除词", "禁止词", "negative", "negativeprompt",
    }
    NUMBERED_HEADING = re.compile(
        r"^\s*(?:#{1,6}\s*)?(?:第\s*)?\d{1,4}(?:\s*[套条款])?\s*[.、:：)）\-]\s*(.+?)\s*$"
    )
    STANDALONE_NUMBER = re.compile(r"^\s*(?:第\s*)?\d{1,4}(?:\s*[套条款])?\s*[.、)）\-]?\s*$")
    ASPECT_RATIO_PREFIX = re.compile(r"^\s*\d{1,2}\s*:\s*\d{1,2}(?:\s|横|竖|$)")

    @classmethod
    def parse(cls, filename: str, raw: bytes, mapping: dict[str, Any] | None = None) -> PromptBundle:
        suffix = pathlib.Path(filename).suffix.lower()
        if suffix == ".json":
            text = raw.decode("utf-8-sig")
            payload = json.loads(text)
            values = cls._json_values(payload)
            items = [cls._from_value(value, index) for index, value in enumerate(values, 1)]
            return cls._finish(filename, items, "json")
        if suffix == ".csv":
            text = raw.decode("utf-8-sig")
            rows = list(csv.DictReader(io.StringIO(text)))
            items = [cls._from_value(row, index) for index, row in enumerate(rows, 1)]
            return cls._finish(filename, items, "csv")
        if suffix in {".txt", ".md"}:
            text = raw.decode("utf-8-sig")
            items = cls._parse_text(text)
            return cls._finish(filename, items, suffix.lstrip("."))
        if suffix == ".docx":
            items = cls._parse_text(cls._docx_text(raw))
            return cls._finish(filename, items, "docx")
        if suffix == ".xlsx":
            items = cls._xlsx_items(raw, mapping)
            return cls._finish(filename, items, "xlsx")
        raise ValueError("支持的提示词合集格式：JSON、CSV、TXT、MD、DOCX、XLSX")

    @classmethod
    def _json_values(cls, payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return [payload] if isinstance(payload, str) else []
        for key in cls.COLLECTION_KEYS:
            if key in payload:
                nested = cls._json_values(payload[key])
                if nested:
                    return nested
        if cls._prompt_text(payload):
            return [payload]
        found: list[Any] = []
        for value in payload.values():
            if isinstance(value, (dict, list)):
                found.extend(cls._json_values(value))
        return found

    @classmethod
    def _prompt_text(cls, value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if not isinstance(value, dict):
            return ""
        lowered = {str(key).lower(): item for key, item in value.items()}
        for key in cls.PROMPT_KEYS:
            item = lowered.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
            if isinstance(item, dict):
                nested = cls._prompt_text(item)
                if nested:
                    return nested
        return ""

    @classmethod
    def _negative_text(cls, value: Any) -> str:
        if not isinstance(value, dict):
            return ""
        lowered = {str(key).strip().lower(): item for key, item in value.items()}
        for key in cls.NEGATIVE_KEYS:
            item = lowered.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
        for item in value.values():
            if isinstance(item, dict):
                nested = cls._negative_text(item)
                if nested:
                    return nested
        return ""

    @classmethod
    def _parse_text(cls, text: str) -> list[PromptItem]:
        lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
        standalone_count = sum(bool(cls.STANDALONE_NUMBER.match(line)) for line in lines if line.strip())
        if standalone_count >= 2:
            groups: list[list[str]] = []
            body: list[str] = []
            started = False
            for line in lines:
                if cls.STANDALONE_NUMBER.match(line):
                    if started and body:
                        groups.append(body)
                    body = []
                    started = True
                elif started and line.strip() and not line.lstrip().startswith("#"):
                    body.append(line.strip())
            if body:
                groups.append(body)
            return [
                PromptItem(f"提示词 {index:03d}", "。".join(group).strip())
                for index, group in enumerate(groups, 1)
                if "。".join(group).strip()
            ]

        groups: list[tuple[str, list[str]]] = []
        title = ""
        body: list[str] = []
        found_numbering = False
        for line in lines:
            match = None if cls.ASPECT_RATIO_PREFIX.match(line) else cls.NUMBERED_HEADING.match(line)
            if match:
                found_numbering = True
                if title or any(item.strip() for item in body):
                    groups.append((title, body))
                title, body = match.group(1).strip(), []
            elif line.strip() and not line.lstrip().startswith("#"):
                body.append(line.strip())
        if found_numbering:
            if title or body:
                groups.append((title, body))
            return [
                PromptItem(group_title or f"提示词 {index:03d}", "。".join(group_body).strip())
                for index, (group_title, group_body) in enumerate(groups, 1)
                if "。".join(group_body).strip()
            ]

        blocks = [block.strip() for block in re.split(r"\n\s*\n+", text) if block.strip()]
        if len(blocks) > 1:
            prompts = [re.sub(r"\s*\n\s*", "。", block).strip() for block in blocks]
        else:
            prompts = [
                line.strip()
                for line in lines
                if line.strip()
                and not line.lstrip().startswith("#")
                and not (standalone_count == 1 and cls.STANDALONE_NUMBER.match(line))
            ]
        return [PromptItem(f"提示词 {index:03d}", prompt) for index, prompt in enumerate(prompts, 1)]

    @staticmethod
    def _docx_text(raw: bytes) -> str:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
        namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paragraphs = []
        for paragraph in root.iter(namespace + "p"):
            text = "".join(node.text or "" for node in paragraph.iter(namespace + "t")).strip()
            if text:
                paragraphs.append(text)
        return "\n".join(paragraphs)

    @staticmethod
    def _normalise_xlsx_header(value: Any) -> str:
        text = str(value or "")
        # Spreadsheet authors commonly append version/scope notes to a known
        # field name, for example ``完整正向Prompt（第一版结构）``.  Removing only
        # the brackets leaves the annotation attached to the field name and
        # prevents an otherwise exact alias match, so discard bracketed header
        # annotations before normalising separators.
        previous = None
        while text != previous:
            previous = text
            text = re.sub(r"（[^（）]*）|\([^()]*\)|【[^【】]*】|\[[^\[\]]*\]", "", text)
        return re.sub(r"[\s_\-—–·.。/\\|｜（）()【】\[\]：:]+", "", text).casefold()

    @classmethod
    def _xlsx_sheets(cls, raw: bytes) -> list[tuple[str, list[dict[str, str]]]]:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                shared = ["".join(node.text or "" for node in item.iter() if node.tag.endswith("}t")) for item in root]
            sheets: list[tuple[str, list[dict[str, str]]]] = []
            for name in sorted(item for item in archive.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", item)):
                root = ET.fromstring(archive.read(name))
                sheet_rows: list[dict[str, str]] = []
                for row in (node for node in root.iter() if node.tag.endswith("}row")):
                    values: dict[str, str] = {}
                    for cell in (node for node in row if node.tag.endswith("}c")):
                        value = next((node.text or "" for node in cell.iter() if node.tag.endswith("}v")), "")
                        if cell.attrib.get("t") == "inlineStr":
                            value = "".join(node.text or "" for node in cell.iter() if node.tag.endswith("}t"))
                        if cell.attrib.get("t") == "s" and value.isdigit() and int(value) < len(shared):
                            value = shared[int(value)]
                        if value.strip():
                            column = re.match(r"[A-Z]+", cell.attrib.get("r", ""))
                            values[column.group(0) if column else str(len(values))] = value.strip()
                    if values:
                        sheet_rows.append(values)
                if sheet_rows:
                    sheets.append((name, sheet_rows))
        return sheets

    @classmethod
    def xlsx_mapping_info(cls, raw: bytes) -> dict[str, Any]:
        sheets = cls._xlsx_sheets(raw)
        headers: list[str] = []
        for _, rows in sheets:
            for value in rows[0].values():
                if value not in headers:
                    headers.append(value)
        detected = {
            "title": next((value for value in headers if cls._normalise_xlsx_header(value) in cls.XLSX_TITLE_HEADERS), ""),
            "positive": next((value for value in headers if cls._normalise_xlsx_header(value) in cls.XLSX_POSITIVE_HEADERS), ""),
            "negative": next((value for value in headers if cls._normalise_xlsx_header(value) in cls.XLSX_NEGATIVE_HEADERS), ""),
        }
        selected = {value for value in detected.values() if value}
        detected["metadata"] = [value for value in headers if value not in selected]
        return {"headers": headers, "detected": detected}

    @classmethod
    def _xlsx_items(cls, raw: bytes, mapping: dict[str, Any] | None = None) -> list[PromptItem]:
        all_items: list[PromptItem] = []
        fallback_rows: list[str] = []
        supplied = dict(mapping or {})
        for name, sheet_rows in cls._xlsx_sheets(raw):
            raw_headers = sheet_rows[0]
            headers = {column: cls._normalise_xlsx_header(text) for column, text in raw_headers.items()}
            requested = {
                role: cls._normalise_xlsx_header(supplied.get(role))
                for role in ("title", "positive", "negative")
                if str(supplied.get(role) or "").strip()
            }
            prompt_columns = [column for column, header in headers.items() if header == requested.get("positive") or ("positive" not in requested and header in cls.XLSX_POSITIVE_HEADERS)]
            negative_columns = [column for column, header in headers.items() if header == requested.get("negative") or ("negative" not in requested and header in cls.XLSX_NEGATIVE_HEADERS)]
            title_columns = [column for column, header in headers.items() if header == requested.get("title") or ("title" not in requested and header in cls.XLSX_TITLE_HEADERS)]
            if prompt_columns:
                for row_index, row in enumerate(sheet_rows[1:], 1):
                    prompt = "。".join(row.get(column, "") for column in prompt_columns if row.get(column, "")).strip()
                    if not prompt:
                        continue
                    title = next((row.get(column, "") for column in title_columns if row.get(column, "")), f"提示词 {len(all_items) + 1:03d}")
                    negative = "，".join(row.get(column, "") for column in negative_columns if row.get(column, "")).strip()
                    excluded = set(prompt_columns + negative_columns + title_columns)
                    requested_metadata = supplied.get("metadata")
                    if isinstance(requested_metadata, str):
                        requested_metadata = [part.strip() for part in re.split(r"[,，;；\n]+", requested_metadata) if part.strip()] or None
                    metadata_filter = {cls._normalise_xlsx_header(value) for value in requested_metadata} if isinstance(requested_metadata, list) else None
                    metadata = {"_sheet": name, "_row": row_index + 1}
                    for column, header_text in raw_headers.items():
                        if column in excluded or not row.get(column, ""):
                            continue
                        if metadata_filter is not None and cls._normalise_xlsx_header(header_text) not in metadata_filter:
                            continue
                        metadata[header_text] = row[column]
                    all_items.append(PromptItem(title, prompt, metadata, negative))
            else:
                fallback_rows.extend("；".join(row.values()) for row in sheet_rows)
        return all_items or cls._parse_text("\n\n".join(fallback_rows))

    @staticmethod
    def _finish(filename: str, items: list[PromptItem], source_format: str) -> PromptBundle:
        items = [PromptBundleParser._split_labeled_negative(item) for item in items]
        if not items:
            raise ValueError("提示词合集为空")
        if len(items) > 1000:
            raise ValueError("单次最多导入 1000 条提示词")
        return PromptBundle(pathlib.Path(filename).stem, items, source_format)

    @staticmethod
    def _split_labeled_negative(item: PromptItem) -> PromptItem:
        if item.negative_prompt:
            return item
        match = re.search(r"(?:^|[。；\n])\s*(?:负面|反向|负向)提示词\s*[：:]\s*(.+)$", item.prompt, flags=re.S | re.I)
        if not match:
            prompt = re.sub(r"^\s*(?:正面|正向)提示词\s*[：:]\s*", "", item.prompt, flags=re.I)
            return PromptItem(item.title, prompt.strip(), item.metadata, "")
        positive = item.prompt[:match.start()].strip("。；\n ")
        positive = re.sub(r"^\s*(?:正面|正向)提示词\s*[：:]\s*", "", positive, flags=re.I)
        return PromptItem(item.title, positive.strip(), item.metadata, match.group(1).strip())

    @classmethod
    def _from_value(cls, value: Any, index: int) -> PromptItem:
        if isinstance(value, str):
            return PromptItem(f"提示词 {index:03d}", value.strip())
        if not isinstance(value, dict):
            raise ValueError(f"第 {index} 项不是文本或对象")
        title = str(value.get("title") or value.get("name") or f"提示词 {index:03d}").strip()
        direct = cls._prompt_text(value)
        prompt = direct or cls._render_structured(value)
        if not prompt:
            raise ValueError(f"第 {index} 项没有可生成的提示词")
        return PromptItem(title, prompt, copy.deepcopy(value), cls._negative_text(value))

    @staticmethod
    def _render_structured(value: dict[str, Any]) -> str:
        pieces: list[str] = []
        for key in ("brief", "category", "positive_contract", "review_focus"):
            if value.get(key):
                pieces.append(str(value[key]).strip())
        layers = value.get("layers") or {}
        if isinstance(layers, dict):
            layer_text = "；".join(str(item).strip() for item in layers.values() if str(item).strip())
            if layer_text:
                pieces.append("服饰：" + layer_text)
        for block_name in ("fashion_detail", "scene_detail"):
            block = value.get(block_name) or {}
            if isinstance(block, dict):
                detail = "；".join(str(item).strip() for item in block.values() if str(item).strip())
                if detail:
                    pieces.append(detail)
        for key, label in (("camera", "镜头"), ("composition", "构图"), ("pose", "姿势"), ("scene", "场景"), ("palette", "配色")):
            if value.get(key):
                pieces.append(f"{label}：{value[key]}")
        return "。".join(pieces)


class PromptCompiler:
    """Compiles one reviewed task into the exact text sent to ComfyUI."""

    RISKY_LAYOUT_TERMS = (
        "三视图", "多视图", "前后视图", "角色设定板", "角色设计板", "服装设定图",
        "转面图", "分镜", "分栏", "拼贴", "并排角色", "小窗", "辅助面板",
    )
    NEGATION_MARKERS = ("不是", "禁止", "不得", "不使用", "不要", "避免", "非")
    SINGLE_SUBJECT_GUARD = (
        "单幅连续画面，仅一名人物且人物只出现一次；人物拥有一个完整连续的身体，"
        "采用统一视角、单一镜头、单一姿势和单一连续场景，完成一张独立成品插画。"
    )

    @classmethod
    def _remove_visual_negation_cues(cls, prompt: str) -> str:
        clauses = re.split(r"[。！？；;\n]+", prompt)
        kept: list[str] = []
        for clause in clauses:
            fragments = re.split(r"[，,]+", clause)
            safe = [
                fragment.strip()
                for fragment in fragments
                if fragment.strip()
                and not (
                    any(term in fragment for term in cls.RISKY_LAYOUT_TERMS)
                    and any(marker in fragment for marker in cls.NEGATION_MARKERS)
                )
            ]
            if safe:
                kept.append("，".join(safe))
        return "。".join(kept).strip("。 ")

    @classmethod
    def apply_style_templates(cls, source: str, styles: Any) -> str:
        """docs/15 §6.2 方案A：风格模板编译进提示词（"提示词融合"语义）。

        ``{prompt}`` 占位替换；模板无占位则追加。正编路径（``compile``）与
        原生注入的回退档共用这一份实现，保证两条路合并结果一致。
        """
        text = str(source).strip()
        for style in styles:
            template = str(style.get("prompt") or "").strip()
            if not template:
                continue
            text = template.replace("{prompt}", text) if "{prompt}" in template else f"{text}, {template}"
        return text

    @classmethod
    def compile(cls, prompt: str, config: BatchConfig) -> str:
        source = str(prompt).strip()
        if config.single_subject_guard:
            source = cls._remove_visual_negation_cues(source)
        if config.style_application != "native":
            source = cls.apply_style_templates(source, config.styles)
        pieces = [source]
        triggers: list[str] = []
        for lora in config.loras:
            if not lora.get("use_triggers", True):
                continue
            values = lora.get("trigger_words") or []
            if isinstance(values, str):
                values = re.split(r"[,，;；\n]+", values)
            for value in values:
                word = str(value).strip()
                if word and word not in triggers:
                    triggers.append(word)
        if triggers:
            pieces.append("LoRA触发词：" + ", ".join(triggers) + "。")
        if config.single_subject_guard and cls.SINGLE_SUBJECT_GUARD not in pieces[0]:
            pieces.append(cls.SINGLE_SUBJECT_GUARD)
        return "\n".join(piece for piece in pieces if piece)

    @staticmethod
    def compile_negative(task_negative: str, config: BatchConfig) -> str:
        values: list[str] = []
        sources = [task_negative, config.negative_prompt, *(style.get("negative_prompt") for style in config.styles)]
        for source in sources:
            for part in re.split(r"[,，;；\n]+", str(source or "")):
                text = part.strip()
                if text and text.casefold() not in {item.casefold() for item in values}:
                    values.append(text)
        return ", ".join(values)


class ImageQualityInspector:
    """Fast local checks for obvious triptychs; no cloud or LLM is used."""

    @staticmethod
    def _similarity(left: Image.Image, right: Image.Image) -> float:
        left = left.convert("L").resize((96, 144))
        right = right.convert("L").resize((96, 144))
        mean_difference = ImageStat.Stat(ImageChops.difference(left, right)).mean[0]
        return max(0.0, 1.0 - mean_difference / 255.0)

    @classmethod
    def inspect(cls, path: str | pathlib.Path) -> dict[str, Any]:
        with Image.open(path) as image:
            image = image.convert("RGB")
            width, height = image.size
            third = width // 3
            if third < 32:
                return {"repeated_panels": False, "panel_similarity": 0.0, "size": [width, height]}
            panels = [image.crop((third * index, 0, third * (index + 1), height)) for index in range(3)]
            scores = [cls._similarity(panels[0], panels[1]), cls._similarity(panels[1], panels[2]), cls._similarity(panels[0], panels[2])]
            score = sum(scores) / len(scores)
            return {
                "repeated_panels": score >= 0.88,
                "panel_similarity": round(score, 4),
                "size": [width, height],
            }


class ImageSeedInspector:
    """The seeds recorded inside a finished image.

    ComfyUI stores the API prompt it executed in the PNG's ``prompt`` text chunk,
    so the produced file -- not the request we sent -- is the authority on which
    seed made that picture. Reading it is offline, per-image, and works long after
    ComfyUI's history has been cleared.

    A file without that chunk (metadata stripped, re-exported, or not a PNG at
    all) reports ``read=False`` with a reason. Guessing a seed there would defeat
    the entire point of the check.
    """

    #: Largest prompt chunk worth parsing. A batch image's graph is a few tens of
    #: kilobytes; anything far larger is not something to JSON-decode in a loop.
    MAX_CHUNK = 4 * 1024 * 1024

    @classmethod
    def inspect(cls, path: str | pathlib.Path) -> dict[str, Any]:
        try:
            with Image.open(path) as image:
                raw = (getattr(image, "text", None) or {}).get("prompt") or ""
        except Exception as exc:  # noqa: BLE001 - a broken file must not fail a run
            return {"read": False, "seeds": {}, "reason": f"读取图片元数据失败：{exc}"}
        if not raw:
            return {"read": False, "seeds": {}, "reason": "图片内没有 ComfyUI 记录的提示"}
        if len(raw) > cls.MAX_CHUNK:
            return {"read": False, "seeds": {}, "reason": "图片内嵌提示过大，已跳过"}
        try:
            graph = json.loads(raw)
        except json.JSONDecodeError as exc:
            return {"read": False, "seeds": {}, "reason": f"图片内嵌提示不是有效 JSON：{exc}"}
        if not isinstance(graph, dict):
            return {"read": False, "seeds": {}, "reason": "图片内嵌提示不是节点图"}
        return {"read": True, "seeds": graph_seeds(graph), "reason": ""}


def real_seed_evidence(source: str | pathlib.Path, entry: dict[str, Any] | None) -> dict[str, Any]:
    """Where the seeds really came from, most authoritative source first.

    The image's own metadata wins because it is per-file and outlives ComfyUI's
    history; the executed graph in ``/history`` is the next best thing; when
    neither can be read the submitted graph is all we have, and ``source`` says
    so rather than implying the seed was confirmed.
    """
    stamped = ImageSeedInspector.inspect(source)
    if stamped["read"] and stamped["seeds"]:
        return {"seeds": stamped["seeds"], "available": True, "source": "图片内嵌提示", "reason": ""}
    executed = executed_graph(entry)
    history_seeds = graph_seeds(executed) if executed else {}
    if history_seeds:
        return {"seeds": history_seeds, "available": True, "source": "执行历史", "reason": ""}
    reasons: list[str] = []
    if stamped["read"]:
        reasons.append("图片内嵌提示未发现具体种子")
    elif stamped["reason"]:
        reasons.append(str(stamped["reason"]))
    if executed:
        reasons.append("执行历史未发现具体种子")
    else:
        reasons.append("执行历史不可用")
    return {"seeds": {}, "available": False, "source": "未核对", "reason": "；".join(reasons)}


def seed_evidence_payload(planned: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """The ``generation`` keys that record what actually happened to the seed."""
    return {
        "real_seeds": evidence["seeds"],
        "seed_source": evidence["source"],
        "seed_check": seed_report(planned, evidence["seeds"], available=bool(evidence["available"])),
        "seed_note": evidence["reason"],
    }


class ResourceInventory:
    """Discovers selectable local ComfyUI resources behind one interface."""

    def __init__(self, comfy_root: pathlib.Path, workflow_roots: list[pathlib.Path], client: Any = None):
        self.comfy_root = pathlib.Path(comfy_root)
        self.workflow_roots = [pathlib.Path(root) for root in workflow_roots]
        #: Optional ComfyUI client. When present, resource listings come from
        #: ``/models/{folder}`` so extra_model_paths are included; otherwise the
        #: inventory falls back to scanning the filesystem.
        self.client = client

    def snapshot(self) -> dict[str, Any]:
        return {
            "comfy_root": str(self.comfy_root),
            "workflows": self._workflows(),
            "models": self._models(),
            "loras": self._loras(),
            "styles": self._styles(),
            "upscale_models": self._upscale_models(),
        }

    def _comfy_listing(self, folder: str) -> list[str]:
        """ComfyUI's own merged listing for a model folder, when reachable.

        Preferred over filesystem scanning because ComfyUI merges
        ``extra_model_paths.yaml`` into it, and because the names it returns are
        exactly the strings the node inputs expect (including subfolder-relative
        paths for LoRAs).
        """
        client = getattr(self, "client", None)
        if client is None or not hasattr(client, "models"):
            return []
        try:
            return list(client.models(folder))
        except Exception:  # noqa: BLE001 - offline must degrade, not fail
            return []

    def _upscale_models(self) -> list[dict[str, Any]]:
        """Upscale models, which the previous version never enumerated.

        Without this the missing ``OmniSR_X4_DIV2K.safetensors`` could only be
        discovered by ComfyUI rejecting the submission.
        """
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for value in self._comfy_listing("upscale_models"):
            seen.add(value.lower())
            rows.append({"name": pathlib.Path(value).name, "value": value, "root": "ComfyUI", "loader": "UpscaleModelLoader", "source": "comfyui"})
        root = self.comfy_root / "models" / "upscale_models"
        for row in self._scan_model_roots([(root, "UpscaleModelLoader")]):
            if row["value"].lower() in seen:
                continue
            row["source"] = "filesystem"
            rows.append(row)
        return rows

    def _workflows(self) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        seen: set[str] = set()
        for root in self.workflow_roots:
            if not root.exists():
                continue
            for path in sorted(root.rglob("*.json")):
                resolved = str(path.resolve()).lower()
                if resolved in seen:
                    continue
                seen.add(resolved)
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    ui_nodes = payload.get("nodes", []) if isinstance(payload, dict) else []
                    ui_nodes = [node for node in ui_nodes if int(node.get("mode", 0) or 0) == 0]
                    node_types = {str(node.get("type")) for node in ui_nodes}
                    api_like = self._is_api_graph(payload)
                    if api_like:
                        node_types = {str(node.get("class_type")) for node in payload.values()}
                    model_loader = "UNETLoader" if "UNETLoader" in node_types else "CheckpointLoaderSimple" if "CheckpointLoaderSimple" in node_types else ""
                    core = {"SaveImage", "KSampler", "VAEDecode"}
                    latent_source = bool({"EmptyLatentImage", "EmptySD3LatentImage", "VAEEncode"} & node_types)
                    prompt_target = bool({"PrimitiveStringMultiline", "CLIPTextEncode"} & node_types)
                    compatible = core.issubset(node_types) and latent_source and bool(model_loader) and prompt_target
                    current_model = self._workflow_model(payload, model_loader)
                    variants = Krea2WorkflowAdapter(payload).workflow_variants() if not api_like else []
                    family = self._model_family(current_model)
                    # Ship each workflow its own candidate list, computed by the
                    # very method /api/start validates against. The page used to
                    # rebuild this filter itself, which is how the two drifted
                    # apart and why a model the user has got refused as
                    # "incompatible".
                    candidates = [
                        {"value": item["value"], "name": item["name"], "family": item.get("family", "")}
                        for item in self.compatible_models_for(model_loader, family)
                    ]
                    found.append({
                        "name": path.name,
                        "value": str(path),
                        "format": "api" if api_like else "ui",
                        "compatible": compatible,
                        "model_loader": model_loader,
                        "model_family": family,
                        "current_model": current_model,
                        "compatible_models": candidates,
                        "variants": variants,
                    })
                except Exception as exc:
                    found.append({"name": path.name, "value": str(path), "format": "invalid", "compatible": False, "error": str(exc)})
        return found

    @staticmethod
    def _is_api_graph(value: Any) -> bool:
        return isinstance(value, dict) and bool(value) and "nodes" not in value and all(
            isinstance(item, dict) and "class_type" in item for item in value.values()
        )

    @staticmethod
    def _model_family(name: str) -> str:
        lowered = str(name or "").lower()
        for family, terms in (
            ("krea2", ("krea2", "krea_2")),
            ("minimax", ("minimax", "h3_")),
            ("z-image", ("z-image", "z_image", "zimage")),
            ("sdxl", ("sdxl", "xl_", " xl")),
            ("flux", ("flux",)),
        ):
            if any(term in lowered for term in terms):
                return family
        return "unknown"

    @staticmethod
    def model_basename(name: Any) -> str:
        """The file name part of a model reference, without any folder.

        Model references are written inconsistently across workflows: the same
        ``Krea2/krea2_turbo.safetensors`` is named bare in one loader node and
        with its folder in another, and the model inventory reports it relative
        to the model root. Comparing those strings directly reports a model the
        user actually has as "incompatible", so every comparison goes through
        this. Both ``/`` and ``\\`` are separators: workflows saved on Windows
        carry backslashes.
        """
        text = str(name or "").strip()
        if not text:
            return ""
        # Normalize separators before splitting so both styles behave the same.
        return text.replace("\\", "/").rsplit("/", 1)[-1]

    @staticmethod
    def model_key(name: Any) -> str:
        """A comparison key for a model reference: basename, case-folded."""
        return ResourceInventory.model_basename(name).lower()

    @classmethod
    def _workflow_model(cls, payload: dict[str, Any], loader: str) -> str:
        """The model this workflow loads, reported as a bare file name.

        Deliberately folder-free: loader nodes disagree about whether to include
        the folder, and a bare name is the shape ``/api/start`` validates
        against. ``ResourceInventory.model_basename`` is the single normalizer
        both sides use, so they cannot drift apart again.
        """
        if not loader:
            return ""
        if cls._is_api_graph(payload):
            node = next((item for item in payload.values() if item.get("class_type") == loader), {})
            inputs = node.get("inputs") or {}
            raw = inputs.get("unet_name") or inputs.get("ckpt_name") or ""
        else:
            node = next((item for item in payload.get("nodes", []) if item.get("type") == loader), {})
            widgets = node.get("widgets_values") or []
            raw = widgets[0] if widgets else ""
        return cls.model_basename(raw)

    def _scan_model_roots(self, roots: list[tuple[pathlib.Path, str]]) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for root, loader in roots:
            if not root.exists():
                continue
            for path in sorted(item for item in root.rglob("*") if item.is_file() and item.suffix.lower() in MODEL_EXTENSIONS):
                value = path.relative_to(root).as_posix()
                key = (value.lower(), str(root).lower())
                if key not in seen:
                    seen.add(key)
                    found.append({
                        "name": path.name,
                        "value": value,
                        "root": str(root),
                        "path": str(path),
                        "loader": loader,
                        "family": self._model_family(value),
                    })
        return found

    def _models(self) -> list[dict[str, Any]]:
        models = self.comfy_root / "models"
        return self._scan_model_roots([
            (models / "diffusion_models", "UNETLoader"),
            (models / "unet", "UNETLoader"),
            (models / "checkpoints", "CheckpointLoaderSimple"),
        ])

    def _loras(self) -> list[dict[str, Any]]:
        roots = [self.comfy_root / "models" / "loras"]
        rows = self._scan_model_roots([(root, "LoraLoaderModelOnly") for root in roots])
        for row in rows:
            row.update(self._lora_metadata(pathlib.Path(row["path"])))
        return rows

    def compatible_models_for(self, model_loader: str, family: str = "") -> list[dict[str, Any]]:
        """Models this workflow may legitimately load, unfiltered by name.

        Split out from ``compatible_models`` because the workflow scan needs the
        candidate list before it can look a workflow up by path, and because the
        page needs exactly the same list the validator uses.
        """
        rows = [item for item in self._models() if not model_loader or item.get("loader") == model_loader]
        if family and family != "unknown":
            narrowed = [item for item in rows if item.get("family") == family]
            if narrowed:
                rows = narrowed
        return rows

    def compatible_models(self, workflow_path: str, workflow_variant: str = "") -> list[dict[str, Any]]:
        workflow = next((item for item in self._workflows() if pathlib.Path(item["value"]).resolve() == pathlib.Path(workflow_path).resolve()), None)
        if not workflow:
            return []
        variant = next((item for item in workflow.get("variants", []) if item.get("id") == workflow_variant), None)
        loader = (variant or {}).get("model_loader") or workflow.get("model_loader")
        return self.compatible_models_for(loader, workflow.get("model_family") or "")

    @classmethod
    def _lora_metadata(cls, path: pathlib.Path) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        try:
            with path.open("rb") as handle:
                header_size = struct.unpack("<Q", handle.read(8))[0]
                if 0 < header_size <= 16 * 1024 * 1024:
                    header = json.loads(handle.read(header_size).decode("utf-8"))
                    metadata = dict(header.get("__metadata__") or {}) if isinstance(header, dict) else {}
        except Exception:
            metadata = {}
        triggers: list[str] = []
        for key in ("modelspec.tags", "trigger_words", "ss_trigger_words"):
            value = metadata.get(key)
            if isinstance(value, str):
                triggers.extend(re.split(r"[,，;；\n]+", value))
        frequencies = metadata.get("ss_tag_frequency")
        if isinstance(frequencies, str):
            try:
                frequencies = json.loads(frequencies)
            except json.JSONDecodeError:
                frequencies = None
        if isinstance(frequencies, dict):
            counts: dict[str, float] = {}
            for group in frequencies.values():
                if isinstance(group, dict):
                    for tag, count in group.items():
                        try:
                            counts[str(tag)] = counts.get(str(tag), 0.0) + float(count)
                        except (TypeError, ValueError):
                            continue
            triggers.extend(tag for tag, _ in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:8])
        cleaned: list[str] = []
        for value in triggers:
            word = str(value).strip()
            if word and word not in cleaned:
                cleaned.append(word)
        base_model = str(metadata.get("ss_base_model_version") or metadata.get("modelspec.architecture") or "")
        return {
            "trigger_words": cleaned,
            "trigger_status": "confirmed" if cleaned else "unknown",
            "family": cls._model_family(base_model) if base_model else "unknown",
            "metadata_summary": {
                "title": str(metadata.get("modelspec.title") or metadata.get("ss_output_name") or metadata.get("name") or ""),
                "architecture": str(metadata.get("modelspec.architecture") or ""),
                "base_model": base_model,
                "notes": str(metadata.get("notes") or "")[:500],
            },
        }

    def _styles(self) -> list[dict[str, Any]]:
        custom_nodes = self.comfy_root / "custom_nodes"
        found: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        if not custom_nodes.exists():
            return found
        candidates = list(custom_nodes.rglob("styles/*.json")) + list(custom_nodes.rglob("styles/**/*.json"))
        for path in sorted(set(candidates)):
            if "backup" in str(path).lower():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, list):
                    continue
                library = path.stem
                library_cn = self._style_library_cn(library)
                for entry in payload:
                    name = str(entry.get("name", "")).strip() if isinstance(entry, dict) else ""
                    key = (library, name)
                    if name and key not in seen:
                        seen.add(key)
                        found.append({
                            "library": library,
                            "library_cn": library_cn,
                            "name": name,
                            "name_cn": str(entry.get("name_cn") or name).strip(),
                            "display_name": self._style_display_name(str(entry.get("name_cn") or ""), name),
                            "prompt": str(entry.get("prompt") or ""),
                            "negative_prompt": str(entry.get("negative_prompt") or ""),
                            "thumbnail": str(entry.get("thumbnail") or "").strip(),
                        })
            except Exception:
                continue
        return found

    @staticmethod
    def _style_library_cn(library: str) -> str:
        parts = [part for part in str(library).split("_") if part and part.lower() not in {"krea2", "styles"}]
        chinese = next((part for part in reversed(parts) if re.search(r"[\u4e00-\u9fff]", part)), "")
        label = chinese.replace("-", "·") if chinese else "风格库"
        numbered = next((re.search(r"-(\d+)$", part) for part in parts if re.search(r"-(\d+)$", part)), None)
        if numbered:
            label += f"（第{numbered.group(1)}组）"
        elif str(library).lower().endswith("_styles"):
            label += "风格合集"
        return label

    @staticmethod
    def _style_display_name(name_cn: str, name: str) -> str:
        if re.search(r"[\u4e00-\u9fff]", name_cn):
            return name_cn
        phrases = {
            "Cel-Shaded": "赛璐璐着色", "Cel Animation": "赛璐璐动画", "Concept Art": "概念艺术",
            "Art Nouveau": "新艺术", "Art Deco": "装饰艺术", "Golden Hour": "黄金时刻",
            "High-Key": "高调", "Low-Key": "低调", "Mid-Century": "中世纪现代",
            "Hand-Drawn": "手绘", "Crosshatch": "交叉排线", "Screenprint": "丝网印刷",
            "Dreamscape": "梦境", "Cloudscapes": "云海", "Storytelling": "叙事",
            "Solarpunk": "太阳朋克", "Cyberpunk": "赛博朋克", "Cottagecore": "乡村美学",
            "Retro-Futurism": "复古未来主义", "Futurism": "未来主义", "Surrealism": "超现实主义",
            "Minimalism": "极简主义", "Maximalism": "繁复主义", "Impressionism": "印象主义",
            "Expressionism": "表现主义", "Brutalism": "粗野主义", "Pastoralism": "田园主义",
            "Photography": "摄影", "Illustration": "插画", "Animation": "动画", "Anime": "动漫",
            "Watercolor": "水彩", "Gouache": "水粉", "Pencil": "铅笔", "Ink": "墨线",
            "Vector": "矢量", "Graphic": "图形", "Painterly": "绘画感", "Voxel": "体素",
            "Isometric": "等距", "Miniatures": "微缩景观", "Architecture": "建筑",
            "Architectural": "建筑", "Industrial": "工业", "Editorial": "编辑设计",
            "Fashion": "时尚", "Portraiture": "人像", "Portrait": "人像", "Landscape": "风景",
            "Ethereal": "空灵", "Luminous": "明亮", "Luminescent": "发光", "Nostalgic": "怀旧",
            "Nostalgia": "怀旧", "Whimsical": "奇幻", "Dreamy": "梦幻", "Cozy": "温馨",
            "Gritty": "粗粝", "Clinical": "冷峻", "Monumental": "宏伟", "Colossal": "巨构",
            "Nocturnal": "夜间", "Midnight": "午夜", "Twilight": "暮光", "Sunset": "日落",
            "Neon": "霓虹", "Pastel": "粉彩", "Crimson": "绯红", "Cobalt": "钴蓝",
            "Azure": "蔚蓝", "Indigo": "靛蓝", "Amber": "琥珀", "Magenta": "品红",
            "Emerald": "祖母绿", "Mint": "薄荷色", "Monochromatic": "单色",
            "Classic": "经典", "Modern": "现代", "Retro": "复古", "Analog": "模拟",
            "Glitch": "故障", "Kinetic": "动感", "Dynamic": "动态", "Soft": "柔和",
            "Bold": "大胆", "Minimal": "极简", "Ornate": "华丽", "Organic": "有机",
            "Abstract": "抽象", "Cosmic": "宇宙", "Celestial": "天穹", "Arcane": "秘法",
            "Gothic": "哥特", "Noir": "黑色电影", "Punk": "朋克", "Kawaii": "可爱",
            "Ghibli": "吉卜力", "Summer": "夏日", "Forest": "森林", "Woodlands": "林地",
            "Clouds": "云景", "Sky": "天空", "Abyss": "深渊", "Utopia": "乌托邦",
            "Blueprint": "蓝图", "Linework": "线稿", "Glow": "辉光", "Light": "光影",
            "Dappled": "斑驳", "Translucent": "半透明", "Floral": "花卉", "Velvet": "天鹅绒",
            "Avant-Garde": "前卫", "Techwear": "机能服", "Chiaroscuro": "明暗对照",
            "Synthesis": "融合", "Realism": "写实", "Realistic": "写实", "Stylized": "风格化",
        }
        translated = name
        for source, target in sorted(phrases.items(), key=lambda item: len(item[0]), reverse=True):
            translated = re.sub(rf"\b{re.escape(source)}\b", target, translated, flags=re.IGNORECASE)
        translated = translated.replace("—", "·").replace("��", "·")
        return translated if re.search(r"[\u4e00-\u9fff]", translated) else f"风格·{name}"


class Krea2WorkflowAdapter:
    """Turns a compatible UI workflow into a ComfyUI prompt graph."""

    def __init__(self, workflow: dict[str, Any], registry: "NodeSchemaRegistry | None" = None):
        self.workflow = workflow
        self.registry = registry
        #: Per-node input provenance from the last build, consumed by the audit.
        self.last_sources: dict[str, dict[str, str]] = {}
        self.last_binding_problems: list[Any] = []
        self.last_graph: dict[str, Any] | None = None
        #: Problems found while applying workbench parameters.
        self.last_param_problems: list[Any] = []
        #: Conditioning nodes that lost every consumer to a negative-prompt
        #: injection: ``{node_id: class_type}``. Left in the graph on purpose --
        #: see ``_apply_prompt`` -- but reported so the user can tell.
        self.last_orphaned_conditioning: dict[str, str] = {}
        # docs/15 §6.1 方案A/C bookkeeping: which sampler's negative encoder got
        # its CLIP from a graph-wide fallback (方案A), and which samplers kept
        # the author's own empty negative because the graph has no CLIP source
        # at all (方案C). Both must be visible in the audit.
        self.last_negative_clip_fallback: dict[str, str] = {}
        self.last_negative_skipped: dict[str, str] = {}
        # docs/15 §6.2 方案A bookkeeping: the native-style injection ledger --
        # which samplers met the whitelist anchor contract, which did not,
        # what got injected, and which text source was displaced.
        self.last_style_injection: dict[str, Any] = {}

    @classmethod
    def from_path(cls, path: str | pathlib.Path, registry: "NodeSchemaRegistry | None" = None) -> "Krea2WorkflowAdapter":
        payload = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        return cls(payload, registry)

    def _schema(self) -> "NodeSchemaRegistry":
        return self.registry or active_registry()

    def _ui_variant_closure(self, save_node_id: str) -> set[str]:
        nodes = {str(node["id"]): node for node in self.workflow.get("nodes", [])}
        links = {int(link[0]): str(link[1]) for link in self.workflow.get("links", [])}
        if save_node_id not in nodes or str(nodes[save_node_id].get("type")) != "SaveImage":
            return set()
        selected = {save_node_id}
        pending = [save_node_id]
        while pending:
            node = nodes[pending.pop()]
            for item in node.get("inputs") or []:
                link_id = item.get("link")
                if link_id is None or int(link_id) not in links:
                    continue
                source_id = links[int(link_id)]
                if source_id not in selected:
                    selected.add(source_id)
                    pending.append(source_id)
        return selected

    def workflow_variants(self) -> list[dict[str, Any]]:
        """Describe independently selectable SaveImage output branches in a UI workflow."""
        if self._is_api_graph(self.workflow):
            return []
        nodes = {str(node["id"]): node for node in self.workflow.get("nodes", [])}
        variants: list[dict[str, Any]] = []
        upscale_types = {"LatentUpscale", "LatentUpscaleBy", "UltimateSDUpscale", "ImageUpscaleWithModel", "ImageScale", "ImageScaleBy", "ImageScaleToTotalPixels"}
        for node_id, node in nodes.items():
            if str(node.get("type")) != "SaveImage":
                continue
            closure = self._ui_variant_closure(node_id)
            types = [str(nodes[item].get("type") or "") for item in closure]
            position = list(node.get("pos") or [0, 0])
            group_names = []
            for group in self.workflow.get("groups", []) or []:
                bounds = list(group.get("bounding") or [])
                if len(bounds) >= 4 and len(position) >= 2:
                    x, y, width, height = (float(value) for value in bounds[:4])
                    if x <= float(position[0]) <= x + width and y <= float(position[1]) <= y + height:
                        group_names.append(str(group.get("title") or "").strip())
            widgets = node.get("widgets_values")
            if isinstance(widgets, list):
                prefix = str(widgets[0] if widgets else "").strip()
            else:
                prefix = str(widgets or "").strip()
            name = next((value for value in group_names if value), "") or prefix or f"输出节点 {node_id}"
            model_loader = "UNETLoader" if "UNETLoader" in types else "CheckpointLoaderSimple" if "CheckpointLoaderSimple" in types else ""
            variants.append({
                "id": f"save:{node_id}",
                "name": name,
                "save_node_id": node_id,
                "active": int(node.get("mode", 0) or 0) == 0,
                "sampler_count": sum(1 for value in types if value in {"KSampler", "KSamplerAdvanced"}),
                "upscale_count": sum(1 for value in types if value in upscale_types),
                "model_loader": model_loader,
                "node_count": len(closure),
            })
        return variants

    def _selected_ui_node_ids(self, config: BatchConfig) -> set[str] | None:
        variant = str(config.workflow_variant or "").strip()
        if not variant:
            return None
        if not variant.startswith("save:"):
            return set()
        return self._ui_variant_closure(variant.split(":", 1)[1])

    def capabilities(self, config: BatchConfig, source_image: str = "") -> dict[str, Any]:
        selected_variant: dict[str, Any] | None = None
        #: Nodes of the branch this config selects. ``seed_plan`` needs it: a
        #: workflow where one branch can be pinned and another cannot must not be
        #: reported as if both were equally fine.
        active_seed_ids: set[str] | None = None
        if self._is_api_graph(self.workflow):
            node_types = {str(node.get("class_type")) for node in self.workflow.values()}
            workflow_format = "API"
            node_ids = lambda class_type: [str(node_id) for node_id, node in self.workflow.items() if str(node.get("class_type")) == class_type]
        else:
            selected_ids = self._selected_ui_node_ids(config)
            active_nodes = [
                node for node in self.workflow.get("nodes", [])
                if str(node.get("id")) in selected_ids
            ] if selected_ids is not None else [
                node for node in self.workflow.get("nodes", []) if int(node.get("mode", 0) or 0) == 0
            ]
            active_seed_ids = {str(node.get("id")) for node in active_nodes}
            node_types = {str(node.get("type")) for node in active_nodes}
            workflow_format = "UI"
            node_ids = lambda class_type: [str(node.get("id")) for node in active_nodes if str(node.get("type")) == class_type]
            selected_variant = next((item for item in self.workflow_variants() if item["id"] == config.workflow_variant), None)
        errors: list[str] = []
        warnings: list[str] = []
        if config.workflow_variant and not selected_variant and workflow_format == "UI":
            errors.append("所选工作流执行分支不存在，请重新选择")
        if "SaveImage" not in node_types:
            errors.append("工作流缺少 SaveImage")
        if not ({"KSampler", "KSamplerAdvanced"} & node_types):
            errors.append("工作流缺少 KSampler 或 KSamplerAdvanced")
        model_loader = "UNETLoader" if "UNETLoader" in node_types else "CheckpointLoaderSimple" if "CheckpointLoaderSimple" in node_types else ""
        if not model_loader:
            errors.append("工作流缺少受支持的模型加载器（UNETLoader 或 CheckpointLoaderSimple）")
        prompt_target = "PrimitiveStringMultiline" if "PrimitiveStringMultiline" in node_types else "CLIPTextEncode" if "CLIPTextEncode" in node_types else ""
        if not prompt_target:
            errors.append("工作流缺少可写入提示词的节点")
        image_input_nodes = node_ids("LoadImage")
        if source_image and not image_input_nodes:
            errors.append("当前任务包含输入图片，但工作流没有可用的 LoadImage 节点")
        negative_requested = bool(PromptCompiler.compile_negative("", config))
        # docs/15 §6.1 方案A+C: the CLIP search is graph-wide now, so the
        # preflight asks "is there ANY usable CLIP source" instead of demanding
        # a CLIPTextEncode. Only a graph with no CLIP source at all ends up not
        # applying the negative prompt -- and that is announced here (方案C),
        # not refused: the run keeps the author's own wiring.
        negative_clip_source = bool({"CLIPTextEncode", "CLIPLoader", "CheckpointLoaderSimple"} & node_types)
        if negative_requested and not negative_clip_source:
            warnings.append(
                "已填写负面提示词，但工作流没有任何 CLIP 来源（CLIPTextEncode/CLIPLoader/CheckpointLoaderSimple），"
                "将按作者原样挂空负面运行：负面词不会生效"
            )
        # The workflow author may already handle the negative branch without a
        # real encoder -- ``ConditioningZeroOut`` is the common idiom, and it
        # means "no negative guidance at all". Writing a negative prompt in that
        # case does not *fail*; it quietly swaps the author's handling for real
        # negative guidance, which is a semantic change the user cannot see.
        # Report it, and let them decide -- do not refuse, and do not hide it.
        # (Only when a CLIP source exists: with none, the author's wiring stays
        # untouched and the 方案C warning above already covers it.)
        negative_surrogates = (
            self._negative_anchor_types(self.workflow, config) if negative_requested else []
        )
        if negative_surrogates and negative_clip_source:
            named = "、".join(negative_surrogates)
            warnings.append(
                f"该工作流的负面条件原本由「{named}」处理，填写负面提示词后将改为"
                f"直接负面引导；若原作依赖该节点（如归一化负面以关闭负面引导），出图效果会随之改变"
            )

        selected_styles = config.styles or ([{"catalog": config.style_library, "name": config.style_name}] if config.style_name else [])
        usable_styles = [item for item in selected_styles if str(item.get("prompt") or "").strip()]
        native_styles = config.style_application == "native"
        # docs/15 §6.2 方案A：capabilities 与 build 共用同一份锚点评估，
        # 预告不再许诺构建做不到的事。
        style_hosts = self._style_anchor_hosts(self.workflow) if isinstance(self.workflow, dict) else {}
        if selected_styles and native_styles and prompt_target and style_hosts:
            style_mode = "工作流原生组合器"
        elif selected_styles and native_styles and prompt_target:
            # 白名单锚点不满足：不再硬拒（原实现会在 build 时抛
            # "无法启用原生风格组合器"），回退为提示词编译并如实相告。
            if usable_styles:
                style_mode = "本地提示词编译"
                warnings.append(
                    "该工作流的采样器正面口未直连 CLIPTextEncode，无法插入原生风格组合器（白名单锚点不满足）；"
                    "将把风格模板编译进提示词，并绕开工作流自带的风格组合器（如有）"
                )
            else:
                style_mode = "不可用"
                errors.append(
                    "所选风格没有可编译的模板文字，且工作流不满足原生风格注入的锚点契约"
                    "（采样器正面口未直连 CLIPTextEncode），无法应用该风格"
                )
        elif selected_styles and usable_styles:
            style_mode = "本地提示词编译"
            if len(usable_styles) != len(selected_styles):
                warnings.append(f"{len(selected_styles) - len(usable_styles)} 个风格没有模板文字，将被忽略")
        elif selected_styles and "easy stylesSelector" in node_types and len(selected_styles) == 1:
            style_mode = "工作流风格节点"
        elif selected_styles:
            style_mode = "不可用"
            errors.append("所选风格没有可编译的模板文字，且工作流不能可靠应用该风格")
        else:
            style_mode = "未选择"

        required_injected_nodes: list[str] = []
        if selected_styles and native_styles:
            required_injected_nodes.extend(["PrimitiveStringMultiline", "easy stylesSelector"])
        if config.loras and model_loader == "CheckpointLoaderSimple":
            lora_mode = "MODEL + CLIP"
            required_injected_nodes.append("LoraLoader")
        elif config.loras and model_loader == "UNETLoader":
            lora_mode = "MODEL"
            required_injected_nodes.append("LoraLoaderModelOnly")
        elif config.loras:
            lora_mode, required_injected_nodes = "不可用", []
        else:
            lora_mode, required_injected_nodes = "未选择", []
        # 固定种子能落到哪里：预检阶段就说清楚。以前这里什么都没说，用户选「固定种子」
        # 而工作流的种子由上游节点提供时，每次出图都是随机的，页面上连一行提示都没有。
        seed_evidence = seed_plan(self.workflow, self._schema(), active_seed_ids)
        if seed_evidence["warning"]:
            warnings.append(seed_evidence["warning"])
        width, height = resolve_image_dimensions(config.aspect_ratio, config.megapixels)
        workflow_path = pathlib.Path(config.workflow_path).resolve()
        # The audit, the saved replacement rules and a workflow's saved parameter
        # defaults are all keyed by the *structural* fingerprint (see
        # ``Application.workflow_fingerprint``). This report used to publish a
        # whole-file hash under the same name, so the page showed two different
        # "工作流指纹" values for one workflow at the same time. The file hash is
        # still worth having -- it changes when the file changes at all -- so it
        # keeps its own key instead of borrowing the other one's name.
        file_hash = ""
        if workflow_path.is_file():
            file_hash = hashlib.sha256(workflow_path.read_bytes()).hexdigest()[:12]
        fingerprint = structural_fingerprint(self.workflow) if isinstance(self.workflow, dict) else ""
        dimension_nodes = node_ids("ResolutionSelector") + node_ids("EmptyLatentImage") + node_ids("EmptySD3LatentImage")
        sampler_nodes = node_ids("KSampler") + node_ids("KSamplerAdvanced")
        upscale_types = ("LatentUpscale", "LatentUpscaleBy", "UltimateSDUpscale", "ImageUpscaleWithModel", "ImageScale", "ImageScaleBy", "ImageScaleToTotalPixels")
        upscale_nodes = [node_id for class_type in upscale_types for node_id in node_ids(class_type)]
        return {
            "ready": not errors,
            "format": workflow_format,
            "workflow": {
                "name": workflow_path.name or str(config.workflow_path),
                "path": str(workflow_path),
                "fingerprint": fingerprint,
                "file_hash": file_hash,
            },
            "nodes": {
                "prompt": node_ids(prompt_target) if prompt_target else [],
                "image_input": image_input_nodes,
                "dimensions": list(dict.fromkeys(dimension_nodes)),
                "save": node_ids("SaveImage"),
                "model": node_ids(model_loader) if model_loader else [],
                "samplers": sampler_nodes,
                "upscale": upscale_nodes,
            },
            "errors": errors,
            "warnings": warnings,
            "model": {"name": config.model, "loader": model_loader or "不可用"},
            "prompt_target": prompt_target or "不可用",
            "negative_prompt": {
                "requested": negative_requested,
                "mode": (
                    "未填写" if not negative_requested
                    else "空负面（填了但不生效）" if not negative_clip_source
                    else "自动写入或补建 CLIPTextEncode"
                ),
                "replaces": negative_surrogates,
                "empty_negative": bool(negative_requested and not negative_clip_source),
                # 方案A: no CLIPTextEncode at all means the rebuilt negative
                # encoder can only borrow from a loader output -- say so up
                # front, the audit then carries the exact source per sampler.
                "fallback_expected": bool(negative_requested and negative_clip_source and "CLIPTextEncode" not in node_types),
            },
            "input_image": {"requested": bool(source_image), "supported": bool(image_input_nodes), "path": source_image},
            "style": {
                "mode": style_mode,
                "count": len(selected_styles) if native_styles else len(usable_styles),
                "names": [str(item.get("name") or "") for item in selected_styles],
                # docs/15 §6.2 方案A：锚点契约评估结果与回退标记，真相栏据此标注。
                "native_anchor": bool(style_hosts),
                "native_fallback": bool(selected_styles and native_styles and prompt_target and not style_hosts and usable_styles),
            },
            "lora": {"mode": lora_mode, "count": len(config.loras), "names": [str(item.get("name") or "") for item in config.loras]},
            "seed": seed_evidence,
            "dimensions": {"width": width, "height": height},
            "required_injected_nodes": required_injected_nodes,
            "variant": selected_variant or {"id": "", "name": "当前已启用分支", "sampler_count": len(sampler_nodes), "upscale_count": len(upscale_nodes)},
            "variants": self.workflow_variants(),
        }

    def build(self, prompt_text: str, config: BatchConfig, output_prefix: str, task_negative: str = "", source_image: str = "") -> dict[str, Any]:
        report = self.capabilities(config, source_image=source_image)
        if not report["ready"]:
            raise ValueError("；".join(report["errors"]))
        if self._is_api_graph(self.workflow):
            graph = copy.deepcopy(self.workflow)
            self.last_sources = {}
            self.last_binding_problems = []
            self.last_orphaned_conditioning = {}
            self.last_negative_clip_fallback = {}
            self.last_negative_skipped = {}
            self.last_style_injection = {}
            self._override_api_graph(graph, prompt_text, config, output_prefix, task_negative, source_image)
        else:
            self.last_orphaned_conditioning = {}
            self.last_negative_clip_fallback = {}
            self.last_negative_skipped = {}
            self.last_style_injection = {}
            graph = self._convert_ui_graph(prompt_text, config, output_prefix, task_negative, source_image)
        self._inject_loras(graph, config.loras, self.last_sources)
        if config.seed is not None:
            apply_seed(graph, int(config.seed))
        # The parameter workbench goes on last, so a value the user set explicitly
        # is what actually reaches ComfyUI. Problems are collected rather than
        # raised here; the audit turns them into blocking findings.
        self.last_param_problems = apply_params(graph, config.params, self._schema(), self.last_sources)
        self._validate_graph_links(graph)
        self.last_graph = graph
        return graph

    def audit(self, config: BatchConfig | None = None, branch: str = "") -> dict[str, Any]:
        """Explain the graph produced by the last :meth:`build`.

        This is the fourth preflight layer and the evidence behind "the page
        value equals the submitted graph".
        """
        graph = getattr(self, "last_graph", None)
        if graph is None:
            return {}
        # Shared with Application.workflow_fingerprint so a saved rule and an audit
        # entry always refer to the same workflow revision.
        fingerprint = structural_fingerprint(self.workflow) if isinstance(self.workflow, dict) else ""
        result = CompiledGraphAudit.run(
            graph,
            registry=self._schema(),
            sources=self.last_sources,
            workflow_fingerprint=fingerprint,
            branch=branch,
            purpose=str(getattr(config, "purpose", "") or ""),
        )
        result["binding_problems"] = [problem.to_dict() for problem in self.last_binding_problems]
        result["param_problems"] = [problem.to_dict() for problem in self.last_param_problems]
        # Conditioning nodes the negative prompt displaced. They stay in the
        # graph deliberately (a rgthree "Fast Groups Bypasser" can re-enable the
        # branch later, so deleting them is not safe), but they must be visible:
        # a user editing the workflow afterwards would otherwise not understand
        # why their negative-conditioning node no longer does anything.
        result["orphaned_conditioning"] = [
            {"node_id": node_id, "node_type": node_type}
            for node_id, node_type in sorted(
                self.last_orphaned_conditioning.items(),
                key=lambda item: int(item[0]) if str(item[0]).isdigit() else 0,
            )
        ]
        # docs/15 §6.1 方案A/C evidence: which negative encoders borrowed their
        # CLIP from a graph-wide fallback (so the source is accounted for), and
        # which samplers kept the author's empty negative because the graph has
        # no CLIP source at all (so the user knows the text did not apply).
        result["negative_clip_fallback"] = dict(self.last_negative_clip_fallback)
        result["negative_skipped"] = dict(self.last_negative_skipped)
        # docs/15 §6.2 方案A evidence: the whitelist injection ledger -- anchor
        # hits and misses per sampler, the injected node ids, and any text
        # source the injection displaced.
        result["style_injection"] = dict(self.last_style_injection)
        # Warnings (e.g. a node storing extra frontend-only widget state) are
        # surfaced but must not stop a run.
        result["binding_blocking"] = [
            problem.to_dict()
            for problem in self.last_binding_problems
            if problem.severity == SEVERITY_BLOCKING or problem.workflow_level
        ]
        # A workbench value that is out of range or names a non-existent parameter
        # is blocking: it means the parameter would silently not take effect.
        param_blocking = [p for p in self.last_param_problems if p.workflow_level]
        result["param_blocking"] = [p.to_dict() for p in param_blocking]
        result["ready"] = not result["blocking"] and not result["binding_blocking"] and not param_blocking
        return result

    @staticmethod
    def _is_api_graph(value: dict[str, Any]) -> bool:
        return bool(value) and "nodes" not in value and all(isinstance(item, dict) and "class_type" in item for item in value.values())

    @staticmethod
    def _node_ids(graph: dict[str, Any], class_type: str) -> list[str]:
        return [node_id for node_id, node in graph.items() if node.get("class_type") == class_type]

    @staticmethod
    def _style_inputs(config: BatchConfig) -> dict[str, str]:
        if config.styles:
            first = next((item for item in config.styles if str(item.get("catalog") or "").strip() and str(item.get("name") or "").strip()), None)
            if first:
                return {"styles": str(first["catalog"]).strip(), "select_styles": str(first["name"]).strip()}
        return {"styles": config.style_library, "select_styles": config.style_name}

    def _override_api_graph(self, graph: dict[str, Any], prompt_text: str, config: BatchConfig, output_prefix: str, task_negative: str = "", source_image: str = "") -> None:
        width, height = resolve_image_dimensions(config.aspect_ratio, config.megapixels)
        overrides = {
            "UNETLoader": {"unet_name": config.model},
            "CheckpointLoaderSimple": {"ckpt_name": config.model},
            "ResolutionSelector": {"aspect_ratio": config.aspect_ratio, "megapixels": config.megapixels},
            "SaveImage": {"filename_prefix": output_prefix},
        }
        for class_type, values in overrides.items():
            ids = self._node_ids(graph, class_type)
            if ids:
                graph[ids[0]].setdefault("inputs", {}).update(values)
        for node_id in self._node_ids(graph, "EmptyLatentImage"):
            inputs = graph[node_id].setdefault("inputs", {})
            if not isinstance(inputs.get("width"), list):
                inputs["width"] = width
            if not isinstance(inputs.get("height"), list):
                inputs["height"] = height
        if source_image:
            image_ids = self._node_ids(graph, "LoadImage")
            if image_ids:
                graph[image_ids[0]].setdefault("inputs", {})["image"] = source_image
        self._apply_prompt(graph, prompt_text, PromptCompiler.compile_negative(task_negative, config))
        if config.styles and config.style_application == "native":
            if not self._apply_native_styles(graph, prompt_text, config.styles)["injected"]:
                # docs/15 §6.2 方案A 回退档：白名单锚点不满足 → 风格模板编译进
                # 提示词，并绕开作者的风格组合器（与"提示词融合"同语义，避免双重风格）。
                merged = PromptCompiler.apply_style_templates(prompt_text, config.styles)
                self._apply_prompt(graph, merged, "")
                self._bypass_style_nodes(graph)
        elif any(str(item.get("prompt") or "").strip() for item in config.styles):
            self._bypass_style_nodes(graph)
        elif config.style_name or config.styles:
            ids = self._node_ids(graph, "easy stylesSelector")
            if ids:
                graph[ids[0]].setdefault("inputs", {}).update(self._style_inputs(config))

    def _convert_ui_graph(self, prompt_text: str, config: BatchConfig, output_prefix: str, task_negative: str = "", source_image: str = "") -> dict[str, Any]:
        nodes = {str(node["id"]): node for node in self.workflow.get("nodes", [])}
        links = {int(link[0]): [str(link[1]), int(link[2])] for link in self.workflow.get("links", [])}
        ui_only_types = {"Label (rgthree)", "Note", "Fast Groups Bypasser (rgthree)"}
        selected_ids = self._selected_ui_node_ids(config)

        def preserve_ui_bypass(node: dict[str, Any]) -> bool:
            """Keep optional resource loaders bypassed even when forcing an output branch.

            Output variants intentionally activate samplers/save nodes from an inactive UI
            group, but a bypassed LoRA is usually optional (and may not exist locally).
            """
            return int(node.get("mode", 0) or 0) == 4 and str(node.get("type") or "") in {
                "LoraLoader", "LoraLoaderModelOnly",
            }

        def resolve_bypassed_link(value: list[Any]) -> list[Any]:
            resolved = list(value)
            visited: set[str] = set()
            while resolved and str(resolved[0]) in nodes:
                source_id, output_slot = str(resolved[0]), int(resolved[1])
                source = nodes[source_id]
                if (
                    int(source.get("mode", 0) or 0) != 4
                    or (selected_ids is not None and source_id in selected_ids and not preserve_ui_bypass(source))
                    or source_id in visited
                ):
                    break
                visited.add(source_id)
                source_type = str(source.get("type") or "")
                preferred = {
                    "LoraLoaderModelOnly": {0: "model"},
                    "LoraLoader": {0: "model", 1: "clip"},
                }.get(source_type, {}).get(output_slot)
                source_inputs = list(source.get("inputs") or [])
                candidates = [item for item in source_inputs if item.get("link") is not None]
                if preferred:
                    candidates = [item for item in candidates if str(item.get("name")) == preferred]
                elif source.get("outputs") and output_slot < len(source["outputs"]):
                    output_type = str(source["outputs"][output_slot].get("type") or "")
                    typed = [item for item in candidates if str(item.get("type") or "") == output_type]
                    if typed:
                        candidates = typed
                if not candidates:
                    break
                upstream_id = candidates[0].get("link")
                if upstream_id is None or int(upstream_id) not in links:
                    break
                resolved = list(links[int(upstream_id)])
            return [str(resolved[0]), int(resolved[1])]

        target_width, target_height = resolve_image_dimensions(config.aspect_ratio, config.megapixels)
        registry = self._schema()
        style_inputs = self._style_inputs(config)
        graph: dict[str, Any] = {}
        self.last_sources = {}
        self.last_binding_problems = []
        self.last_param_problems = []
        for node_id, node in nodes.items():
            node_type = str(node.get("type"))
            if node_type in ui_only_types or (
                selected_ids is None and int(node.get("mode", 0) or 0) != 0
            ) or (selected_ids is not None and node_id not in selected_ids) or preserve_ui_bypass(node):
                continue
            linked: dict[str, Any] = {}
            for item in node.get("inputs") or []:
                link_id = item.get("link")
                if link_id is not None and int(link_id) in links:
                    linked[str(item["name"])] = resolve_bypassed_link(links[int(link_id)])
            inputs, sources, problems = convert_node(
                node_id,
                node,
                linked,
                config=config,
                target_size=(target_width, target_height),
                output_prefix=output_prefix,
                prompt_text=prompt_text,
                source_image=source_image,
                registry=registry,
                style_inputs=style_inputs,
                resource_overrides=(config.resource_overrides or {}).get(str(node_id)),
            )
            self.last_binding_problems.extend(problems)
            self.last_sources[node_id] = sources
            graph[node_id] = {"class_type": node_type, "inputs": inputs}

        save_ids = self._node_ids(graph, "SaveImage")
        if save_ids:
            reachable = set(save_ids)
            pending = list(save_ids)
            while pending:
                current = pending.pop()
                for value in graph[current].get("inputs", {}).values():
                    if isinstance(value, list) and len(value) == 2:
                        source_id = str(value[0])
                        if source_id in graph and source_id not in reachable:
                            reachable.add(source_id)
                            pending.append(source_id)
            graph = {node_id: node for node_id, node in graph.items() if node_id in reachable}
        self._apply_prompt(graph, prompt_text, PromptCompiler.compile_negative(task_negative, config))
        if config.styles and config.style_application == "native":
            if not self._apply_native_styles(graph, prompt_text, config.styles)["injected"]:
                # docs/15 §6.2 方案A 回退档：白名单锚点不满足 → 编译进提示词
                # 并绕开作者的风格组合器（与"提示词融合"同语义）。
                merged = PromptCompiler.apply_style_templates(prompt_text, config.styles)
                self._apply_prompt(graph, merged, "")
                self._bypass_style_nodes(graph)
        elif any(str(item.get("prompt") or "").strip() for item in config.styles):
            self._bypass_style_nodes(graph)
        return graph

    @staticmethod
    def _validate_graph_links(graph: dict[str, Any]) -> None:
        dangling = [
            (str(node_id), str(name), str(value[0]))
            for node_id, node in graph.items()
            for name, value in (node.get("inputs") or {}).items()
            if isinstance(value, list) and len(value) == 2 and str(value[0]) not in graph
        ]
        if dangling:
            details = "、".join(f"节点 {node}.{name} → 缺失节点 {source}" for node, name, source in dangling[:8])
            raise ValueError("工作流转换后存在悬空连接，当前分支含未兼容或错误绕过的节点：" + details)

    @classmethod
    def _conditioning_encoder(cls, graph: dict[str, Any], input_name: str) -> str | None:
        sampler_ids = cls._node_ids(graph, "KSampler") + cls._node_ids(graph, "KSamplerAdvanced")
        if not sampler_ids:
            return None
        link = graph[sampler_ids[0]].get("inputs", {}).get(input_name)
        if isinstance(link, list) and link:
            node_id = str(link[0])
            if graph.get(node_id, {}).get("class_type") == "CLIPTextEncode":
                return node_id
        return None

    @classmethod
    def _fallback_clip_sources(cls, graph: dict[str, Any]) -> list[tuple[str, list[Any]]]:
        """Every CLIP source the graph offers, nearest-first, deterministically.

        docs/15 §6.1 方案A: borrowing used to look only at the sampler's
        positive encoder. When that fails -- the positive runs through a style
        chain, or the workflow never had a real encoder -- any other
        ``CLIPTextEncode`` clip input or a loader's own CLIP output can feed
        the rebuilt negative encoder just as well, as long as the audit says
        so. Ordered by numeric node id so the same workflow always picks the
        same source; ties between kinds keep encoder inputs ahead of loader
        outputs because an encoder's clip is by construction wired to the
        model the author intended.
        """
        def order(node_id: str) -> tuple[int, int]:
            return (0, int(node_id)) if str(node_id).isdigit() else (1, 0)

        candidates: list[tuple[str, list[Any]]] = []
        for node_id in sorted((str(key) for key in graph), key=order):
            node = graph[node_id]
            class_type = str(node.get("class_type") or "")
            if class_type == "CLIPTextEncode":
                link = (node.get("inputs") or {}).get("clip")
                if isinstance(link, list) and link:
                    candidates.append((f"CLIPTextEncode {node_id} 的 CLIP 输入", link))
            elif class_type == "CLIPLoader":
                candidates.append((f"CLIPLoader {node_id} 的 CLIP 输出", [node_id, 0]))
            elif class_type == "CheckpointLoaderSimple":
                candidates.append((f"CheckpointLoaderSimple {node_id} 的 CLIP 输出", [node_id, 1]))
        return candidates

    #: Conditioning nodes that stand in for a real negative encoder. When one of
    #: these is wired into a sampler's ``negative`` input, filling in a negative
    #: prompt *replaces* the workflow author's own handling -- which changes what
    #: the sampler actually receives, so the user has to be told.
    NEGATIVE_SURROGATE_TYPES = frozenset({
        "ConditioningZeroOut",
        "ConditioningSetArea",
        "ConditioningSetAreaPercentage",
        "ConditioningSetMask",
        "ConditioningCombine",
        "ConditioningConcat",
        "ConditioningAverage",
        "ConditioningSetTimestepRange",
    })

    #: docs/15 §6.2 方案A：风格注入白名单。每个条目自带三件事——
    #: ① ``class_type``（键）；② 锚点（插在哪个输入口）；③ 谁消费它的输出（重指哪个下游口）。
    #: easy stylesSelector 是字符串级组合器（文本进、文本出），所以锚点契约收敛为一个形状：
    #: 采样器 positive 直连 CLIPTextEncode，组合器链输出汇回该编码器的 text 输入口——
    #: 正面链其余部分不动。锚点不满足时绝不猜测插桩位置，按方案A回退为提示词编译。
    STYLE_INJECTION_WHITELIST: dict[str, dict[str, Any]] = {
        "easy stylesSelector": {
            "host_types": ("KSampler", "KSamplerAdvanced"),
            "anchor_port": "positive",
            "anchor_node": "CLIPTextEncode",
            "insert_port": "text",
            "chain_input_port": "positive",
            "output_index": 0,
        },
    }

    @classmethod
    def _style_anchor_hosts(cls, workflow: dict[str, Any]) -> dict[str, str]:
        """docs/15 §6.2 方案A：在原始图上评估白名单锚点契约（API 图与 UI 图通用）。

        返回 ``{采样器 id: 正面编码器 id}``——只有正面口按契约直连
        ``CLIPTextEncode`` 的采样器才在列。空字典表示没有任何安全锚点，
        此时不允许猜测插桩位置（``docs/01`` 的教训），只能走回退档。
        """
        entry = cls.STYLE_INJECTION_WHITELIST["easy stylesSelector"]
        is_api = cls._is_api_graph(workflow)
        if is_api:
            pairs = [(str(node_id), node) for node_id, node in workflow.items()]
        else:
            pairs = [(str(node.get("id")), node) for node in workflow.get("nodes", [])]
        type_key = "class_type" if is_api else "type"
        by_id = dict(pairs)
        hosts: dict[str, str] = {}
        for sampler_id, node in pairs:
            if str(node.get(type_key) or "") not in entry["host_types"]:
                continue
            inputs = node.get("inputs") or {}
            if isinstance(inputs, list):
                # UI graphs store inputs as a list of {"name", "link"} entries.
                link = next(
                    (item.get("link") for item in inputs
                     if isinstance(item, dict) and item.get("name") == entry["anchor_port"]),
                    None,
                )
            else:
                link = inputs.get(entry["anchor_port"])
            if is_api:
                target_id = str(link[0]) if isinstance(link, list) and link else ""
            else:
                target_id = ""
                if isinstance(link, int):
                    for link_entry in workflow.get("links", []):
                        if isinstance(link_entry, list) and link_entry and link_entry[0] == link:
                            target_id = str(link_entry[1])
                            break
            target = by_id.get(target_id)
            if target and str(target.get(type_key) or "") == entry["anchor_node"]:
                hosts[sampler_id] = target_id
        return hosts

    @classmethod
    def _positive_target_type(cls, graph: dict[str, Any], sampler_id: str) -> str:
        """What currently feeds a sampler's ``positive`` port (API-shaped graph)."""
        link = graph.get(sampler_id, {}).get("inputs", {}).get("positive")
        if isinstance(link, list) and link:
            return str(graph.get(str(link[0]), {}).get("class_type") or "未知节点")
        return "未连接"

    def _negative_anchor_types(self, workflow: dict[str, Any], config: BatchConfig) -> list[str]:
        """Node types currently feeding every sampler's ``negative`` input.

        Only types other than ``CLIPTextEncode`` are returned: those are the ones
        a negative prompt would displace. Works on both API and UI graphs, and
        honours the selected UI variant so a bypassed branch is not reported.
        """
        is_api = self._is_api_graph(workflow)
        if is_api:
            pairs = [(str(node_id), node) for node_id, node in workflow.items()]
        else:
            selected_ids = self._selected_ui_node_ids(config)
            pairs = [
                (str(node.get("id")), node) for node in workflow.get("nodes", [])
                if (str(node.get("id")) in selected_ids if selected_ids is not None
                    else int(node.get("mode", 0) or 0) == 0)
            ]
        type_key = "class_type" if is_api else "type"
        by_id = dict(pairs)
        anchors: list[str] = []
        for _, node in pairs:
            if str(node.get(type_key) or "") not in {"KSampler", "KSamplerAdvanced"}:
                continue
            inputs = node.get("inputs") or {}
            if isinstance(inputs, list):
                # UI graphs store inputs as a list of {"name", "link"} entries.
                link = next(
                    (item.get("link") for item in inputs
                     if isinstance(item, dict) and item.get("name") == "negative"),
                    None,
                )
            else:
                link = inputs.get("negative")
            if is_api:
                target_id = str(link[0]) if isinstance(link, list) and link else ""
            else:
                target_id = ""
                if isinstance(link, int):
                    for entry in workflow.get("links", []):
                        if isinstance(entry, list) and entry and entry[0] == link:
                            target_id = str(entry[1])
                            break
            target = by_id.get(target_id)
            if not target:
                continue
            target_type = str(target.get(type_key) or "")
            if target_type and target_type != "CLIPTextEncode" and target_type not in anchors:
                anchors.append(target_type)
        return anchors

    def _apply_native_styles(self, graph: dict[str, Any], prompt_text: str, styles: list[dict[str, Any]]) -> dict[str, Any]:
        """docs/15 §6.2 方案A：按白名单锚点契约注入原生风格组合器。

        锚点（白名单②）＝采样器 positive 直连的 CLIPTextEncode；消费口（③）
        ＝该编码器的 text 输入口。锚点不满足时不再抛错——返回 ``injected: False``
        的台账，由调用方回退为提示词编译；绝不猜测插桩位置。返回值即注入台账，
        同时落入 :attr:`last_style_injection` 供审计引用。
        """
        cls = type(self)
        entry = cls.STYLE_INJECTION_WHITELIST["easy stylesSelector"]
        selected = [
            (str(item.get("catalog") or "").strip(), str(item.get("name") or "").strip())
            for item in styles
            if str(item.get("catalog") or "").strip() and str(item.get("name") or "").strip()
        ]
        ledger: dict[str, Any] = {"mode": "", "injected": False, "hosts": {}, "unanchored": {}, "injected_nodes": [], "displaced": {}}
        if not selected:
            self.last_style_injection = ledger
            return ledger
        all_samplers = cls._node_ids(graph, "KSampler") + cls._node_ids(graph, "KSamplerAdvanced")
        ledger["hosts"] = cls._style_anchor_hosts(graph)
        ledger["unanchored"] = {
            sampler_id: cls._positive_target_type(graph, sampler_id)
            for sampler_id in all_samplers
            if sampler_id not in ledger["hosts"]
        }
        if not ledger["hosts"]:
            # 锚点契约不满足：不硬拒、不猜测。调用方按方案A回退为提示词编译。
            ledger["mode"] = "编译回退（原生锚点不满足）"
            self.last_style_injection = ledger
            return ledger
        numeric_ids = [int(node_id) for node_id in graph if str(node_id).isdigit()]
        next_id = max([1999, *numeric_ids]) + 1
        primitive_id = str(next_id)
        next_id += 1
        graph[primitive_id] = {"class_type": "PrimitiveStringMultiline", "inputs": {"value": prompt_text}}
        current: list[Any] = [primitive_id, entry["output_index"]]
        chain_ids: list[str] = []
        for catalog, name in selected:
            node_id = str(next_id)
            next_id += 1
            graph[node_id] = {
                "class_type": "easy stylesSelector",
                "inputs": {entry["chain_input_port"]: current, "styles": catalog, "select_styles": name},
            }
            current = [node_id, entry["output_index"]]
            chain_ids.append(node_id)
        for encoder_id in set(ledger["hosts"].values()):
            encoder_inputs = graph[encoder_id].setdefault("inputs", {})
            old_text = encoder_inputs.get(entry["insert_port"])
            if isinstance(old_text, list) and old_text:
                displaced_id = str(old_text[0])
                if displaced_id != primitive_id and displaced_id not in chain_ids:
                    # 注入挤掉了编码器 text 口原有的连线来源（如作者自带的风格组合器）。
                    # 该节点仍在图中（可能与别处相连），但这条消费关系没了，必须记账。
                    ledger["displaced"][displaced_id] = str(graph.get(displaced_id, {}).get("class_type") or "未知节点")
            encoder_inputs[entry["insert_port"]] = copy.deepcopy(current)
        ledger["injected"] = True
        ledger["mode"] = "原生组合器"
        ledger["injected_nodes"] = [primitive_id, *chain_ids]
        # 精确记账：只有本次创建的节点标记为软件注入（API 图与 UI 图一致）。
        for node_id in ledger["injected_nodes"]:
            self.last_sources[node_id] = {
                name: SOURCE_INJECTED for name in (graph[node_id].get("inputs") or {})
            }
        self.last_style_injection = ledger
        return ledger

    def _apply_prompt(self, graph: dict[str, Any], prompt_text: str, negative_text: str) -> None:
        cls = type(self)
        primitive_ids = cls._node_ids(graph, "PrimitiveStringMultiline")
        if primitive_ids:
            graph[primitive_ids[0]].setdefault("inputs", {})["value"] = prompt_text
        else:
            positive_id = cls._conditioning_encoder(graph, "positive")
            if positive_id:
                graph[positive_id].setdefault("inputs", {})["text"] = prompt_text
        if not negative_text:
            return
        # Both sampler classes must be covered. Iterating only "KSampler" meant a
        # two-pass workflow (which uses KSamplerAdvanced) silently dropped the
        # negative prompt, and the capabilities check passed because a
        # CLIPTextEncode node did exist.
        sampler_ids = cls._node_ids(graph, "KSampler") + cls._node_ids(graph, "KSamplerAdvanced")
        updated_encoders: set[str] = set()
        # Every sampler that needs a negative encoder gets the *same* node when
        # the text and CLIP source match. They used to each get a private copy,
        # which inflated the graph and left the user with several identical
        # nodes and no way to tell which pass a later edit would affect.
        injected_by_key: dict[tuple[str, str], str] = {}
        for sampler_id in sampler_ids:
            sampler_inputs = graph[sampler_id].setdefault("inputs", {})
            negative_link = sampler_inputs.get("negative")
            if not isinstance(negative_link, list) or not negative_link:
                raise ValueError(f"{graph[sampler_id].get('class_type', '采样器')} {sampler_id} 没有负面条件输入，无法应用负面提示词")
            negative_id = str(negative_link[0])
            if graph.get(negative_id, {}).get("class_type") == "CLIPTextEncode":
                if negative_id not in updated_encoders:
                    current = str(graph[negative_id].setdefault("inputs", {}).get("text", "")).strip()
                    graph[negative_id]["inputs"]["text"] = PromptCompiler.compile_negative(
                        negative_text, BatchConfig("", "", negative_prompt=current)
                    )
                    updated_encoders.add(negative_id)
                continue
            positive_link = sampler_inputs.get("positive")
            positive_id = str(positive_link[0]) if isinstance(positive_link, list) and positive_link else ""
            positive_node = graph.get(positive_id, {})
            clip_link = positive_node.get("inputs", {}).get("clip") if positive_node.get("class_type") == "CLIPTextEncode" else None
            clip_origin = ""
            if not (isinstance(clip_link, list) and clip_link):
                # docs/15 §6.1 方案A: the positive encoder could not lend a
                # CLIP (it is not an encoder, or its clip input is unwired).
                # Fall back to any other CLIP source in the graph -- the audit
                # records which one, so a surprising picture has a visible
                # cause.
                for origin, candidate in cls._fallback_clip_sources(graph):
                    clip_link = candidate
                    clip_origin = origin
                    break
            if clip_link is None:
                # docs/15 §6.1 方案C: the graph has no CLIP source at all.
                # Keep the author's own wiring -- ConditioningZeroOut and its
                # kin mean "no negative guidance", which is exactly an empty
                # negative -- and say so, instead of refusing to run or
                # silently dropping the text. The preflight already announced
                # this; the audit carries the per-sampler evidence.
                self.last_negative_skipped.setdefault(
                    sampler_id, str(graph.get(negative_id, {}).get("class_type") or "未连接")
                )
                continue
            key = (negative_text, json.dumps(clip_link, sort_keys=True))
            injected_id = injected_by_key.get(key)
            if injected_id is None:
                numeric_ids = [int(node_id) for node_id in graph if str(node_id).isdigit()]
                injected_id = str(max([0, *numeric_ids]) + 1)
                while injected_id in graph:
                    injected_id = str(int(injected_id) + 1)
                graph[injected_id] = {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"clip": copy.deepcopy(clip_link), "text": negative_text},
                }
                injected_by_key[key] = injected_id
            if clip_origin:
                self.last_negative_clip_fallback.setdefault(sampler_id, clip_origin)
            # The replaced conditioning node keeps its own upstream links but
            # loses every consumer. ComfyUI will not execute it, so leaving it in
            # place is harmless -- and deleting it is *not*: a
            # ``Fast Groups Bypasser`` can re-activate that branch on the next
            # pass. Record it so the audit can mention it instead.
            orphan_type = str(graph.get(negative_id, {}).get("class_type") or "")
            if orphan_type in cls.NEGATIVE_SURROGATE_TYPES:
                self.last_orphaned_conditioning.setdefault(negative_id, orphan_type)
            sampler_inputs["negative"] = [injected_id, 0]

    @staticmethod
    def _bypass_style_nodes(graph: dict[str, Any]) -> None:
        style_ids = [node_id for node_id, node in graph.items() if node.get("class_type") == "easy stylesSelector"]
        for style_id in style_ids:
            source = graph[style_id].get("inputs", {}).get("positive")
            if not isinstance(source, list):
                continue
            for node_id, node in graph.items():
                if node_id == style_id:
                    continue
                for name, value in list((node.get("inputs") or {}).items()):
                    if value == [style_id, 0]:
                        node["inputs"][name] = copy.deepcopy(source)

    def _inject_loras(self, graph: dict[str, Any], loras: list[dict[str, Any]], sources: dict[str, dict[str, str]] | None = None) -> None:
        if not loras:
            return
        sources = sources if sources is not None else {}
        unet_ids = self._node_ids(graph, "UNETLoader")
        checkpoint_ids = self._node_ids(graph, "CheckpointLoaderSimple")
        original_ids = set(graph)
        numeric_ids = [int(node_id) for node_id in graph if str(node_id).isdigit()]
        next_id = max([999, *numeric_ids]) + 1

        def record(node_id: str, node: dict[str, Any]) -> None:
            sources[node_id] = {name: SOURCE_INJECTED for name in (node.get("inputs") or {})}

        if unet_ids:
            base_model = [unet_ids[0], 0]
            current_model = base_model
            for value in loras:
                name = str(value.get("name") or "").strip()
                if not name:
                    continue
                node_id = str(next_id)
                next_id += 1
                graph[node_id] = {"class_type": "LoraLoaderModelOnly", "inputs": {
                    "model": current_model,
                    "lora_name": name,
                    "strength_model": float(value.get("strength", 1.0)),
                }}
                record(node_id, graph[node_id])
                current_model = [node_id, 0]
            self._replace_output_links(graph, original_ids, base_model, current_model)
            return

        if checkpoint_ids:
            base_model, base_clip = [checkpoint_ids[0], 0], [checkpoint_ids[0], 1]
            current_model, current_clip = base_model, base_clip
            for value in loras:
                name = str(value.get("name") or "").strip()
                if not name:
                    continue
                strength = float(value.get("strength", 1.0))
                node_id = str(next_id)
                next_id += 1
                graph[node_id] = {"class_type": "LoraLoader", "inputs": {
                    "model": current_model,
                    "clip": current_clip,
                    "lora_name": name,
                    "strength_model": strength,
                    "strength_clip": float(value.get("clip_strength", strength)),
                }}
                record(node_id, graph[node_id])
                current_model, current_clip = [node_id, 0], [node_id, 1]
            self._replace_output_links(graph, original_ids, base_model, current_model)
            self._replace_output_links(graph, original_ids, base_clip, current_clip)
            return
        raise ValueError("工作流缺少受支持的模型加载器，无法注入 LoRA")

    @staticmethod
    def _replace_output_links(
        graph: dict[str, Any], node_ids: set[str], source: list[Any], replacement: list[Any]
    ) -> None:
        for node_id in node_ids:
            for name, value in list((graph[node_id].get("inputs") or {}).items()):
                if value == source:
                    graph[node_id]["inputs"][name] = copy.deepcopy(replacement)


class ComfyClient:
    """Backwards-compatible facade over :class:`ProductionGateway`.

    ``json`` / ``output`` are kept because the whole existing suite and
    ``Application`` use them. The behaviour that changed is that failures now
    raise :class:`ComfyError` carrying ComfyUI's full response body instead of a
    bare ``urllib`` ``HTTPError`` with the body discarded.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8188", gateway: Any = None):
        self.base_url = base_url.rstrip("/")
        self.gateway = gateway if gateway is not None else ProductionGateway(self.base_url)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def json(self, path: str, method: str = "GET", payload: dict[str, Any] | None = None, timeout: int = 30) -> Any:
        return self.gateway.request(path, method, payload, timeout)

    def system_stats(self) -> dict[str, Any]:
        return self.gateway.system_stats()

    def object_info(self, *, refresh: bool = False, timeout: float | None = None) -> dict[str, Any]:
        return self.gateway.object_info(refresh=refresh, timeout=timeout)

    def models(self, folder: str) -> list[str]:
        return self.gateway.models(folder)

    def all_models(self) -> dict[str, list[str]]:
        return self.gateway.all_models()

    def submit(self, graph: dict[str, Any], client_id: str, *, timeout: float | None = None) -> str:
        if timeout is None:
            return self.gateway.submit(graph, client_id)
        return self.gateway.submit(graph, client_id, timeout=timeout)

    def poll(self, prompt_id: str, *, timeout: float | None = None) -> dict[str, Any]:
        if timeout is None:
            return self.gateway.poll(prompt_id)
        return self.gateway.poll(prompt_id, timeout=timeout)

    def output(self, prompt_id: str) -> dict[str, Any] | None:
        """Legacy accessor: the first image, or ``None``."""
        images = (self.gateway.poll(prompt_id) or {}).get("images") or []
        return images[0] if images else None

    def interrupt(self) -> None:
        self.gateway.interrupt()


#: Statuses in which the queue still owns the slot. Nothing else may start a
#: batch, redo an item or reconfigure the runner while one of these is current.
#: ``segment`` belongs here precisely because a batch waiting at a segment
#: boundary is not finished -- letting a second batch start in that gap is the
#: bug this list exists to prevent.
ACTIVE_STATUSES = ("starting", "running", "paused", "segment")


class RunProgressStore:
    """Persists enough of a batch to continue it after the process went away.

    The runner keeps its state in memory, so a crash, a closed window or a
    machine that lost power used to mean the remaining items were simply gone.
    One JSON document per run records the bundle, the config and which indexes
    already finished, which is what makes 断点续跑 possible: everything the
    queue needs to pick the batch up again lives in this file.

    Written atomically (temp file + replace) because the file is rewritten after
    every item: a half-written snapshot would be worse than no snapshot at all.
    """

    def __init__(self, root: pathlib.Path):
        self.root = pathlib.Path(root)

    @staticmethod
    def validate_run_id(run_id: str) -> str:
        value = str(run_id or "").strip()
        if not value:
            raise ValueError("进度快照缺少 run_id")
        if not all(character.isalnum() or character in "-_" for character in value):
            raise ValueError("run_id 含有不允许的字符，拒绝访问进度快照")
        return value

    def path_for(self, run_id: str) -> pathlib.Path:
        safe_id = self.validate_run_id(run_id)
        return self.root / f"{safe_id}.json"

    def save(self, document: dict[str, Any]) -> pathlib.Path:
        run_id = str(document.get("run_id") or "").strip()
        run_id = self.validate_run_id(run_id)
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.path_for(run_id)
        # atomic_write_text uses a scratch name unique to this process, so a second
        # S running against the same data directory can no longer be writing the
        # very file this one is about to publish.
        atomic_write_text(target, json.dumps(document, ensure_ascii=False, indent=2))
        return target

    def load(self, run_id: str) -> dict[str, Any] | None:
        path = self.path_for(run_id)
        if not path.is_file():
            return None
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return document if isinstance(document, dict) else None

    def documents(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        rows: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json")):
            document = self.load(path.stem)
            if document is not None:
                rows.append(document)
        return rows

    @staticmethod
    def is_resumable(document: dict[str, Any] | None) -> bool:
        """A run is resumable while items remain, whatever stopped it."""
        if not document:
            return False
        try:
            selected = [int(value) for value in (document.get("selected_indexes") or [])]
            if selected:
                completed = {int(value) for value in (document.get("completed_indexes") or [])}
                return any(index not in completed for index in selected)
            return int(document.get("next_index") or 1) <= int(document.get("total") or 0)
        except (TypeError, ValueError):
            return False

    def latest(self, *, resumable_only: bool = False) -> dict[str, Any] | None:
        """Newest snapshot by ``updated_at``, falling back to file order."""
        rows = self.documents()
        if resumable_only:
            rows = [row for row in rows if self.is_resumable(row)]
        if not rows:
            return None
        return max(rows, key=lambda row: str(row.get("updated_at") or "").replace("T", " "))

    def summary(self, document: dict[str, Any] | None) -> dict[str, Any] | None:
        """Small payload for the page: enough to offer the resume button."""
        if not document or not self.is_resumable(document):
            return None
        return {
            "run_id": str(document.get("run_id") or ""),
            "total": int(document.get("total") or 0),
            "completed": len(document.get("completed_indexes") or []),
            "next_index": int(document.get("next_index") or 1),
            "status": str(document.get("status") or ""),
            "updated_at": str(document.get("updated_at") or ""),
        }

    def clear(self, run_id: str) -> None:
        path = self.path_for(run_id)
        if path.is_file():
            path.unlink()


class OpenAICompatibleAdapter:
    """Optional adapter; disabled by default and sends only prompt text when enabled."""

    def refine(self, items: list[PromptItem], config: dict[str, Any]) -> list[PromptItem]:
        if not config.get("enabled"):
            return items
        base_url = str(config.get("base_url") or "").rstrip("/")
        model = str(config.get("model") or "").strip()
        if not base_url or not model:
            raise ValueError("启用 LLM 时必须填写兼容接口地址和模型名")
        key = str(config.get("api_key") or "")
        system = str(config.get("instruction") or "在不改变原意的前提下，整理为可执行的二维服装图像提示词。只返回 JSON 数组，每项包含 title 和 prompt。")
        payload = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps([item.to_dict() for item in items], ensure_ascii=False)}], "temperature": float(config.get("temperature", 0.2))}
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        request = urllib.request.Request(base_url + "/chat/completions", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST", headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=180) as response:
            result = json.loads(response.read().decode("utf-8"))
        content = result["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.S)
        values = json.loads(content)
        if len(values) != len(items):
            raise ValueError("LLM 返回的提示词数量与输入不一致")
        return [PromptItem(
            str(value.get("title") or items[index].title),
            str(value["prompt"]),
            {**items[index].metadata, "llm_refined": True},
            str(value.get("negative_prompt") or items[index].negative_prompt),
        ) for index, value in enumerate(values)]


class BatchRunner:
    """Owns queue ordering, pause/resume/cancel, output copying and reports."""

    def __init__(self, comfy_root: pathlib.Path, client: ComfyClient | None = None, progress_store: "RunProgressStore | None" = None):
        self.comfy_root = pathlib.Path(comfy_root)
        self.client = client or ComfyClient()
        #: Where the per-item progress snapshot is written. ``None`` disables
        #: snapshots entirely, which is what the hermetic tests want.
        self.progress_store = progress_store
        self._lock = threading.RLock()
        self._resume = threading.Event()
        self._resume.set()
        self._cancel = threading.Event()
        #: Released by ``next_segment`` to carry the queue past a segment
        #: boundary. Cleared each time the queue arrives at one, so a stale
        #: release can never skip the next boundary.
        self._next_segment = threading.Event()
        self._state: dict[str, Any] = {
            "status": "idle", "run_id": None, "total": 0, "submitted": 0, "completed": 0,
            "errors": 0, "current": None, "results": [], "report": None,
            #: 0 means the whole batch runs in one go (the behaviour of every
            #: release before this one).
            "segment_size": 0,
            #: First index of the segment waiting for confirmation, or ``None``
            #: when the queue is not at a boundary.
            "next_index": None,
            #: Non-empty when writing the snapshot failed. Kept visible instead
            #: of swallowed: a batch whose progress cannot be saved must not
            #: pretend it can be resumed.
            "snapshot_error": "",
        }
        #: Kept so a single item can be re-run from the review page without the
        #: caller having to resend the whole bundle and config.
        self._last_bundle: PromptBundle | None = None
        self._last_config: BatchConfig | None = None
        #: Graphs from the last run keyed by item index, so a ``same_seed`` redo
        #: resubmits the identical graph (seed included) instead of recompiling
        #: with a fresh one.
        self._last_graphs: dict[int, dict[str, Any]] = {}
        #: Optional schema registry applied to every adapter this runner builds.
        self.adapter_registry: NodeSchemaRegistry | None = None
        #: Status to restore after a single-item redo finishes.
        self._redo_previous_status = ""
        #: Monotonic completion order for "use latest". Result rows are kept in
        #: task-number order for review, so their array position is not recency.
        self._result_revision = 0

    def _next_result_revision(self) -> int:
        with self._lock:
            self._result_revision += 1
            return self._result_revision

    def _adapter(self, workflow_path: str) -> "Krea2WorkflowAdapter":
        return Krea2WorkflowAdapter.from_path(workflow_path, self.adapter_registry or active_registry())

    @staticmethod
    def _original_index(item: PromptItem, position: int) -> int:
        metadata = item.metadata if isinstance(item.metadata, dict) else {}
        return int(metadata.get("original_index") or position)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state)

    def start(self, bundle: PromptBundle, config: BatchConfig, *, resume: dict[str, Any] | None = None) -> dict[str, Any]:
        """Begin a batch, or continue one from a saved progress snapshot.

        ``resume`` is a document produced by :meth:`snapshot`. When it is given
        the run keeps its original ``run_id`` -- so the output folder and the
        review history still line up -- and every index it already completed is
        skipped instead of being generated a second time.
        """
        with self._lock:
            if self._state["status"] in ACTIVE_STATUSES:
                raise ValueError("已有批次正在运行")
            run_id = str((resume or {}).get("run_id") or uuid.uuid4())
            segment_size = max(0, int(getattr(config, "segment_size", 0) or 0))
            restored = [copy.deepcopy(row) for row in ((resume or {}).get("results") or []) if isinstance(row, dict)]
            selected_indexes = [self._original_index(item, position) for position, item in enumerate(bundle.items, 1)]
            if len(set(selected_indexes)) != len(selected_indexes) or any(index < 1 for index in selected_indexes):
                raise ValueError("运行任务编号无效或重复")
            self._state = {
                "status": "starting",
                "run_id": run_id,
                "total": len(bundle.items),
                "submitted": len(restored),
                "completed": sum(1 for row in restored if row.get("status") == "completed"),
                "errors": sum(1 for row in restored if row.get("status") == "error"),
                "current": None,
                "results": restored,
                "report": None,
                "output_dir": config.output_dir,
                "segment_size": segment_size,
                "next_index": None,
                "snapshot_error": "",
                "selected_indexes": selected_indexes,
                "prompts_refined": bool((resume or {}).get("prompts_refined")),
                #: Where this run picked up from; 0 for a fresh batch. The page
                #: says so rather than presenting a resumed run as a new one.
                "resumed_from": int((resume or {}).get("next_index") or 1) if resume else 0,
            }
            self._result_revision = 0

        self._cancel.clear()
        self._resume.set()
        self._next_segment.clear()
        self._last_bundle = bundle
        self._last_config = config
        self._last_graphs = {}
        # Persist the intent before the worker starts. A crash before the first
        # image must still leave a resumable record rather than losing the run.
        self._write_snapshot("starting")
        threading.Thread(target=self._run, args=(run_id, bundle, config), daemon=True).start()
        return self.status()

    def redo(self, index: int, mode: str = "new_seed", *, prompt: str = "", note: str = "", seed: int | None = None) -> dict[str, Any]:
        """Re-run one item in the background, appending a new attempt.

        ``mode`` selects what is deliberately held constant:

        ``same_seed``      resubmit the identical compiled graph (same seed and
                           same parameters), which is how you check whether a
                           result was a one-off fluke.
        ``new_seed``       recompile from the stored config so seeds are fresh.
        ``edited_prompt``  recompile with a replacement prompt for this item.
        ``reupscale_only`` reuse the previous output as the input image of a
                           workflow that accepts one.

        Passing ``seed`` pins every concrete seed in the rebuilt graph to that
        value, which is how you reproduce one specific image or deliberately vary
        only the seed while keeping every other parameter fixed.
        """
        if mode not in REDO_MODES:
            raise ValueError(f"不支持的重做方式：{mode}")
        # Claim the slot inside the same lock as the check: releasing between the
        # two would let a batch start in the gap and corrupt the state.
        with self._lock:
            if self._state["status"] in ACTIVE_STATUSES:
                raise ValueError("已有批次正在运行，无法重做单条任务")
            bundle, config = self._last_bundle, self._last_config
            if bundle is None or config is None:
                raise ValueError("还没有可重做的批次，请先运行一次")

            item = next((candidate for position, candidate in enumerate(bundle.items, 1)
                         if self._original_index(candidate, position) == int(index)), None)
            if item is None:
                raise ValueError(f"批次中没有第 {index} 条任务")

            if prompt.strip():
                item = PromptItem(item.title, prompt.strip(), dict(item.metadata), item.negative_prompt)

            previous_graph = self._last_graphs.get(int(index))
            if mode == "same_seed" and previous_graph is None:
                raise ValueError("没有可用于同种子重做的历史提交记录")

            run_id = str(self._state.get("run_id") or "")
            # Remember the pre-redo status so the redo can restore it instead of
            # inventing one: a redo in review happens after the batch finished.
            self._redo_previous_status = str(self._state.get("status") or "")
            self._state["status"] = "starting"

            # A batch-level seed pin applies to the batch, not to a redo that
            # exists precisely to change the seed. Releasing it here is what makes
            # ``new_seed`` actually re-roll; ``same_seed`` reuses the stored graph
            # and so is unaffected either way.
            if mode != "same_seed":
                config = copy.deepcopy(config)
                config.seed = None

        threading.Thread(
            target=self._redo_one,
            args=(run_id, int(index), item, copy.deepcopy(config), mode, previous_graph, note, seed),
            daemon=True,
        ).start()
        return self.status()

    def redo_many(self, indexes: Any, mode: str = "new_seed", *, note: str = "", seed: int | None = None) -> dict[str, Any]:
        """Re-run several items in the background with one sequential worker.

        Unlike :meth:`redo`, which spawns one thread per call, the batch version
        validates every requested index inside a single lock acquisition and then
        runs the whole queue on exactly one background thread, item by item, so
        no ordinary batch can slip in between two redo items.
        """
        # 去重并保持调用方给出的顺序，编号统一转成 int。
        unique_indexes: list[int] = []
        for raw in indexes or []:
            value = int(raw)
            if value not in unique_indexes:
                unique_indexes.append(value)
        if not unique_indexes:
            raise ValueError("请提供要重做的任务编号")
        if mode not in REDO_MODES:
            raise ValueError(f"不支持的重做方式：{mode}")
        if mode == "edited_prompt":
            raise ValueError("批量重做不支持「改提示词」，请逐张打回")

        # Claim the slot inside the same lock as the check: releasing between the
        # two would let a batch start in the gap and corrupt the state. The batch
        # version validates every index in this one critical section.
        with self._lock:
            if self._state["status"] in ACTIVE_STATUSES:
                raise ValueError("已有批次正在运行，无法批量重做")
            bundle, config = self._last_bundle, self._last_config
            if bundle is None or config is None:
                raise ValueError("还没有可重做的批次，请先运行一次")

            tasks: list[tuple[int, PromptItem, dict[str, Any] | None]] = []
            for index in unique_indexes:
                item = next(
                    (candidate for position, candidate in enumerate(bundle.items, 1)
                     if self._original_index(candidate, position) == index),
                    None,
                )
                if item is None:
                    raise ValueError(f"批次中没有第 {index} 条任务")
                previous_graph = self._last_graphs.get(index) if mode == "same_seed" else None
                if mode == "same_seed" and previous_graph is None:
                    raise ValueError(f"第 {index} 条没有可用于同种子重做的历史提交记录")
                # 复制一份 item，避免后台线程与后续操作共享同一个 bundle 内对象。
                item = PromptItem(item.title, item.prompt, dict(item.metadata), item.negative_prompt)
                tasks.append((index, item, previous_graph))

            run_id = str(self._state.get("run_id") or "")
            # Remember the pre-redo status so the redo can restore it instead of
            # inventing one: a redo in review happens after the batch finished.
            self._redo_previous_status = str(self._state.get("status") or "")
            self._state["status"] = "starting"

            # A batch-level seed pin applies to the batch, not to a redo that
            # exists precisely to change the seed. Releasing it here is what makes
            # ``new_seed`` actually re-roll; ``same_seed`` reuses the stored graph
            # and so is unaffected either way. Done once for the whole queue.
            if mode != "same_seed":
                config = copy.deepcopy(config)
                config.seed = None

        threading.Thread(
            target=self._redo_many_one,
            args=(run_id, tasks, config, mode, note, seed),
            daemon=True,
        ).start()
        return self.status()

    def _redo_many_one(
        self,
        run_id: str,
        tasks: list[tuple[int, PromptItem, dict[str, Any] | None]],
        config: BatchConfig,
        mode: str,
        note: str,
        seed: int | None,
    ) -> None:
        """Run the queued redo items one by one inside a single worker thread."""
        total = len(tasks)
        # 状态全程保持 running：杜绝两张之间出现空窗让普通批次插进来。
        self._update(status="running")
        for position, (index, item, previous_graph) in enumerate(tasks, 1):
            self._update(current=f"批量重做 {position}/{total} · {index:03d}")
            # 单张失败不中断队列：_redo_one 内部已自行捕获并记录错误。
            self._redo_one(run_id, index, item, copy.deepcopy(config), mode, previous_graph, note, seed, keep_status=True)
        # Only restore the status when the queue still owns it. Unconditionally
        # writing "completed" here would clobber a batch that was still running,
        # which is the same conservative philosophy as the single redo.
        with self._lock:
            if self._state.get("status") == "running":
                values: dict[str, Any] = {
                    "status": self._redo_previous_status or "completed",
                    "current": None,
                }
                self._state.update(values)

    def _redo_one(
        self,
        run_id: str,
        index: int,
        item: PromptItem,
        config: BatchConfig,
        mode: str,
        previous_graph: dict[str, Any] | None,
        note: str,
        seed: int | None = None,
        keep_status: bool = False,
    ) -> None:
        result: dict[str, Any] = {"index": index, "title": _safe_name(item.title, f"item-{index:03d}"), "status": "error", "redo_mode": mode}
        try:
            adapter = self._adapter(config.workflow_path)
            compiled_prompt = PromptCompiler.compile(item.prompt, config)
            compiled_negative_prompt = PromptCompiler.compile_negative(item.negative_prompt, config)
            source_image = str(item.metadata.get("source_image") or "") if isinstance(item.metadata, dict) else ""
            stamp = time.strftime("%H%M%S")
            prefix = f"ComfyBatch-V2/{run_id[:8]}/{index:03d}-redo-{mode}-{stamp}"

            if mode == "same_seed" and previous_graph is not None:
                graph = copy.deepcopy(previous_graph)
                for node in graph.values():
                    if node.get("class_type") in {"SaveImage", "PreviewImage"}:
                        node.setdefault("inputs", {})["filename_prefix"] = prefix
            elif mode == "reupscale_only":
                # Only meaningful for workflows that take an input image. Rather
                # than guessing, ask the compiled capabilities.
                capabilities = adapter.capabilities(config, source_image=source_image)
                if not (capabilities.get("input_image") or {}).get("supported"):
                    raise ValueError("当前工作流没有输入图片节点，无法只重做放大。请改用「换种子重做」，或选择含输入图片的工作流。")
                graph = adapter.build(compiled_prompt, config, prefix, task_negative=item.negative_prompt, source_image=source_image)
            else:
                graph = adapter.build(compiled_prompt, config, prefix, task_negative=item.negative_prompt, source_image=source_image)

            if seed is not None:
                apply_seed(graph, int(seed))
            result["redo_seed"] = int(seed) if seed is not None else None
            self._last_graphs[index] = copy.deepcopy(graph)
            prompt_id = self.client.submit(graph, "comfybatch-v2-redo-" + run_id)
            result.update({"prompt_id": prompt_id, "status": "submitted", "prompt": item.prompt, "negative_prompt": item.negative_prompt,
                           "compiled_prompt": compiled_prompt, "compiled_negative_prompt": compiled_negative_prompt})
            deadline = time.time() + 1800
            outcome: dict[str, Any] = {"state": "pending", "images": []}
            started = time.time()
            while time.time() < deadline and not self._cancel.is_set():
                outcome = self._poll(prompt_id)
                if outcome["state"] in {"done", "error"}:
                    break
                time.sleep(2)
            if outcome["state"] == "error":
                problems = list(outcome.get("problems") or [])
                raise WorkflowFailure(problems or [Problem(category=ErrorCategory.EXECUTION_FAILED, title="重做失败", detail="ComfyUI 报告执行失败。")], prompt_id=prompt_id)
            image = (outcome.get("images") or [None])[0]
            if not image:
                raise TimeoutError("等待 ComfyUI 输出超时或任务已取消")
            source = resolve_output_source(self.comfy_root / "output", image)
            if not source.is_file():
                raise FileNotFoundError(
                    f"ComfyUI 报告了成图 {image.get('subfolder') or ''}/{image.get('filename') or ''}，"
                    f"但在 {source} 找不到文件。请确认输出目录设置正确。"
                )
            output_dir = pathlib.Path(config.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            # The image keeps its batch subfolder (``<run>/003-title.png``) but not
            # ComfyUI's own ``ComfyBatch-V2`` tier -- see ``copy_output_image``.
            target = copy_output_image(source, output_dir, image, exist_ok=True)
            quality = ImageQualityInspector.inspect(source) if config.single_subject_guard else {"repeated_panels": False}
            result.update({
                "status": "completed",
                "image": image,
                "copied_to": str(target),
                "elapsed": round(time.time() - started, 2),
                "quality": quality,
                "note": note,
                "completed_revision": self._next_result_revision(),
                "generation": {
                    "model": config.model, "styles": config.styles, "style_library": config.style_library,
                    "style_name": config.style_name, "loras": config.loras, "aspect_ratio": config.aspect_ratio,
                    "megapixels": config.megapixels, "source_image": source_image,
                    "workflow_path": config.workflow_path, "workflow_variant": config.workflow_variant,
                    "seeds": graph_seeds(graph),
                    **seed_evidence_payload(graph_seeds(graph), real_seed_evidence(source, outcome.get("entry"))),
                },
                "attempts": [{"attempt": 1, "prompt_id": prompt_id, "quality": quality, "elapsed": round(time.time() - started, 2), "mode": mode}],
            })
        except Exception as exc:
            problems = self._translate(exc)
            result["error"] = summarise_problems(problems)
            result["errors"] = [problem.to_dict() for problem in problems]
            result["error_category"] = problems[0].category if problems else ErrorCategory.UNKNOWN
        finally:
            with self._lock:
                merged = [entry for entry in (self._state.get("results") or []) if int(entry.get("index") or -1) != index]
                merged.append(result)
                merged.sort(key=lambda entry: int(entry.get("index") or 0))
                values: dict[str, Any] = {"results": merged}
                # Only restore the status when no batch owns it. Unconditionally
                # writing "completed" here clobbered a batch that was still
                # running, which is how a live run came to look finished.
                # 批量重做队列统一在 _redo_many_one 收尾时恢复状态，单张只合并结果。
                if not keep_status and self._state.get("status") not in {"running", "paused"}:
                    values["status"] = self._redo_previous_status or "completed"
                self._state.update(values)

    def pause(self) -> dict[str, Any]:
        """Stop releasing new items, but only while the queue is actually running.

        Doing this at a segment boundary used to clear the resume event without
        changing the status: the batch looked like it was waiting for 「下一段」,
        and then froze for good the moment that button was pressed. A no-op is
        the honest answer -- there is nothing to pause when nothing is moving.
        """
        with self._lock:
            if self._state["status"] != "running":
                return self.status()
            self._resume.clear()
            self._state["status"] = "paused"
        return self.status()

    def resume(self) -> dict[str, Any]:
        self._resume.set()
        with self._lock:
            if self._state["status"] == "paused":
                self._state["status"] = "running"
        return self.status()

    def cancel(self) -> dict[str, Any]:
        self._cancel.set()
        self._resume.set()
        try:
            self.client.interrupt()
        except Exception:
            pass
        return self.status()

    # ---- 分段生成与断点续跑 ------------------------------------------------

    def next_segment(self) -> dict[str, Any]:
        """Release the queue past the segment boundary it is waiting at.

        Refusing when nothing is waiting is the point: a stray click must not
        quietly arm the gate so that the *next* boundary is skipped.
        """
        with self._lock:
            if self._state.get("status") != "segment":
                raise ValueError("当前没有等待确认的分段")
            # Claim this one-shot release while still holding the same lock as
            # the status check. A concurrent second click now observes running.
            self._state.update(status="running", next_index=None)
            self._next_segment.set()
            return copy.deepcopy(self._state)

    def snapshot(self, status: str = "") -> dict[str, Any] | None:
        """The resumable document for the current run, or ``None`` if there is none.

        ``status`` overrides the live status so a caller can write the snapshot
        *before* it publishes that status -- which is what makes the file on disk
        always at least as new as what the page is showing.

        ``next_index`` is derived from the completed rows rather than from a
        counter, so it stays right when an item failed and the batch carried on.
        """
        with self._lock:
            state = copy.deepcopy(self._state)
            bundle, config = self._last_bundle, self._last_config
        run_id = str(state.get("run_id") or "")
        if not run_id or bundle is None or config is None:
            return None
        results = list(state.get("results") or [])
        completed = sorted({
            int(row["index"]) for row in results
            if isinstance(row.get("index"), int) and row.get("status") == "completed"
        })
        selected_indexes = [int(value) for value in (state.get("selected_indexes") or range(1, int(state.get("total") or 0) + 1))]
        completed_set = set(completed)
        next_index = next((index for index in selected_indexes if index not in completed_set), None)
        config_document = asdict(config)
        # Same rule as the run report: the key never leaves the process.
        if (config_document.get("llm") or {}).get("api_key"):
            config_document["llm"]["api_key"] = ""
        return {
            "run_id": run_id,
            "status": status or str(state.get("status") or ""),
            "updated_at": datetime.datetime.now().isoformat(sep=" ", timespec="microseconds"),
            "segment_size": int(state.get("segment_size") or 0),
            "total": int(state.get("total") or 0),
            "completed_indexes": completed,
            "selected_indexes": selected_indexes,
            "prompts_refined": bool(state.get("prompts_refined")),
            "next_index": next_index if next_index is not None else (max(selected_indexes, default=0) + 1),
            "output_dir": str(state.get("output_dir") or ""),
            "bundle": bundle.to_dict(),
            "config": config_document,
            "results": results,
        }

    def last_bundle(self) -> "PromptBundle | None":
        """The prompt collection the current (or most recent) run was built from.

        Public because resuming has to hand the collection back to the page, and
        letting the application layer read ``_last_bundle`` directly would make
        that a private detail with a public caller.
        """
        return self._last_bundle

    def resumable(self) -> dict[str, Any] | None:
        """Summary of the newest unfinished run, for the page's resume prompt."""
        if self.progress_store is None:
            return None
        with self._lock:
            if self._state.get("status") in ACTIVE_STATUSES:
                # A batch that is still running owns its snapshot; offering to
                # "resume" it would invite a second queue into the same run.
                return None
        return self.progress_store.summary(self.progress_store.latest(resumable_only=True))

    def resume_interrupted(self, run_id: str = "") -> dict[str, Any]:
        """Continue the newest unfinished batch from its saved snapshot."""
        if self.progress_store is None:
            raise ValueError("本次启动没有开启进度快照，无法续跑")
        with self._lock:
            if self._state["status"] in ACTIVE_STATUSES:
                raise ValueError("已有批次正在运行，无法续跑")
        document = self.progress_store.load(run_id) if run_id else self.progress_store.latest(resumable_only=True)
        if not document:
            raise ValueError("没有可以续跑的批次")
        if not self.progress_store.is_resumable(document):
            raise ValueError("上一次批次已经全部完成，无需续跑")
        saved_bundle = document.get("bundle") or {}
        items = [
            PromptItem(
                title=str(row.get("title") or ""),
                prompt=str(row.get("prompt") or ""),
                metadata=dict(row.get("metadata") or {}),
                negative_prompt=str(row.get("negative_prompt") or ""),
            )
            for row in (saved_bundle.get("items") or [])
            if isinstance(row, dict)
        ]
        if not items:
            raise ValueError("进度快照里没有可用的提示词，无法续跑")
        bundle = PromptBundle(
            name=str(saved_bundle.get("name") or "续跑批次"),
            items=items,
            source_format=str(saved_bundle.get("source_format") or "resume"),
        )
        # The frozen config is reused verbatim: a resumed batch has to be the
        # batch the user confirmed, not a fresh resolution of the same paths.
        config = BatchConfig.from_dict(dict(document.get("config") or {}))
        return self.start(bundle, config, resume=document)

    def _wait_for_next_segment(self, index: int, segment_size: int) -> bool:
        """Hold the queue at a segment boundary until the user releases it.

        Returns ``False`` when the batch was cancelled while waiting. The status
        stays ``segment`` rather than ``paused`` so the page can tell "you paused
        me" from "I finished a segment and need a decision".
        """
        self._next_segment.clear()
        # 先落盘、再公布状态：页面一旦看到「等待下一段」，快照就已经在磁盘上，
        # 此刻断电也还能续跑；反过来就有个"状态已到、文件未到"的空窗。
        self._write_snapshot("segment")
        self._update(
            status="segment",
            next_index=index,
            current=f"本段已完成（每段 {segment_size} 条）· 待确认后从第 {index:03d} 条继续",
        )
        while not self._cancel.is_set() and not self._next_segment.wait(0.2):
            pass
        if self._cancel.is_set():
            return False
        self._update(status="running", next_index=None)
        return True

    def _write_snapshot(self, status: str = "") -> None:
        """Save the progress document. A failure is reported, never raised."""
        if self.progress_store is None:
            return
        document = self.snapshot(status)
        if document is None:
            return
        try:
            self.progress_store.save(document)
        except (OSError, ValueError) as exc:
            # Losing one snapshot must not kill an otherwise healthy batch, but
            # the page has to be able to say that resume is unavailable.
            self._update(snapshot_error=f"{type(exc).__name__}: {exc}")
        else:
            if self._state.get("snapshot_error"):
                self._update(snapshot_error="")

    def _update(self, **values: Any) -> None:
        with self._lock:
            self._state.update(values)

    def _poll(self, prompt_id: str) -> dict[str, Any]:
        """Normalised history probe.

        Accepts both the gateway contract (``poll``) and the legacy test-double
        contract (``output``) so existing fakes keep working unchanged.
        """
        if hasattr(self.client, "poll"):
            return self.client.poll(prompt_id) or {"state": "pending", "images": [], "problems": []}
        image = self.client.output(prompt_id)
        if image:
            return {"state": "done", "images": [image], "problems": []}
        return {"state": "pending", "images": [], "problems": []}

    def _translate(self, exc: Exception) -> list[Problem]:
        if isinstance(exc, ComfyError):
            return ErrorTranslator.translate(exc)
        problem = Problem(
            category=ErrorCategory.EXECUTION_FAILED,
            title="任务失败",
            detail=str(exc),
            batch_impact="该任务失败",
        )
        problem.fixes = ["展开原始信息查看完整报错"]
        return [problem]

    def _run(self, run_id: str, bundle: PromptBundle, config: BatchConfig) -> None:
        output_dir = pathlib.Path(config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        # A resumed batch inherits the rows it already produced, so the report
        # and the review page show one continuous run rather than just the tail.
        with self._lock:
            results: list[dict[str, Any]] = [copy.deepcopy(row) for row in (self._state.get("results") or [])]
        # Indexes that already produced an image. They are skipped, never
        # re-submitted: 断点续跑 must not regenerate what is already on disk.
        done_indexes = {
            int(row["index"]) for row in results
            if isinstance(row.get("index"), int) and row.get("status") == "completed"
        }
        completed_count = len(done_indexes)
        segment_size = max(0, int(getattr(config, "segment_size", 0) or 0))
        # 段按「本次运行跑了几条」计数，不按绝对编号。用绝对编号的话，续跑会
        # 立刻撞上中断前那道边界、白停一次；按计数则每次续跑都从新的一段开始，
        # 用户点一次「续跑」就真的往前跑一段。
        processed_in_segment = 0

        def record(row: dict[str, Any]) -> None:
            """Store one item's outcome, replacing any earlier attempt at it.

            A resumed batch re-runs the item that was in flight when it stopped,
            so the same index can legitimately be written twice. Keeping both
            rows would double-count it in the metrics and leave the failure
            displayed next to the success forever.
            """
            seat = next(
                (position for position, existing in enumerate(results) if existing.get("index") == row.get("index")),
                None,
            )
            if seat is None:
                results.append(row)
            else:
                results[seat] = row

        effective: dict[str, Any] = {}
        audit: dict[str, Any] = {}
        consecutive_failures = 0
        aborted_reason = ""

        try:
            # A range-selected run carries only the selected items, so expensive
            # local refinement and graph compilation never touch excluded tasks.
            if self._state.get("prompts_refined"):
                items = bundle.items
            else:
                items = OpenAICompatibleAdapter().refine(bundle.items, config.llm)
                bundle = PromptBundle(name=bundle.name, items=items, source_format=bundle.source_format)
                self._last_bundle = bundle
                self._update(prompts_refined=True)
                self._write_snapshot("running")
            adapter = self._adapter(config.workflow_path)
            first_source_image = next((str(item.metadata.get("source_image") or "") for item in items if isinstance(item.metadata, dict) and item.metadata.get("source_image")), "")
            effective = adapter.capabilities(config, source_image=first_source_image)
            self._update(status="running")
            submitted_count = len({int(row.get("index") or 0) for row in results if row.get("index")})
            for position, item in enumerate(items, 1):
                index = self._original_index(item, position)
                if index in done_indexes:
                    continue
                if self._cancel.is_set():
                    break
                # 分段闸门：跑满一段就先停下等人确认。闸门放在「开始下一条之前」，
                # 所以一段正好是 segment_size 条，不多不少。
                if segment_size and processed_in_segment >= segment_size:
                    if not self._wait_for_next_segment(index, segment_size):
                        break
                    processed_in_segment = 0
                self._resume.wait()
                if self._cancel.is_set():
                    break
                processed_in_segment += 1
                title = _safe_name(item.title, f"item-{index:03d}")
                preset_name = str(item.metadata.get("style_lora_preset_name") or "").strip() if isinstance(item.metadata, dict) else ""
                file_title = _safe_name(f"{title}--{preset_name}", title) if preset_name else title
                current_suffix = f" · {preset_name}" if preset_name else ""
                self._update(current=f"{index:03d} {title}{current_suffix}", submitted=submitted_count)
                result: dict[str, Any] = {"index": index, "title": title, "preset_name": preset_name, "status": "error"}
                try:
                    item_config = copy.deepcopy(config)
                    generation = item.metadata.get("generation") if isinstance(item.metadata, dict) else None
                    if isinstance(generation, dict):
                        item_config.model = str(generation.get("model") or item_config.model)
                        item_config.style_library = str(generation.get("style_library") or item_config.style_library)
                        item_config.style_name = str(generation.get("style_name") or item_config.style_name)
                        if isinstance(generation.get("styles"), list):
                            item_config.styles = copy.deepcopy(generation["styles"])
                        item_config.aspect_ratio = str(generation.get("aspect_ratio") or item_config.aspect_ratio)
                        item_config.megapixels = max(0.25, min(4.0, float(generation.get("megapixels") or item_config.megapixels)))
                        if isinstance(generation.get("loras"), list):
                            item_config.loras = copy.deepcopy(generation["loras"])
                        if isinstance(generation.get("params"), dict):
                            # Task-level workbench values win over batch ones.
                            item_config.params = {**item_config.params, **generation["params"]}
                    compiled_prompt = PromptCompiler.compile(item.prompt, item_config)
                    compiled_negative_prompt = PromptCompiler.compile_negative(item.negative_prompt, item_config)
                    source_image = str(item.metadata.get("source_image") or "") if isinstance(item.metadata, dict) else ""
                    attempts: list[dict[str, Any]] = []
                    repeated_panels = False
                    for attempt in range(item_config.max_retries + 1):
                        retry_suffix = "" if attempt == 0 else f"-retry-{attempt}"
                        prefix = f"ComfyBatch-V2/{run_id[:8]}/{index:03d}-{file_title}{retry_suffix}"
                        graph = adapter.build(compiled_prompt, item_config, prefix, task_negative=item.negative_prompt, source_image=source_image)
                        # Retained for same-seed redos from the review page.
                        self._last_graphs[index] = copy.deepcopy(graph)
                        if not audit:
                            # The graph is identical for every item except the seed
                            # and filename, so auditing the first submission is
                            # enough to prove page-to-graph equivalence.
                            audit = adapter.audit(item_config, branch=str(item_config.workflow_variant or ""))
                        started = time.time()
                        prompt_id = self.client.submit(graph, "comfybatch-v2-" + run_id)
                        result.update({"prompt_id": prompt_id, "status": "submitted", "generation": {
                            "model": item_config.model,
                            "styles": item_config.styles,
                            "style_library": item_config.style_library,
                            "style_name": item_config.style_name,
                            "loras": item_config.loras,
                            "aspect_ratio": item_config.aspect_ratio,
                            "megapixels": item_config.megapixels,
                            "source_image": source_image,
                            "workflow_path": item_config.workflow_path,
                            "workflow_variant": item_config.workflow_variant,
                            "seeds": graph_seeds(graph),
                        }})
                        deadline = time.time() + 1800
                        outcome: dict[str, Any] = {"state": "pending", "images": []}
                        while time.time() < deadline and not self._cancel.is_set():
                            outcome = self._poll(prompt_id)
                            # An execution error is reported through history. The
                            # old code could not tell this apart from "running"
                            # and burned the full 30-minute deadline per item.
                            if outcome["state"] in {"done", "error"}:
                                break
                            time.sleep(2)
                        if outcome["state"] == "error":
                            problems = list(outcome.get("problems") or [])
                            raise WorkflowFailure(problems or [Problem(
                                category=ErrorCategory.EXECUTION_FAILED,
                                title="执行的节点报错",
                                detail="ComfyUI 报告该任务执行失败，但未提供更详细的原因。",
                            )], prompt_id=prompt_id)
                        image = (outcome.get("images") or [None])[0]
                        if not image:
                            raise TimeoutError("等待 ComfyUI 输出超时或任务已取消")
                        source = resolve_output_source(self.comfy_root / "output", image)
                        if not source.is_file():
                            raise FileNotFoundError(
                                f"ComfyUI 报告了成图 {image.get('subfolder') or ''}/{image.get('filename') or ''}，"
                                f"但在 {source} 找不到文件。请确认输出目录设置正确。"
                            )
                        quality = ImageQualityInspector.inspect(source) if item_config.single_subject_guard else {"repeated_panels": False}
                        # 这张图真正用的种子。以成图内嵌的提示为准，其次执行历史；
                        # 两处都读不到就记「未核对」，绝不拿提交值冒充实际值。
                        result["generation"].update(seed_evidence_payload(
                            graph_seeds(graph), real_seed_evidence(source, outcome.get("entry")),
                        ))
                        repeated_panels = bool(quality.get("repeated_panels"))
                        attempts.append({"attempt": attempt + 1, "prompt_id": prompt_id, "quality": quality, "elapsed": round(time.time() - started, 2)})
                        if repeated_panels and attempt < item_config.max_retries:
                            continue
                        target = copy_output_image(source, output_dir, image, exist_ok=True)
                        result.update({
                            "status": "completed",
                            "image": image,
                            "copied_to": str(target),
                            "elapsed": round(time.time() - started, 2),
                            "prompt": item.prompt,
                            "negative_prompt": item.negative_prompt,
                            "compiled_prompt": compiled_prompt,
                            "compiled_negative_prompt": compiled_negative_prompt,
                            "metadata": item.metadata,
                            "quality": quality,
                            "attempts": attempts,
                            "review_status": "待确认",
                            "completed_revision": self._next_result_revision(),
                        })
                        break
                    completed_count += 1
                    consecutive_failures = 0
                    self._update(completed=self._state["completed"] + 1)
                except WorkflowFailure as exc:
                    problems = exc.problems
                    result["error"] = summarise_problems(problems)
                    result["errors"] = [problem.to_dict() for problem in problems]
                    result["error_category"] = problems[0].category if problems else ErrorCategory.UNKNOWN
                    self._update(errors=self._state["errors"] + 1)
                    if any(problem.workflow_level for problem in problems):
                        # Workflow-level defects are identical for every item, so
                        # submitting the rest would just repeat the same failure.
                        aborted_reason = summarise_problems(problems)
                        record(result)
                        self._update(results=copy.deepcopy(results), submitted=submitted_count + 1)
                        break
                    consecutive_failures += 1
                except Exception as exc:
                    problems = self._translate(exc)
                    result["error"] = summarise_problems(problems)
                    result["errors"] = [problem.to_dict() for problem in problems]
                    result["error_category"] = problems[0].category if problems else ErrorCategory.UNKNOWN
                    self._update(errors=self._state["errors"] + 1)
                    if any(problem.workflow_level for problem in problems):
                        aborted_reason = summarise_problems(problems)
                        record(result)
                        self._update(results=copy.deepcopy(results), submitted=submitted_count + 1)
                        break
                    consecutive_failures += 1
                if consecutive_failures >= max(1, int(config.max_consecutive_failures)):
                    aborted_reason = f"连续 {consecutive_failures} 条任务失败，已暂停批次以避免继续浪费算力。"
                record(result)
                submitted_count = len({int(row.get("index") or 0) for row in results if row.get("index")})
                self._update(results=copy.deepcopy(results), submitted=submitted_count)
                # 每条一存：中断可能发生在任何一条之后，快照必须一直是最新的。
                self._write_snapshot()
                if aborted_reason:
                    break
            if aborted_reason and not self._cancel.is_set():
                final_status = "aborted"
            else:
                final_status = "cancelled" if self._cancel.is_set() else "completed"
        except Exception as exc:
            problems = self._translate(exc)
            results.append({
                "status": "error",
                "error": summarise_problems(problems),
                "errors": [problem.to_dict() for problem in problems],
                "error_category": problems[0].category if problems else ErrorCategory.UNKNOWN,
            })
            self._update(errors=self._state["errors"] + 1)
            final_status = "error"
            aborted_reason = aborted_reason or summarise_problems(problems)
        # 统计本批各图片种子的出现频次：同一种子反复高频出现往往意味着种子
        # 参数没有真正生效，这里把「防止单一图片大量产出」的事后可见性直接
        # 写进批次报告，便于用户排查。
        seed_counts: dict[str, int] = {}
        real_counts: dict[str, int] = {}
        unverified: list[int] = []
        mismatched: list[dict[str, Any]] = []
        for entry in results:
            generation = entry.get("generation") or {}
            seeds = generation.get("seeds") or {}
            # graph_seeds 返回的是 {节点.参数: 种子值} 字典，必须取值而不是取键。
            seed_values = seeds.values() if isinstance(seeds, dict) else seeds
            for seed_value in seed_values:
                key = str(seed_value)
                seed_counts[key] = seed_counts.get(key, 0) + 1
            check = generation.get("seed_check") or {}
            if not check:
                continue
            index = int(entry.get("index") or 0)
            if not check.get("available"):
                # A completed image whose seed we could not read back is not a
                # failure and must not be counted as one -- but it is also not
                # confirmation, so it is listed separately.
                if entry.get("status") == "completed":
                    unverified.append(index)
                continue
            real = generation.get("real_seeds") or {}
            for seed_value in (real.values() if isinstance(real, dict) else real):
                key = str(seed_value)
                real_counts[key] = real_counts.get(key, 0) + 1
            if not check.get("effective"):
                mismatched.append({
                    "index": index,
                    "source": generation.get("seed_source") or "",
                    "detail": check.get("mismatched") or {},
                    "missing": check.get("missing") or [],
                })
        seed_stats = {
            "values": seed_counts,
            "duplicates": {key: count for key, count in seed_counts.items() if count > 1},
            # 实际生效的种子：同一实际种子重复出现＝必然同图，比提交值更可靠。
            "real_values": real_counts,
            "real_duplicates": {key: count for key, count in real_counts.items() if count > 1},
            "mismatched": mismatched,
            "unverified_indexes": unverified,
        }
        report = {
            **self.status(),
            "status": final_status,
            "results": results,
            "bundle": bundle.to_dict(),
            "config": asdict(config),
            "effective": effective,
            "audit": audit,
            "aborted_reason": aborted_reason,
            "completed_count": completed_count,
            "seed_stats": seed_stats,
        }
        if report.get("config", {}).get("llm", {}).get("api_key"):
            report["config"]["llm"]["api_key"] = ""
        report_path = output_dir / f"comfybatch-v2-{run_id[:8]}-report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        # 收尾快照先落盘、再公布最终状态。全部跑完时 next_index 会超过 total，于是
        # 它自然从「可续跑」列表里消失，不需要额外清理；顺序反过来就会有一瞬
        # "页面说跑完了、快照却还说能续跑"。
        self._update(next_index=None)
        self._write_snapshot(final_status)
        self._update(status=final_status, current=None, results=results, report=str(report_path), aborted_reason=aborted_reason, audit=audit)


REVIEW_STATUSES = ("待确认", "已通过", "需重做", "已替换")
REDO_MODES = ("same_seed", "new_seed", "edited_prompt", "reupscale_only")
#: Longest edge of a cached preview, in pixels. Large enough to look sharp in the
#: review board's card, small enough that a whole batch is a few hundred KB.
THUMBNAIL_SIZE = 512


class ResultReviewStore:
    """Persists per-image review state so confirmations survive a restart.

    Stored as one JSON document per run, written atomically, in the application
    data directory rather than the output directory so that moving or clearing
    the output folder does not lose the review history.
    """

    def __init__(self, root: pathlib.Path):
        self.root = pathlib.Path(root)

    def path_for(self, run_id: str) -> pathlib.Path:
        return self.root / f"{run_id}.json"

    @property
    def thumbnail_root(self) -> pathlib.Path:
        """Where cached preview JPEGs live: beside the review document.

        Deliberately not in the output folder. The point of the cache is that the
        preview survives the user moving or clearing their output directory, the
        same reason the review document itself lives here.
        """
        return self.root / "thumbs"

    def ensure_thumbnail(self, source: pathlib.Path | str, size: int = 0, quality: int = 82) -> pathlib.Path:
        """Build (once) and return a downscaled preview for ``source``.

        Why a cache instead of resizing on every request
        ------------------------------------------------
        The review board used to point every ``<img>`` straight at the full-size
        PNG, so opening a 20-image batch decoded and shipped ~30 MB, and every
        re-render during a run did it again. The cache is keyed by the source
        file's own path, size and mtime, so a redone image produces a new entry
        automatically instead of serving a stale preview.

        Returns the source itself when a preview cannot be made (Pillow missing a
        decoder, an unreadable file), so a preview failure can never turn into a
        missing image on the page.
        """
        source = pathlib.Path(source)
        if size <= 0:
            size = THUMBNAIL_SIZE
        try:
            stat = source.stat()
        except OSError:
            return source
        key = hashlib.sha1(f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{size}".encode("utf-8")).hexdigest()[:20]
        target = self.thumbnail_root / f"{key}.jpg"
        if target.is_file() and target.stat().st_size:
            return target
        try:
            self.thumbnail_root.mkdir(parents=True, exist_ok=True)
            with Image.open(source) as image:
                image = image.convert("RGB") if image.mode not in {"RGB", "L"} else image
                image.thumbnail((size, size), Image.LANCZOS)
                temporary = unique_temp_for(target)
                image.save(temporary, "JPEG", quality=quality, optimize=True)
            temporary.replace(target)
        except (OSError, ValueError):
            return source
        return target

    def latest_run_id(self) -> str:
        """Most recent run that has a persisted review document, if any.

        Used after a restart, when the runner's in-memory state is gone but the
        confirmation decisions are still on disk and must stay visible.
        """
        if not self.root.exists():
            return ""
        newest, newest_mtime = "", -1.0
        for path in self.root.glob("*.json"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime > newest_mtime:
                newest, newest_mtime = path.stem, mtime
        return newest

    def load(self, run_id: str) -> dict[str, Any]:
        path = self.path_for(run_id)
        if not run_id or not path.exists():
            return {"run_id": run_id, "items": {}}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"run_id": run_id, "items": {}}
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), dict):
            return {"run_id": run_id, "items": {}}
        return payload

    def save(self, run_id: str, document: dict[str, Any]) -> None:
        if not run_id:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        document = {**document, "run_id": run_id, "updated_at": _timestamp()}
        path = self.path_for(run_id)
        # The lock keeps the replace from interleaving with another process's
        # read-modify-write. It does *not* make this whole-document write safe on its
        # own: a caller that read the document first must hold the lock across its read,
        # which ``ingest`` and ``confirm`` do below.
        with FileLock(path):
            atomic_write_text(path, json.dumps(document, ensure_ascii=False, indent=2))

    def ingest(self, run_id: str, results: list[dict[str, Any]]) -> dict[str, Any]:
        """Merge a run's results into the review document, preserving decisions.

        The whole read-merge-write runs under the file lock: two windows can both be
        looking at the same run (both may resume the same interrupted batch), and a
        merge computed from a stale read would drop whichever decision landed second.

        A redo produces a *fresh* single-entry ``attempts`` list rather than a
        grown one, so "the attempt count increased" is not a sufficient signal.
        ``redo_mode`` is authoritative: it marks the result as a new version of an
        existing item, which is what moves the old version into ``history`` and
        makes the new one the active version.
        """
        with FileLock(self.path_for(run_id)):
            return self._ingest_locked(run_id, results)

    def _ingest_locked(self, run_id: str, results: list[dict[str, Any]]) -> dict[str, Any]:
        document = self.load(run_id)
        items: dict[str, Any] = document.setdefault("items", {})
        replaced = False
        for result in results:
            key = str(result.get("index") or "")
            if not key:
                continue
            existing = items.get(key) or {}
            attempts = list(result.get("attempts") or [])
            previous_attempts = list(existing.get("attempts") or [])
            previous_count = len(previous_attempts)

            # ``ingest`` runs on every poll, so re-ingesting the *same* result must
            # not count as a new version. Without this guard each poll appended
            # another history entry, and the attempt count grew without bound
            # (observed: attempts 162, history 161 for a single redo).
            latest_prompt_id = str((previous_attempts[-1] if previous_attempts else {}).get("prompt_id") or "")
            incoming_prompt_id = str((attempts[0] if attempts else {}).get("prompt_id") or "") or str(result.get("prompt_id") or "")
            already_recorded = bool(incoming_prompt_id) and incoming_prompt_id == latest_prompt_id
            is_new_version = (not already_recorded) and (
                bool(result.get("redo_mode")) or len(attempts) > previous_count
            )

            entry = {
                **existing,
                "index": result.get("index"),
                "title": result.get("title") or existing.get("title") or "",
                "preset_name": result.get("preset_name") or existing.get("preset_name") or "",
                "status": result.get("status"),
                "error": result.get("error") or "",
                "errors": result.get("errors") or existing.get("errors") or [],
                "error_category": result.get("error_category") or "",
                "copied_to": result.get("copied_to") or existing.get("copied_to") or "",
                "image": result.get("image") or existing.get("image") or {},
                "generation": result.get("generation") or existing.get("generation") or {},
                "prompt": result.get("prompt") or existing.get("prompt") or "",
                "negative_prompt": result.get("negative_prompt") or existing.get("negative_prompt") or "",
                "compiled_prompt": result.get("compiled_prompt") or existing.get("compiled_prompt") or "",
                "quality": result.get("quality") or existing.get("quality") or {},
                "elapsed": result.get("elapsed") if result.get("elapsed") is not None else existing.get("elapsed"),
                # Attempts accumulate across versions, so the count reflects how
                # many times the item has actually been generated.
                # A re-ingest of an already-recorded result must never shrink the
                # list back to the single attempt the result carries.
                "attempts": previous_attempts + attempts if is_new_version else (previous_attempts or attempts),
                "review_status": existing.get("review_status") or "待确认",
                "note": existing.get("note") or "",
            }
            if is_new_version and previous_count:
                # A new attempt supersedes the previous decision: the old image
                # is kept in ``history`` but is no longer the active version.
                history = list(existing.get("history") or [])
                if existing.get("copied_to"):
                    history.append({
                        "copied_to": existing.get("copied_to"),
                        "image": existing.get("image"),
                        "review_status": existing.get("review_status"),
                        "note": existing.get("note"),
                        "attempts": previous_count,
                    })
                entry["history"] = history
                entry["review_status"] = "已替换"
                replaced = True
            items[key] = entry
        document["items"] = items
        document["replaced"] = bool(document.get("replaced")) or replaced
        self.save(run_id, document)
        return document

    def confirm(self, run_id: str, indexes: list[int], status: str, note: str = "") -> dict[str, Any]:
        if status not in REVIEW_STATUSES:
            raise ValueError(f"不支持的确认状态：{status}")
        with FileLock(self.path_for(run_id)):
            return self._confirm_locked(run_id, indexes, status, note)

    def _confirm_locked(self, run_id: str, indexes: list[int], status: str,
                        note: str = "") -> dict[str, Any]:
        # Held across read and write for the same reason as ``_ingest_locked``.
        document = self.load(run_id)
        items: dict[str, Any] = document.get("items") or {}
        if not items:
            raise ValueError("当前批次还没有可确认的成图")
        applied: list[int] = []
        for index in indexes:
            entry = items.get(str(index))
            if entry is None:
                continue
            entry["review_status"] = status
            if note:
                entry["note"] = note
            entry["reviewed_at"] = _timestamp()
            applied.append(int(index))
        if not applied:
            raise ValueError("未找到要确认的任务编号")
        self.save(run_id, document)
        return document

    def list_for_run(self, run_id: str, run_dir: pathlib.Path | None = None) -> list[dict[str, Any]]:
        """Current valid version of every item, newest attempt first in ``history``."""
        document = self.load(run_id)
        items = list((document.get("items") or {}).values())
        for entry in items:
            copied = str(entry.get("copied_to") or "")
            path = pathlib.Path(copied) if copied else None
            available = bool(copied and path.is_file())
            entry["available"] = available
            # The board shows a preview per item. Serving the full-resolution PNG
            # for that made a completed batch unusable: every scroll and every
            # poll re-fetched megabytes. ``thumbnail`` stays the full-size path
            # (the viewer needs it); the preview is a separate, cached file.
            if available and path is not None:
                preview = self.ensure_thumbnail(path)
                entry["thumbnail"] = copied
                entry["preview"] = str(preview) if preview != path else ""
            else:
                entry["thumbnail"] = ""
                entry["preview"] = ""
            self._annotate_dimensions(entry, path if available else None)
        items.sort(key=lambda item: int(item.get("index") or 0))
        return items

    @staticmethod
    def _annotate_dimensions(entry: dict[str, Any], path: pathlib.Path | None) -> None:
        """Add original size, final size and the upscale factor.

        The plan requires each result to show 原始尺寸、最终尺寸与放大倍数. The final
        size comes from the file header (PIL reads only the header, so this stays
        cheap); the original comes from the item's own aspect ratio and
        megapixels, which is what the workflow was configured to start from.
        """
        generation = entry.get("generation") or {}
        if isinstance(generation, dict) and generation.get("aspect_ratio"):
            try:
                width, height = resolve_image_dimensions(
                    str(generation.get("aspect_ratio")), float(generation.get("megapixels") or 1.0)
                )
                entry["base_dimensions"] = {"width": width, "height": height}
            except (TypeError, ValueError):
                pass
        if path is None:
            return
        try:
            with Image.open(path) as image:
                width, height = image.size
        except (OSError, ValueError):
            return
        entry["final_dimensions"] = {"width": width, "height": height}
        base = entry.get("base_dimensions") or {}
        if base.get("width"):
            entry["upscale_factor"] = round(width / float(base["width"]), 2)

    def summary(self, run_id: str) -> dict[str, Any]:
        items = self.list_for_run(run_id)
        counts: dict[str, int] = {status: 0 for status in REVIEW_STATUSES}
        for entry in items:
            status = str(entry.get("review_status") or "待确认")
            counts[status] = counts.get(status, 0) + 1
        return {"total": len(items), "by_status": counts}


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class ResourceOverrideStore:
    """Persistent, user-approved resource replacements.

    Keyed by ``workflow fingerprint + node id + input name``. The fingerprint is
    part of the key on purpose: if the workflow file changes, a rule saved
    against the old structure must not silently keep applying, because the node
    numbering may now point somewhere else.

    Nothing here ever substitutes on its own. A rule only exists because the user
    chose a candidate in the software, and the compiled graph tags every replaced
    input as ``user_replaced`` so the audit always shows it.
    """

    def __init__(self, root: pathlib.Path):
        self.root = pathlib.Path(root)

    @property
    def path(self) -> pathlib.Path:
        return self.root / "resource_rules.json"

    @staticmethod
    def key(fingerprint: str, node_id: str, input_name: str) -> str:
        return f"{fingerprint}:{node_id}:{input_name}"

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"rules": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"rules": {}}
        if not isinstance(payload, dict) or not isinstance(payload.get("rules"), dict):
            return {"rules": {}}
        return payload

    def save(self, document: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        document = {**document, "updated_at": _timestamp()}
        # Shared file, read-modify-write at every call site: take the lock so a second
        # process cannot slip a change in between this write and its own read.
        with FileLock(self.path):
            atomic_write_text(self.path, json.dumps(document, ensure_ascii=False, indent=2))

    @with_file_lock()
    def remember(
        self,
        fingerprint: str,
        node_id: str,
        input_name: str,
        value: str,
        *,
        node_type: str = "",
        note: str = "",
        workflow_path: str = "",
    ) -> dict[str, Any]:
        document = self.load()
        document["rules"][self.key(fingerprint, node_id, input_name)] = {
            "workflow_fingerprint": fingerprint,
            "workflow_path": str(workflow_path),
            "node_id": str(node_id),
            "input_name": str(input_name),
            "value": str(value),
            "node_type": node_type,
            "note": note,
            "created_at": _timestamp(),
        }
        self.save(document)
        return document

    def stale_for(self, workflow_path: str, fingerprint: str) -> list[dict[str, Any]]:
        """Rules made for this same file under a *different* fingerprint.

        Surfaced as a warning rather than applied: the workflow changed, so the
        node numbering a rule refers to may no longer mean the same thing. Silently
        dropping them was worse -- the user would see the preflight suddenly block
        again with no explanation.
        """
        if not workflow_path:
            return []
        stale = []
        for rule in (self.load().get("rules") or {}).values():
            if not isinstance(rule, dict):
                continue
            if str(rule.get("workflow_path") or "") != str(workflow_path):
                continue
            if str(rule.get("workflow_fingerprint") or "") == fingerprint:
                continue
            stale.append(rule)
        return stale

    @with_file_lock()
    def reapply(self, workflow_path: str, fingerprint: str) -> dict[str, Any]:
        """Re-key rules made for this file onto the current fingerprint."""
        document = self.load()
        moves = self.stale_for(workflow_path, fingerprint)
        for rule in moves:
            old_key = self.key(str(rule.get("workflow_fingerprint") or ""), str(rule.get("node_id") or ""), str(rule.get("input_name") or ""))
            document["rules"].pop(old_key, None)
            rule = {**rule, "workflow_fingerprint": fingerprint, "reapplied_at": _timestamp()}
            document["rules"][self.key(fingerprint, str(rule.get("node_id") or ""), str(rule.get("input_name") or ""))] = rule
        self.save(document)
        return document

    @with_file_lock()
    def forget(self, fingerprint: str, node_id: str, input_name: str) -> dict[str, Any]:
        document = self.load()
        document["rules"].pop(self.key(fingerprint, node_id, input_name), None)
        self.save(document)
        return document

    def clear(self) -> dict[str, Any]:
        document: dict[str, Any] = {"rules": {}}
        self.save(document)
        return document

    def for_fingerprint(self, fingerprint: str) -> dict[str, dict[str, str]]:
        """``{node_id: {input_name: value}}`` for rules matching this workflow."""
        grouped: dict[str, dict[str, str]] = {}
        for rule in (self.load().get("rules") or {}).values():
            if not isinstance(rule, dict) or rule.get("workflow_fingerprint") != fingerprint:
                continue
            grouped.setdefault(str(rule.get("node_id") or ""), {})[str(rule.get("input_name") or "")] = str(rule.get("value") or "")
        return grouped

    def rules_for_fingerprint(self, fingerprint: str) -> list[dict[str, Any]]:
        return [
            rule for rule in (self.load().get("rules") or {}).values()
            if isinstance(rule, dict) and rule.get("workflow_fingerprint") == fingerprint
        ]


class WorkflowParamsStore:
    """Workflow-level default parameter values, keyed by workflow fingerprint.

    This is the "工作流默认值" scope: values that should apply every time this
    workflow is run, without having to set them again. Kept separate from the
    batch overrides so the page can show both and say which one is winning.

    Keyed by fingerprint for the same reason resource rules are: if the workflow
    changes structurally, a default saved against the old shape may now point at
    a different node, so it must not silently keep applying.
    """

    def __init__(self, root: pathlib.Path):
        self.root = pathlib.Path(root)

    @property
    def path(self) -> pathlib.Path:
        return self.root / "workflow_params.json"

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"workflows": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"workflows": {}}
        if not isinstance(payload, dict) or not isinstance(payload.get("workflows"), dict):
            return {"workflows": {}}
        return payload

    def save(self, document: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        document = {**document, "updated_at": _timestamp()}
        # Shared file, read-modify-write at every call site: take the lock so a second
        # process cannot slip a change in between this write and its own read.
        with FileLock(self.path):
            atomic_write_text(self.path, json.dumps(document, ensure_ascii=False, indent=2))

    def for_fingerprint(self, fingerprint: str) -> dict[str, Any]:
        if not fingerprint:
            return {}
        entry = (self.load().get("workflows") or {}).get(fingerprint)
        if not isinstance(entry, dict):
            return {}
        values = entry.get("params")
        return dict(values) if isinstance(values, dict) else {}

    @with_file_lock()
    def remember(self, fingerprint: str, workflow_path: str, values: dict[str, Any]) -> dict[str, Any]:
        document = self.load()
        existing = self.for_fingerprint(fingerprint)
        merged = {**existing, **values}
        document.setdefault("workflows", {})[fingerprint] = {
            "workflow_path": str(workflow_path),
            "params": merged,
            "updated_at": _timestamp(),
        }
        self.save(document)
        return document

    @with_file_lock()
    def forget(self, fingerprint: str, keys: list[str] | None = None) -> dict[str, Any]:
        document = self.load()
        entry = (document.get("workflows") or {}).get(fingerprint)
        if isinstance(entry, dict):
            if not keys:
                document["workflows"].pop(fingerprint, None)
            else:
                params = entry.get("params") or {}
                for key in keys:
                    params.pop(key, None)
                entry["params"] = params
        self.save(document)
        return document

    def stale_for(self, workflow_path: str, fingerprint: str) -> list[dict[str, Any]]:
        """Saved defaults for this same file under a different fingerprint."""
        if not workflow_path:
            return []
        stale = []
        for saved_fingerprint, entry in (self.load().get("workflows") or {}).items():
            if not isinstance(entry, dict):
                continue
            if str(entry.get("workflow_path") or "") != str(workflow_path):
                continue
            if str(saved_fingerprint) == fingerprint:
                continue
            stale.append({"workflow_fingerprint": saved_fingerprint, "workflow_path": workflow_path,
                          "params": entry.get("params") or {}})
        return stale
