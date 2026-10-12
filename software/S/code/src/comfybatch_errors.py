"""Structured problems, error preservation and Chinese error translation.

This module is the base layer of the V2.14 work: it defines the vocabulary that
the compiler, the gateway and the batch orchestrator all share.

Why it exists
-------------
`ComfyClient.json` used to let `urllib` raise `HTTPError` without ever reading
`HTTPError.read()`, so ComfyUI's 400 body -- which contains the node id, the
offending value and the legal candidate list -- was discarded. That is why the
45-failure incident in the handover package could only report
``HTTP Error 400: Bad Request`` for every single item.

The translator is keyed on ComfyUI's own ``error.type`` /
``node_errors[*].errors[*].type`` strings rather than on message text, because
those type strings are a stable API while the prose is not.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


class ErrorCategory:
    """The ten categories the V2.14 plan calls for, plus an honest fallback."""

    MISSING_NODE = "missing_node"
    MISSING_RESOURCE = "missing_resource"
    MODEL_TYPE_MISMATCH = "model_type_mismatch"
    PARAMETER_MISALIGNED = "parameter_misaligned"
    VALUE_OUT_OF_RANGE = "value_out_of_range"
    DANGLING_LINK = "dangling_link"
    MISSING_INPUT = "missing_input"
    OUT_OF_MEMORY = "out_of_memory"
    EXECUTION_FAILED = "execution_failed"
    SAVE_FAILED = "save_failed"
    UNKNOWN = "unknown"


CATEGORY_TITLES = {
    ErrorCategory.MISSING_NODE: "缺少节点",
    ErrorCategory.MISSING_RESOURCE: "缺少资源文件",
    ErrorCategory.MODEL_TYPE_MISMATCH: "模型类型不兼容",
    ErrorCategory.PARAMETER_MISALIGNED: "参数错位",
    ErrorCategory.VALUE_OUT_OF_RANGE: "参数超出允许范围",
    ErrorCategory.DANGLING_LINK: "连线悬空",
    ErrorCategory.MISSING_INPUT: "缺少必需输入",
    ErrorCategory.OUT_OF_MEMORY: "显存不足",
    ErrorCategory.EXECUTION_FAILED: "执行失败",
    ErrorCategory.SAVE_FAILED: "保存失败",
    ErrorCategory.UNKNOWN: "未知错误",
}

#: Categories caused by the workflow itself rather than by one prompt. The batch
#: orchestrator stops the whole run on these instead of retrying all N items.
WORKFLOW_LEVEL = frozenset({
    ErrorCategory.MISSING_NODE,
    ErrorCategory.MISSING_RESOURCE,
    ErrorCategory.MODEL_TYPE_MISMATCH,
    ErrorCategory.PARAMETER_MISALIGNED,
    ErrorCategory.VALUE_OUT_OF_RANGE,
    ErrorCategory.DANGLING_LINK,
    ErrorCategory.MISSING_INPUT,
})

#: Input names that address a file on disk. A ``value_not_in_list`` on one of
#: these is a missing resource with a candidate list, not a range violation.
RESOURCE_INPUTS = {
    "ckpt_name": "基础模型",
    "unet_name": "扩散模型",
    "lora_name": "LoRA",
    "vae_name": "VAE",
    "clip_name": "CLIP",
    "clip_name1": "CLIP",
    "clip_name2": "CLIP",
    "clip_name3": "CLIP",
    "upscale_model_name": "放大模型",
    "model_name": "模型文件",
    "control_net_name": "ControlNet",
    "embedding_name": "Embedding",
    "style_model_name": "风格模型",
}

#: ``model_name`` is shared by several loaders with different meanings, so the
#: label is refined by node type when one is known. Anchored on the real
#: ``UpscaleModelLoader`` failure captured in
#: ``tests/fixtures/http400_missing_upscale_model.json``.
RESOURCE_LABELS_BY_NODE = {
    ("UpscaleModelLoader", "model_name"): "放大模型",
    ("SeedVR2LoadDiTModel", "model_name"): "SeedVR2 模型",
    ("SeedVR2LoadVAEModel", "model_name"): "SeedVR2 VAE",
    ("LoraLoader", "lora_name"): "LoRA",
    ("LoraLoaderModelOnly", "lora_name"): "LoRA",
}

SEVERITY_BLOCKING = "blocking"
SEVERITY_WARNING = "warning"


@dataclass
class Problem:
    """One structured, explainable defect.

    Rendered by the UI error drawer and asserted on by tests. ``fixes`` is
    ordered by preference; the UI offers the first as the primary action.
    """

    category: str
    title: str
    detail: str = ""
    node_id: str = ""
    node_type: str = ""
    node_title: str = ""
    input_name: str = ""
    received_value: Any = None
    candidates: list[Any] = field(default_factory=list)
    expected_range: str = ""
    batch_impact: str = ""
    fixes: list[str] = field(default_factory=list)
    severity: str = SEVERITY_BLOCKING
    raw: Any = None

    @property
    def workflow_level(self) -> bool:
        """Whether this defect should stop the whole batch.

        A warning is never batch-stopping, even when its category would
        otherwise be workflow-level: ``PARAMETER_MISALIGNED`` on a node that
        merely stores extra frontend state must not abort a run.
        """
        return self.severity == SEVERITY_BLOCKING and self.category in WORKFLOW_LEVEL

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "category": self.category,
            "title": self.title,
            "detail": self.detail,
            "severity": self.severity,
            "workflow_level": self.workflow_level,
        }
        for key in ("node_id", "node_type", "node_title", "input_name", "expected_range", "batch_impact"):
            value = getattr(self, key)
            if value:
                payload[key] = value
        if self.received_value is not None:
            payload["received_value"] = self.received_value
        if self.candidates:
            payload["candidates"] = self.candidates[:64]
        if self.fixes:
            payload["fixes"] = self.fixes
        if self.raw is not None:
            payload["raw"] = self.raw
        return payload

    def render(self) -> str:
        """Single-line Chinese summary, for toasts and report entries."""
        parts: list[str] = []
        # ``title`` is normally just the category label, so only show it once.
        if self.title and self.title != CATEGORY_TITLES.get(self.category):
            parts.append(f"[{CATEGORY_TITLES.get(self.category, self.category)}] {self.title}")
        else:
            parts.append(f"[{CATEGORY_TITLES.get(self.category, self.category)}]")
        if self.node_id:
            where = f"节点 {self.node_id}"
            if self.node_type:
                where += f"（{self.node_type}）"
            parts.append(where)
        if self.input_name:
            parts.append(f"参数 {self.input_name}")
        if self.received_value is not None:
            parts.append(f"当前值 {self.received_value!r}")
        if self.candidates:
            shown = "、".join(str(item) for item in self.candidates[:6])
            more = f" 等 {len(self.candidates)} 项" if len(self.candidates) > 6 else ""
            parts.append(f"可用候选 {shown}{more}")
        elif self.expected_range:
            parts.append(f"允许范围 {self.expected_range}")
        if self.detail:
            parts.append(self.detail)
        return "；".join(parts)


class ComfyError(RuntimeError):
    """A ComfyUI failure that kept its response body.

    ``status`` is 0 when the failure was not an HTTP status (e.g. a connection
    error or an execution error reported through history).
    """

    def __init__(self, message: str, *, status: int = 0, raw: str = "", body: Any = None, path: str = ""):
        super().__init__(message)
        self.status = status
        self.raw = raw
        self.body = body
        self.path = path

    @property
    def concise(self) -> str:
        """The old behaviour, kept for logs: never the whole body."""
        if self.status:
            return f"HTTP {self.status} {self.path}".strip()
        return str(self)


def _first(node_errors: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Return the first (node_id, node_error, error) triple, deterministically."""
    for node_id in sorted(node_errors, key=lambda value: (len(str(value)), str(value))):
        entry = node_errors[node_id]
        if not isinstance(entry, dict):
            continue
        errors = entry.get("errors") or []
        if errors and isinstance(errors[0], dict):
            return str(node_id), entry, errors[0]
    return "", {}, {}


def _candidates_from_config(input_config: Any) -> list[Any]:
    """Pull the legal candidate list out of ComfyUI's ``input_config``.

    Two shapes exist in the wild and both must work, because this list is what
    lets the UI offer a real replacement (``RealESRGAN_x4plus_anime_6B.pth`` for
    the missing ``OmniSR_X4_DIV2K.safetensors``) instead of only saying the value
    is wrong:

    * ComfyUI 0.33 and newer: ``["COMBO", {"options": [...]}]``
    * older builds: ``[[...candidates...], {...opts...}]``
    """
    if not isinstance(input_config, list) or not input_config:
        return []
    head = input_config[0]
    if isinstance(head, list):
        return list(head)
    if isinstance(head, str) and head == "COMBO" and len(input_config) > 1 and isinstance(input_config[1], dict):
        options = input_config[1].get("options")
        if isinstance(options, list):
            return list(options)
    if all(isinstance(item, str) for item in input_config):
        return list(input_config)
    return []


def _range_from_config(input_config: Any) -> str:
    if isinstance(input_config, list) and len(input_config) > 1 and isinstance(input_config[1], dict):
        options = input_config[1]
        low, high = options.get("min"), options.get("max")
        if low is not None or high is not None:
            return f"{low if low is not None else '-∞'} ~ {high if high is not None else '+∞'}"
    return ""


def resource_label(node_type: str, input_name: str) -> str:
    """Human label for the file a resource input points at."""
    return RESOURCE_LABELS_BY_NODE.get((node_type, input_name)) or RESOURCE_INPUTS.get(input_name, input_name)


_CANNOT_EXECUTE = re.compile(r"invalid class_type:\s*([^\s'\"]+)", re.I)
_MISSING_NODE_ID = re.compile(r"Node ID '#?([0-9]+)'", re.I)


class ErrorTranslator:
    """Turn a :class:`ComfyError` (or a history error payload) into Problems.

    ``candidate_resolver`` is an optional ``(class_type, input_name) -> list``
    hook. ComfyUI omits ``input_config`` when a combo has many candidates (it
    truncates to ``not in (list of length 44)`` in ``details``), so the live node
    schema is the only way to still offer real replacements.
    """

    @classmethod
    def translate(
        cls,
        error: ComfyError,
        *,
        candidate_resolver: Any = None,
        node_titles: dict[str, str] | None = None,
    ) -> list[Problem]:
        body = error.body if isinstance(error.body, dict) else None
        if body is None:
            return [cls._from_text(error)]

        problems: list[Problem] = []
        node_errors = body.get("node_errors")
        if isinstance(node_errors, dict) and node_errors:
            node_id, node_entry, node_error = _first(node_errors)
            if node_error:
                problems.append(cls._from_node_error(node_id, node_entry, node_error))
        top = body.get("error")
        if isinstance(top, dict) and top:
            problem = cls._from_top_error(top, already_reported=bool(problems))
            if problem is not None:
                problems.append(problem)
        if not problems:
            problems.append(cls._from_text(error))
        cls._fill_candidates(problems, candidate_resolver)
        # Recompute fixes AFTER candidates are filled: several fixes quote the
        # candidate list, so building them earlier would lose the very
        # replacement option that makes the error actionable.
        for problem in problems:
            problem.fixes = cls._fixes_for(problem.category, problem)
        cls._attach_titles(problems, node_titles or {})
        return cls._attach_context(problems, error)

    @classmethod
    def _fill_candidates(cls, problems: list[Problem], resolver: Any) -> None:
        if resolver is None:
            return
        for problem in problems:
            if problem.candidates or not problem.node_type or not problem.input_name:
                continue
            try:
                found = resolver(problem.node_type, problem.input_name)
            except Exception:  # noqa: BLE001 - the schema is best-effort context
                found = None
            if found:
                problem.candidates = list(found)

    @staticmethod
    def _attach_titles(problems: list[Problem], node_titles: dict[str, str]) -> None:
        for problem in problems:
            if problem.node_id and problem.node_id in node_titles:
                problem.node_title = node_titles[problem.node_id]

    @classmethod
    def from_history(cls, prompt_id: str, entry: dict[str, Any]) -> list[Problem]:
        """Translate the failure reported through ``/history/<id>``.

        ComfyUI reports runtime failures here rather than as an HTTP status, so
        this is the only place an OOM or an exception inside a node surfaces.
        """
        status = (entry or {}).get("status") or {}
        messages = status.get("messages") or []
        problems: list[Problem] = []
        for kind, payload in messages:
            if kind != "execution_error" or not isinstance(payload, dict):
                continue
            exception_type = str(payload.get("exception_type") or "")
            exception_message = str(payload.get("exception_message") or "").strip()
            node_id = str(payload.get("node_id") or "")
            node_type = str(payload.get("node_type") or "")
            category = ErrorCategory.EXECUTION_FAILED
            if "OutOfMemory" in exception_type or "out of memory" in exception_message.lower():
                category = ErrorCategory.OUT_OF_MEMORY
            elif "Saving" in node_type or "SaveImage" in node_type:
                category = ErrorCategory.SAVE_FAILED
            problem = Problem(
                category=category,
                title=CATEGORY_TITLES[category],
                detail=exception_message or exception_type,
                node_id=node_id,
                node_type=node_type,
                input_name="",
                received_value=None,
                batch_impact="该任务失败",
                raw=payload,
            )
            problem.fixes = cls._fixes_for(category, problem)
            problems.append(problem)
        return problems

    # ------------------------------------------------------------------ node

    @classmethod
    def _from_node_error(cls, node_id: str, node_entry: dict[str, Any], node_error: dict[str, Any]) -> Problem:
        event_type = str(node_error.get("type") or "")
        message = str(node_error.get("message") or "").strip()
        details = str(node_error.get("details") or "").strip()
        extra = node_error.get("extra_info") or {}
        node_type = str(node_entry.get("class_type") or extra.get("class_type") or "")
        input_name = str(extra.get("input_name") or "")
        received = extra.get("received_value")
        candidates = _candidates_from_config(extra.get("input_config"))
        expected_range = _range_from_config(extra.get("input_config"))

        category = cls._category_for(event_type, input_name, message, details)
        if category == ErrorCategory.MISSING_RESOURCE:
            label = resource_label(node_type, input_name)
            detail = f"工作流要求{label}「{received}」，但当前 ComfyUI 中不存在同名文件。"
            if candidates:
                detail += f" 本机可用候选：{'、'.join(str(item) for item in candidates[:6])}。"
            elif details:
                detail += f"（ComfyUI 原文：{details}）"
        else:
            detail = details or message

        problem = Problem(
            category=category,
            title=CATEGORY_TITLES[category],
            detail=detail,
            node_id=node_id,
            node_type=node_type,
            input_name=input_name,
            received_value=received,
            candidates=candidates,
            expected_range=expected_range,
            batch_impact="工作流级错误，将停止整个批次" if category in WORKFLOW_LEVEL else "该任务失败",
            raw=node_error,
        )
        problem.fixes = cls._fixes_for(category, problem)
        return problem

    @classmethod
    def _from_top_error(cls, top: dict[str, Any], *, already_reported: bool) -> Problem | None:
        event_type = str(top.get("type") or "")
        message = str(top.get("message") or "").strip()
        details = str(top.get("details") or "").strip()
        probe = f"{message} {details}"

        match = _CANNOT_EXECUTE.search(probe)
        if event_type == "missing_node_type" or match:
            extra = top.get("extra_info") or {}
            class_type = str(extra.get("class_type") or (match.group(1) if match else ""))
            node_id = str(extra.get("node_id") or "")
            if not node_id:
                node_id_match = _MISSING_NODE_ID.search(probe)
                node_id = node_id_match.group(1) if node_id_match else ""
            problem = Problem(
                category=ErrorCategory.MISSING_NODE,
                title=CATEGORY_TITLES[ErrorCategory.MISSING_NODE],
                detail=f"工作流引用了当前 ComfyUI 未安装的节点类型「{class_type}」。{message}".strip(),
                node_id=node_id,
                node_type=class_type,
                batch_impact="工作流级错误，将停止整个批次",
                raw=top,
            )
            problem.fixes = cls._fixes_for(ErrorCategory.MISSING_NODE, problem)
            return problem

        if event_type == "prompt_no_outputs" or "no outputs" in message.lower():
            problem = Problem(
                category=ErrorCategory.DANGLING_LINK,
                title="工作流没有可执行的输出节点",
                detail="当前分支未连接到任何 SaveImage 输出，请检查工作流分支选择。",
                batch_impact="工作流级错误，将停止整个批次",
                raw=top,
            )
            problem.fixes = cls._fixes_for(ErrorCategory.DANGLING_LINK, problem)
            return problem

        if already_reported:
            # A node error already explains the cause; this is just the wrapper.
            return None

        category = ErrorCategory.UNKNOWN
        if event_type in {"invalid_prompt", "prompt_outputs_failed_validation"}:
            category = ErrorCategory.PARAMETER_MISALIGNED if "validation" in event_type else ErrorCategory.UNKNOWN
        problem = Problem(
            category=category,
            title=CATEGORY_TITLES[category],
            detail=details or message or event_type,
            batch_impact="工作流级错误，将停止整个批次" if category in WORKFLOW_LEVEL else "该任务失败",
            raw=top,
        )
        problem.fixes = cls._fixes_for(category, problem)
        return problem

    @classmethod
    def _from_text(cls, error: ComfyError) -> Problem:
        """Fallback when there is no parseable body at all."""
        text = (error.raw or str(error)).strip()
        lowered = text.lower()
        category = ErrorCategory.UNKNOWN
        fixes: list[str] = []
        if error.status == 0 or "refused" in lowered or "urlopen" in lowered:
            category = ErrorCategory.EXECUTION_FAILED
            detail = "无法连接 ComfyUI，请确认 ComfyUI 已启动且地址正确。"
            fixes = ["启动 ComfyUI 后点击「重新检查」", "在「本地环境」确认 ComfyUI 地址与端口"]
        elif "out of memory" in lowered or "cuda" in lowered:
            category = ErrorCategory.OUT_OF_MEMORY
            detail = "显存不足。"
            fixes = ["降低画幅或放大倍数", "减少分块尺寸", "关闭其他占用显存的程序后重试"]
        else:
            detail = text[:600] or "ComfyUI 未返回可解析的错误正文。"
            fixes = ["展开原始报文查看完整信息"]
        problem = Problem(
            category=category,
            title=CATEGORY_TITLES[category],
            detail=detail,
            batch_impact="工作流级错误，将停止整个批次" if category in WORKFLOW_LEVEL else "该任务失败",
            raw={"status_text": text[:4000], "raw": error.raw[:4000] if error.raw else ""},
        )
        problem.fixes = fixes
        return problem

    # ------------------------------------------------------------- mapping

    @classmethod
    def _category_for(cls, event_type: str, input_name: str, message: str, details: str) -> str:
        if event_type == "required_input_missing":
            return ErrorCategory.MISSING_INPUT
        if event_type == "value_not_in_list":
            if input_name in RESOURCE_INPUTS:
                return ErrorCategory.MISSING_RESOURCE
            return ErrorCategory.VALUE_OUT_OF_RANGE
        if event_type in {"return_type_mismatch", "invalid_input_type"}:
            return ErrorCategory.PARAMETER_MISALIGNED
        if event_type == "bad_linked_input":
            return ErrorCategory.DANGLING_LINK
        if event_type in {"invalid_node_type", "node_not_found", "missing_node_type"}:
            return ErrorCategory.MISSING_NODE
        if event_type in {"invalid_prompt", "prompt_outputs_failed_validation"}:
            probe = f"{message} {details}".lower()
            if "out of memory" in probe:
                return ErrorCategory.OUT_OF_MEMORY
            if "not in list" in probe or "not in [" in probe:
                return ErrorCategory.MISSING_RESOURCE if input_name in RESOURCE_INPUTS else ErrorCategory.VALUE_OUT_OF_RANGE
            return ErrorCategory.PARAMETER_MISALIGNED
        return ErrorCategory.UNKNOWN

    @classmethod
    def _fixes_for(cls, category: str, problem: Problem) -> list[str]:
        if category == ErrorCategory.MISSING_RESOURCE:
            fixes: list[str] = []
            if problem.candidates:
                shown = "、".join(str(item) for item in problem.candidates[:6])
                fixes.append(f"改用本机已有候选：{shown}")
            label = RESOURCE_INPUTS.get(problem.input_name, "该资源")
            fixes.append(f"把缺失的{label}放入 ComfyUI 对应模型目录后点击「重新检查」")
            fixes.append("改选不包含该节点的分支")
            return fixes
        if category == ErrorCategory.MISSING_NODE:
            return [
                f"安装提供「{problem.node_type}」的自定义节点后重启 ComfyUI",
                "改选不依赖该节点的分支",
                "在「实际生效检查」确认当前分支是否选错",
            ]
        if category == ErrorCategory.VALUE_OUT_OF_RANGE:
            fixes = []
            if problem.candidates:
                fixes.append(f"改用允许的候选值：{'、'.join(str(item) for item in problem.candidates[:6])}")
            elif problem.expected_range:
                fixes.append(f"把 {problem.input_name or '该参数'} 调整到 {problem.expected_range}")
            fixes.append("在参数工作台修正该参数后重新检查")
            return fixes
        if category == ErrorCategory.PARAMETER_MISALIGNED:
            return [
                "展开原始报文核对节点与参数名",
                "在参数工作台按正确参数名重新赋值",
                "如为已知节点，请反馈该节点的参数映射",
            ]
        if category == ErrorCategory.MISSING_INPUT:
            return ["该输入为必填但未连接，请在工作流中补上连线"]
        if category == ErrorCategory.DANGLING_LINK:
            return ["检查所选分支是否完整连接", "改选其他分支", "在工作流中修复断开的连线"]
        if category == ErrorCategory.OUT_OF_MEMORY:
            return ["降低画幅或放大倍数", "减小分块尺寸", "关闭其他占用显存的程序后重试"]
        if category == ErrorCategory.SAVE_FAILED:
            return ["检查输出目录是否存在且可写", "检查磁盘剩余空间"]
        if category == ErrorCategory.MODEL_TYPE_MISMATCH:
            return ["改选与该工作流家族匹配的模型", "在「实际生效检查」核对模型家族"]
        return ["展开原始报文查看完整信息"]

    @classmethod
    def _attach_context(cls, problems: list[Problem], error: ComfyError) -> list[Problem]:
        for index, problem in enumerate(problems):
            raw = dict(problem.raw) if isinstance(problem.raw, dict) else {"detail": problem.raw}
            raw.setdefault("http_status", error.status)
            if error.raw:
                raw.setdefault("response_text", error.raw[:8000])
            problem.raw = raw
            if index:
                problem.batch_impact = problem.batch_impact or "该任务失败"
        return problems


def summarise(problems: list[Problem]) -> str:
    """One-line Chinese summary for a batch-level abort message."""
    if not problems:
        return ""
    head = problems[0]
    extra = f"（另有 {len(problems) - 1} 个问题）" if len(problems) > 1 else ""
    return head.render() + extra


def dumps(problems: list[Problem], *, indent: int = 2) -> str:
    return json.dumps([problem.to_dict() for problem in problems], ensure_ascii=False, indent=indent)
