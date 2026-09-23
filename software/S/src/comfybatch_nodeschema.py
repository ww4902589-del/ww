"""Schema-driven workflow compilation.

The problem this module exists to solve
--------------------------------------
The original converter mapped ComfyUI UI ``widgets_values`` onto API inputs with
a positional cursor that only advanced for *unlinked* widget inputs
(``comfybatch_v2_core.py:1156-1159``)::

    widget_index = 0
    for item in node.get("inputs") or []:
        if item.get("link") is None and item.get("widget") and widget_index < len(widgets):
            inputs[...] = widgets[widget_index]
            widget_index += 1

``widgets_values`` keeps an entry for every widget, *including* widgets whose
value has been promoted to a link, and it also carries hidden frontend widgets
that have no API input at all. So the cursor desynchronises as soon as a node
links any of its widgets. Measured on the real
``▶▷Krea2-极清生图流+SeedVR2-int8图像放大.json`` ``UltimateSDUpscale`` node:

===================  ==========================================  ====================
``widgets_values``   real meaning                                cursor assigned it to
===================  ==========================================  ====================
0                    ``upscale_by`` (promoted to link 8)         skipped
1                    ``seed`` (promoted to link 9)               skipped
2                    ``control_after_generate`` (hidden)         skipped
3                    ``steps``                                   ``widgets[0]``
9, 10                ``tile_width``/``tile_height`` (links 10,11) skipped
11                   ``mask_blur``                                ``widgets[6]``
===================  ==========================================  ====================

The offset therefore drifts from 3 to 5 within a single node, landing a string
in ``denoise`` and producing the ``HTTP Error 400`` that failed all 45 items.

The fix is to stop guessing. Widget values are bound **by name**, using the
node's own declared widget order (authoritative for that file) and the live
ComfyUI ``object_info`` schema for validation. Nothing is matched by position
except the single, precisely-described hidden-widget insertion, and a mismatch
becomes a reported problem instead of a silent mis-mapping.
"""

from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import random
from dataclasses import dataclass, field
from typing import Any, Callable

from comfybatch_errors import (
    SEVERITY_BLOCKING,
    SEVERITY_WARNING,
    ErrorCategory,
    Problem,
)

#: Widget types that live in ``widgets_values``. Everything else is a link.
PRIMITIVE_WIDGET_TYPES = frozenset({"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"})

#: Values ComfyUI's frontend offers for the ``control_after_generate`` widget.
CONTROL_AFTER_GENERATE_VALUES = frozenset({"fixed", "increment", "decrement", "randomize"})

#: Widget names after which the frontend inserts a hidden
#: ``control_after_generate`` slot. This insertion is the only positional rule in
#: the whole binder, and it is verified by a length check afterwards.
SEED_WIDGET_NAMES = frozenset({"seed", "noise_seed"})

#: Output ranges used when a seed has to be generated locally.
SEED_MAX = 2**63 - 1

#: Widget order for core ComfyUI nodes, used only when *neither* the node itself
#: declares ``inputs[].widget`` *nor* ``/object_info`` knows the type.
#:
#: A normal ComfyUI export carries the declaration, and a connected ComfyUI
#: supplies the schema, so this table is a last resort. It matters because the
#: fallback is not neutral: without an order, ``widgets_values`` cannot be bound
#: at all, and a negative-prompt encoder would then silently lose its text --
#: the batch would still run, just with the wrong conditioning. Hand-built or
#: third-party-converted graphs are the realistic way to arrive here.
#:
#: These entries mirror the node's **declared widget order**, deliberately
#: *excluding* the hidden ``control_after_generate`` slot: ``_align_with_reason``
#: re-inserts that slot positionally right after any seed widget, so listing it
#: here as well would shift every value by one. Verified against a real Krea2
#: export, where ``KSampler`` stores seven values
#: (``[seed, control_after_generate, steps, cfg, sampler_name, scheduler,
#: denoise]``) but declares only the six widgets below.
#:
#: Keep this list to nodes the compiler actually reads values from; anything
#: absent still falls back to the existing "cannot determine order" diagnostic.
BUILTIN_WIDGET_ORDER: dict[str, tuple[str, ...]] = {
    "CLIPTextEncode": ("text",),
    "KSampler": ("seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"),
    "KSamplerAdvanced": (
        "add_noise", "noise_seed", "steps", "cfg",
        "sampler_name", "scheduler", "start_at_step", "end_at_step", "return_with_leftover_noise",
    ),
    "EmptyLatentImage": ("width", "height", "batch_size"),
    "EmptySD3LatentImage": ("width", "height", "batch_size"),
    "UNETLoader": ("unet_name", "weight_dtype"),
    "CheckpointLoaderSimple": ("ckpt_name",),
    "VAELoader": ("vae_name",),
    "CLIPLoader": ("clip_name", "type", "device"),
    "UpscaleModelLoader": ("model_name",),
    "SaveImage": ("filename_prefix",),
    "PreviewImage": (),
    "LoadImage": ("image", "upload"),
    "ImageScaleToTotalPixels": ("upscale_method", "megapixels"),
    "LatentUpscaleBy": ("upscale_method", "scale_by"),
}
RGTHREE_SEED_MAX = 1125899906842625

#: Provenance tags surfaced by the audit and shown in the UI.
SOURCE_LINKED = "linked"
SOURCE_WIDGET = "widget"
SOURCE_OVERRIDE = "override"
SOURCE_INJECTED = "injected"
SOURCE_DEFAULT = "default"
#: A file the user swapped in from the software after a preflight finding.
#: Tracked separately from ``override`` so the audit can always answer "was this
#: the workflow's choice or the user's?".
SOURCE_USER_REPLACED = "user_replaced"
#: A value the user set in the parameter workbench.
SOURCE_PARAM = "param"

SOURCE_LABELS_CN = {
    SOURCE_LINKED: "工作流连线",
    SOURCE_WIDGET: "工作流控件值",
    SOURCE_OVERRIDE: "本次运行覆盖",
    SOURCE_INJECTED: "软件注入",
    SOURCE_DEFAULT: "节点默认值",
    SOURCE_USER_REPLACED: "用户在软件内替换",
    SOURCE_PARAM: "参数工作台",
}


# --------------------------------------------------------------------------- #
# Node schema registry
# --------------------------------------------------------------------------- #


class NodeSchemaRegistry:
    """Typed access to ComfyUI's ``/object_info`` node definitions.

    Every accessor tolerates an empty registry so the compiler still works
    offline; the audit degrades from "validated" to "unvalidated" rather than
    failing.
    """

    def __init__(self, object_info: dict[str, Any] | None = None):
        self._info: dict[str, Any] = object_info or {}

    # ---------------------------------------------------------------- loading

    @classmethod
    def empty(cls) -> NodeSchemaRegistry:
        return cls({})

    @classmethod
    def from_object_info(cls, object_info: dict[str, Any]) -> NodeSchemaRegistry:
        return cls(object_info)

    @classmethod
    def from_path(cls, path: str | pathlib.Path) -> NodeSchemaRegistry:
        path = pathlib.Path(path)
        if not path.exists():
            return cls({})
        return cls(json.loads(path.read_text(encoding="utf-8")))

    @staticmethod
    def looks_valid(payload: Any) -> bool:
        """Whether a response really is ComfyUI's ``/object_info``.

        Without this, anything non-empty was accepted as a node schema -- a
        gateway returning a status blob instead of node definitions produced a
        one-entry registry in which every real node looked "missing". Bad
        schemas are worse than no schema, so this gates acceptance.
        """
        if not isinstance(payload, dict) or not payload:
            return False
        if not all(isinstance(value, dict) for value in payload.values()):
            return False
        # Real definitions carry an "input" block; require a clear majority so a
        # few odd entries cannot fail an otherwise good response.
        with_input = sum(1 for value in payload.values() if isinstance(value.get("input"), dict))
        return with_input >= max(1, int(len(payload) * 0.5))

    def __len__(self) -> int:
        return len(self._info)

    def __bool__(self) -> bool:
        return bool(self._info)

    @property
    def class_types(self) -> frozenset[str]:
        return frozenset(self._info)

    def has(self, class_type: str) -> bool:
        return class_type in self._info

    # -------------------------------------------------------------- accessors

    def _inputs(self, class_type: str, section: str) -> dict[str, Any]:
        entry = self._info.get(class_type) or {}
        if not isinstance(entry, dict):
            return {}
        block = entry.get("input")
        if not isinstance(block, dict):
            return {}
        values = block.get(section)
        return values if isinstance(values, dict) else {}

    def _iter_specs(self, class_type: str):
        for section in ("required", "optional"):
            for name, spec in self._inputs(class_type, section).items():
                yield section, name, spec

    def widget_order(self, class_type: str) -> list[str]:
        """Widget-capable input names, in the schema's declared order.

        Falls back to :data:`BUILTIN_WIDGET_ORDER` for core nodes so an offline
        or hand-built graph still binds its widget values by name instead of
        dropping them. The schema always wins when it is available.
        """
        order: list[str] = []
        for _section, name, spec in self._iter_specs(class_type):
            if self._is_widget(spec):
                order.append(name)
        if not order:
            return list(BUILTIN_WIDGET_ORDER.get(class_type, ()))
        return order

    def link_inputs(self, class_type: str) -> list[str]:
        order: list[str] = []
        for _section, name, spec in self._iter_specs(class_type):
            if not self._is_widget(spec):
                order.append(name)
        return order

    @staticmethod
    def _is_widget(spec: Any) -> bool:
        """True when the spec describes a widget rather than a link socket."""
        if not isinstance(spec, list) or not spec:
            return False
        head = spec[0]
        if isinstance(head, list):
            return True  # legacy inline candidate list
        if not isinstance(head, str):
            return False
        if len(spec) > 1 and isinstance(spec[1], dict) and spec[1].get("forceInput"):
            return False
        return head in PRIMITIVE_WIDGET_TYPES

    def input_spec(self, class_type: str, name: str) -> dict[str, Any] | None:
        for section, candidate, spec in self._iter_specs(class_type):
            if candidate != name:
                continue
            head = spec[0] if isinstance(spec, list) and spec else None
            options = spec[1] if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict) else {}
            kind = "COMBO" if isinstance(head, list) else (head if isinstance(head, str) else "")
            if kind == "COMBO" and head == "COMBO" and options.get("options") is not None:
                return {"name": name, "type": "COMBO", "options": list(options["options"]), "required": section == "required", "opts": options}
            if kind == "COMBO" and isinstance(head, list):
                return {"name": name, "type": "COMBO", "options": list(head), "required": section == "required", "opts": options}
            return {"name": name, "type": kind or "UNKNOWN", "options": [], "required": section == "required", "opts": options}
        return None

    def input_type(self, class_type: str, name: str) -> str:
        spec = self.input_spec(class_type, name)
        return str(spec["type"]) if spec else ""

    def all_input_names(self, class_type: str) -> set[str]:
        return {name for _section, name, _spec in self._iter_specs(class_type)}

    def required_input_names(self, class_type: str) -> list[str]:
        """Names in the ``required`` section.

        ComfyUI rejects a prompt whose node omits any of these with
        ``required_input_missing`` (captured verbatim in
        ``tests/fixtures/http400_missing_input.json``), so checking them here
        turns that 400 into a preflight finding.
        """
        return list(self._inputs(class_type, "required"))

    def combo_candidates(self, class_type: str, name: str) -> list[Any]:
        spec = self.input_spec(class_type, name)
        if not spec or spec.get("type") != "COMBO":
            return []
        return list(spec.get("options") or [])

    def node_label(self, class_type: str) -> str:
        entry = self._info.get(class_type)
        if isinstance(entry, dict):
            for key in ("display_name", "name"):
                value = entry.get(key)
                if isinstance(value, str) and value:
                    return value
        return class_type

    # ------------------------------------------------------------- validation

    def coerce(self, class_type: str, name: str, value: Any) -> Any:
        """Best-effort conversion to the declared widget type.

        Keeps a linked/None value untouched so link references survive.
        """
        if isinstance(value, list) or value is None:
            return value
        kind = self.input_type(class_type, name)
        try:
            if kind == "INT" and not isinstance(value, bool):
                return int(value)
            if kind == "FLOAT":
                return float(value)
            if kind == "BOOLEAN":
                if isinstance(value, str):
                    return value.strip().lower() in {"true", "1", "yes", "on"}
                return bool(value)
            if kind in {"STRING", "COMBO"} and not isinstance(value, str):
                return str(value)
        except (TypeError, ValueError):
            return value
        return value

    def validate(self, class_type: str, name: str, value: Any) -> tuple[bool, str, list[Any], str]:
        """Return ``(ok, reason, candidates, expected_range)`` for one input."""
        if isinstance(value, list):
            return True, "", [], ""  # a link; validated by the graph audit instead
        spec = self.input_spec(class_type, name)
        if spec is None:
            return True, "", [], ""
        kind = spec["type"]
        options = spec.get("options") or []
        opts = spec.get("opts") or {}
        if kind == "COMBO":
            if options and value not in options:
                return False, f"「{value}」不在允许的候选列表中", list(options), ""
            return True, "", [], ""
        if kind == "INT" and not isinstance(value, bool) and not isinstance(value, int):
            return False, f"应为整数，实际为 {type(value).__name__}", [], ""
        if kind == "FLOAT" and not isinstance(value, (int, float)):
            return False, f"应为数字，实际为 {type(value).__name__}", [], ""
        if kind in {"INT", "FLOAT"} and isinstance(value, (int, float)) and not isinstance(value, bool):
            low, high = opts.get("min"), opts.get("max")
            if low is not None and value < low:
                return False, f"小于允许的最小值 {low}", [], f"{low} ~ {high if high is not None else '+∞'}"
            if high is not None and value > high:
                return False, f"大于允许的最大值 {high}", [], f"{low if low is not None else '-∞'} ~ {high}"
        if kind == "BOOLEAN" and not isinstance(value, bool):
            return False, f"应为布尔值，实际为 {type(value).__name__}", [], ""
        return True, "", [], ""


_ACTIVE_REGISTRY: NodeSchemaRegistry = NodeSchemaRegistry.empty()


def active_registry() -> NodeSchemaRegistry:
    return _ACTIVE_REGISTRY


def set_active_registry(registry: NodeSchemaRegistry | None) -> None:
    """Install the process-wide schema.

    ``Application`` calls this once ``/object_info`` has been fetched so that
    ``Krea2WorkflowAdapter`` instances pick up real validation without having to
    thread the registry through every call site.
    """
    global _ACTIVE_REGISTRY
    _ACTIVE_REGISTRY = registry or NodeSchemaRegistry.empty()


# --------------------------------------------------------------------------- #
# Widget binding
# --------------------------------------------------------------------------- #


@dataclass
class BindResult:
    widgets: dict[str, Any] = field(default_factory=dict)
    problems: list[Problem] = field(default_factory=list)
    declared: list[str] = field(default_factory=list)
    strategy: str = ""


class WidgetBinder:
    """Bind ``widgets_values`` to API input names without positional guessing."""

    @staticmethod
    def declared_widgets(node: dict[str, Any]) -> list[str]:
        """Widget-capable input names as declared by the node itself.

        The frontend writes ``{"widget": {"name": ...}}`` on every input that has
        a widget. Order is the frontend's, which is the order ``widgets_values``
        uses -- and, critically, it includes widgets that were promoted to links.
        """
        names: list[str] = []
        for item in node.get("inputs") or []:
            if not isinstance(item, dict):
                continue
            widget = item.get("widget")
            if not isinstance(widget, dict):
                continue
            name = widget.get("name") or item.get("name")
            if isinstance(name, str) and name:
                names.append(name)
        return names

    @classmethod
    def bind(
        cls,
        node: dict[str, Any],
        registry: NodeSchemaRegistry | None = None,
    ) -> BindResult:
        registry = registry or NodeSchemaRegistry.empty()
        node_type = str(node.get("type") or "")
        raw = node.get("widgets_values")
        if isinstance(raw, list):
            values = list(raw)
        elif raw is None:
            values = []
        else:
            values = [raw]

        declared = cls.declared_widgets(node)
        strategy = "declared"
        schema_widgets = registry.widget_order(node_type)

        if not declared:
            declared = schema_widgets
            strategy = "schema" if declared else "none"

        result = BindResult(declared=list(declared), strategy=strategy)
        if not values:
            return result
        if not declared:
            # A recognised node with no widget inputs at all can legitimately
            # carry widgets_values: display-only nodes such as
            # "Image Comparer (rgthree)" store frontend state (temp preview URLs)
            # there, and none of it has an API input to be written to.
            if registry.has(node_type):
                result.strategy = "no-widgets"
                return result
            result.problems.append(Problem(
                category=ErrorCategory.PARAMETER_MISALIGNED,
                title="无法确定控件顺序",
                detail=(
                    f"节点「{node_type}」既未声明控件顺序，当前 ComfyUI 也查不到它的定义，"
                    f"无法把 {len(values)} 个控件值安全地映射到参数上。"
                ),
                node_id=str(node.get("id") or ""),
                node_type=node_type,
                # Diagnostic only: the graph audit reports the missing node type
                # itself, and that is the finding which should block a run.
                severity=SEVERITY_WARNING,
                fixes=["确认该自定义节点已安装并重启 ComfyUI", "点击「重新检查」刷新节点定义"],
            ))
            return result

        mapping, aligned, ran_short = cls._align_with_reason(declared, values)
        strategy = "declared"
        if not aligned:
            schema_widgets = registry.widget_order(node_type)
            if schema_widgets and schema_widgets != declared:
                alternative, alternative_aligned, alternative_short = cls._align_with_reason(schema_widgets, values)
                if alternative_aligned:
                    mapping, aligned, ran_short, strategy = alternative, True, False, "schema"

        if not aligned:
            # Always a diagnostic, never a gate. The binding layer cannot know
            # whether a converter or ComfyUI will supply the missing value, and
            # blocking here produced false positives on real workflows (an older
            # export that omits a defaulted widget). The authority on whether the
            # final graph is valid is CompiledGraphAudit, which sees the graph
            # that would actually be submitted and reports missing required
            # inputs itself.
            result.problems.append(
                cls._misalignment_problem(node, node_type, declared, values, strategy, SEVERITY_WARNING)
            )
            result.widgets = {name: registry.coerce(node_type, name, value) for name, value in mapping.items()}
            return result

        result.strategy = strategy
        result.widgets = {name: registry.coerce(node_type, name, value) for name, value in mapping.items()}
        return result

    @classmethod
    def _align(cls, declared: list[str], values: list[Any]) -> tuple[dict[str, Any], bool]:
        mapping, aligned, _ = cls._align_with_reason(declared, values)
        return mapping, aligned

    @classmethod
    def _align_with_reason(cls, declared: list[str], values: list[Any]) -> tuple[dict[str, Any], bool, bool]:
        """Pair names with values, accounting for hidden ``control_after_generate``.

        Returns ``(mapping, aligned, ran_short)``. ``aligned`` is only true when
        every supplied value was consumed exactly once, so a silent mis-mapping
        is impossible. ``ran_short`` distinguishes the dangerous failure (a
        declared parameter got no value) from the harmless one (the frontend
        stored extra trailing state).
        """
        mapping: dict[str, Any] = {}
        cursor = 0
        for name in declared:
            if cursor >= len(values):
                return mapping, False, True
            mapping[name] = values[cursor]
            cursor += 1
            # The frontend inserts a hidden control_after_generate right after a
            # seed widget. It has no API input, so it must be consumed here or
            # every following value shifts by one.
            if name in SEED_WIDGET_NAMES and cursor < len(values) and isinstance(values[cursor], str) and values[cursor] in CONTROL_AFTER_GENERATE_VALUES:
                cursor += 1
        return mapping, cursor == len(values), False

    @staticmethod
    def _misalignment_problem(
        node: dict[str, Any],
        node_type: str,
        declared: list[str],
        values: list[Any],
        strategy: str,
        severity: str = SEVERITY_BLOCKING,
        dangerous: list[str] | None = None,
    ) -> Problem:
        def shorten(items: list[Any]) -> str:
            shown = "、".join(repr(item) for item in items[:12])
            return shown + ("…" if len(items) > 12 else "")

        dangerous = dangerous or []
        extras = len(values) > len(declared)
        if severity == SEVERITY_WARNING and extras:
            detail = (
                f"节点「{node_type}」声明了 {len(declared)} 个控件参数"
                f"（{shorten(declared)}），工作流里有 {len(values)} 个控件值"
                f"（{shorten(values)}）。多出的值是前端显示状态，没有对应参数，已忽略；"
                f"其余参数已按名称正确映射。"
            )
        elif severity == SEVERITY_WARNING:
            detail = (
                f"节点「{node_type}」声明了 {len(declared)} 个控件参数"
                f"（{shorten(declared)}），工作流里只有 {len(values)} 个控件值"
                f"（{shorten(values)}）。缺少的部分由 ComfyUI 的默认值补齐，"
                f"已绑定的参数均按名称正确映射。"
            )
        else:
            detail = (
                f"节点「{node_type}」（{strategy} 顺序）声明了 {len(declared)} 个控件参数"
                f"（{shorten(declared)}），但工作流里只有 {len(values)} 个控件值"
                f"（{shorten(values)}），"
                + (f"其中 {shorten(dangerous)} 没有默认值可用。" if dangerous else "部分参数拿不到值。")
                + "为避免把值写到错误参数上，已停止按位置猜测。"
            )
        return Problem(
            category=ErrorCategory.PARAMETER_MISALIGNED,
            title="节点控件值与参数名数量不一致",
            detail=detail,
            node_id=str(node.get("id") or ""),
            node_type=node_type,
            severity=severity,
            fixes=(
                ["确认该自定义节点已安装并重启 ComfyUI", "点击「重新检查」刷新节点定义"]
                if severity == SEVERITY_WARNING
                else [
                    "在 ComfyUI 中重新导出该工作流，使其包含完整的控件声明",
                    "确认 ComfyUI 版本与该工作流匹配后点击「重新检查」",
                    "展开原始信息核对该节点的控件列表",
                ]
            ),
        )


# --------------------------------------------------------------------------- #
# Declarative per-node converters (replaces the old elif chain)
# --------------------------------------------------------------------------- #


@dataclass
class BindContext:
    """Everything a converter needs to override one node's inputs."""

    node_type: str
    node_id: str
    widgets: dict[str, Any]
    linked: dict[str, Any]
    config: Any
    target_width: int
    target_height: int
    output_prefix: str
    prompt_text: str
    source_image: str
    registry: NodeSchemaRegistry
    style_inputs: dict[str, Any] = field(default_factory=dict)

    def widget(self, name: str, default: Any = None) -> Any:
        value = self.widgets.get(name)
        return default if value is None else value

    @property
    def native_styles(self) -> bool:
        return bool(getattr(self.config, "styles", None)) and getattr(self.config, "style_application", "") == "native"


@dataclass
class Converter:
    """A declarative override rule for one node class."""

    apply: Callable[[BindContext, dict[str, Any], dict[str, str]], None]
    seed_policy: str = "preserve"
    note: str = ""


def _set(ctx: BindContext, inputs: dict[str, Any], sources: dict[str, str], **values: Any) -> None:
    for name, value in values.items():
        if value is None:
            continue
        inputs[name] = value
        sources[name] = SOURCE_OVERRIDE


def _randomize_seed(inputs: dict[str, Any], sources: dict[str, str], name: str, limit: int = SEED_MAX) -> None:
    """Randomize a seed unless the workflow linked it to another node."""
    if isinstance(inputs.get(name), list):
        return
    inputs[name] = random.SystemRandom().randrange(0, limit)
    sources[name] = SOURCE_OVERRIDE


def _save_image(ctx: BindContext, inputs: dict[str, Any], sources: dict[str, str]) -> None:
    _set(ctx, inputs, sources, filename_prefix=ctx.output_prefix)


def _ksampler(ctx: BindContext, inputs: dict[str, Any], sources: dict[str, str]) -> None:
    _set(
        ctx, inputs, sources,
        steps=int(ctx.widget("steps", 8) or 8),
        cfg=float(ctx.widget("cfg", 1.0) if ctx.widget("cfg") is not None else 1.0),
        sampler_name=ctx.widget("sampler_name", "euler") or "euler",
        scheduler=ctx.widget("scheduler", "simple") or "simple",
        denoise=float(ctx.widget("denoise", 1.0) if ctx.widget("denoise") is not None else 1.0),
    )
    _randomize_seed(inputs, sources, "seed")


def _ksampler_advanced(ctx: BindContext, inputs: dict[str, Any], sources: dict[str, str]) -> None:
    _set(
        ctx, inputs, sources,
        add_noise=ctx.widget("add_noise", "enable") or "enable",
        steps=int(ctx.widget("steps", 20) or 20),
        cfg=float(ctx.widget("cfg", 1.0) if ctx.widget("cfg") is not None else 1.0),
        sampler_name=ctx.widget("sampler_name", "euler") or "euler",
        scheduler=ctx.widget("scheduler", "simple") or "simple",
        start_at_step=int(ctx.widget("start_at_step", 0) or 0),
        end_at_step=int(ctx.widget("end_at_step", 10000) if ctx.widget("end_at_step") is not None else 10000),
        return_with_leftover_noise=ctx.widget("return_with_leftover_noise", "disable") or "disable",
    )
    _randomize_seed(inputs, sources, "noise_seed")


def _ultimate_sd_upscale(ctx: BindContext, inputs: dict[str, Any], sources: dict[str, str]) -> None:
    """The node that started this whole module. All parameters are real names now."""
    _set(
        ctx, inputs, sources,
        upscale_by=float(ctx.widget("upscale_by", 2.0) or 2.0),
        steps=int(ctx.widget("steps", 20) or 20),
        cfg=float(ctx.widget("cfg", 1.0) if ctx.widget("cfg") is not None else 1.0),
        sampler_name=ctx.widget("sampler_name", "euler") or "euler",
        scheduler=ctx.widget("scheduler", "simple") or "simple",
        denoise=float(ctx.widget("denoise", 1.0) if ctx.widget("denoise") is not None else 1.0),
        mode_type=ctx.widget("mode_type", "Linear") or "Linear",
        tile_width=int(ctx.widget("tile_width", 512) or 512),
        tile_height=int(ctx.widget("tile_height", 512) or 512),
        mask_blur=int(ctx.widget("mask_blur", 8) or 8),
        tile_padding=int(ctx.widget("tile_padding", 32) if ctx.widget("tile_padding") is not None else 32),
        seam_fix_mode=ctx.widget("seam_fix_mode", "None") or "None",
        seam_fix_denoise=float(ctx.widget("seam_fix_denoise", 1.0) if ctx.widget("seam_fix_denoise") is not None else 1.0),
        seam_fix_width=int(ctx.widget("seam_fix_width", 64) or 64),
        seam_fix_mask_blur=int(ctx.widget("seam_fix_mask_blur", 8) or 8),
        seam_fix_padding=int(ctx.widget("seam_fix_padding", 16) if ctx.widget("seam_fix_padding") is not None else 16),
        force_uniform_tiles=bool(ctx.widget("force_uniform_tiles", True)),
        tiled_decode=bool(ctx.widget("tiled_decode", False)),
    )
    p = getattr(ctx.config, "params", None) or {}
    if p.get("upscale_model"):
        _set(ctx, inputs, sources, upscale_model_name=str(p["upscale_model"]))
    if p.get("upscale_by") is not None:
        _set(ctx, inputs, sources, upscale_by=float(p["upscale_by"]))
    _randomize_seed(inputs, sources, "seed")


def _upscale_model_loader(ctx: BindContext, inputs: dict[str, Any], sources: dict[str, str]) -> None:
    p = getattr(ctx.config, "params", None) or {}
    if p.get("upscale_model"):
        _set(ctx, inputs, sources, model_name=str(p["upscale_model"]))


def _rgthree_seed(ctx: BindContext, inputs: dict[str, Any], sources: dict[str, str]) -> None:
    if not isinstance(inputs.get("seed"), list):
        inputs["seed"] = random.SystemRandom().randrange(0, RGTHREE_SEED_MAX)
        sources["seed"] = SOURCE_OVERRIDE


def _empty_latent(ctx: BindContext, inputs: dict[str, Any], sources: dict[str, str]) -> None:
    if not isinstance(inputs.get("width"), list):
        _set(ctx, inputs, sources, width=ctx.target_width)
    if not isinstance(inputs.get("height"), list):
        _set(ctx, inputs, sources, height=ctx.target_height)
    _set(ctx, inputs, sources, batch_size=int(ctx.widget("batch_size", 1) or 1))


def _vae_loader(ctx: BindContext, inputs: dict[str, Any], sources: dict[str, str]) -> None:
    # ``vae_name`` is a plain widget and name binding already placed it. Doing
    # nothing here is deliberate: if the value is genuinely absent, the audit
    # should report a missing required input instead of us inventing a default.
    return None


def _clip_loader(ctx: BindContext, inputs: dict[str, Any], sources: dict[str, str]) -> None:
    # Only fill the fields ComfyUI would have defaulted anyway; never clobber a
    # value that name binding already recovered from the workflow.
    for name, fallback in (("type", "krea2"), ("device", "default")):
        if inputs.get(name) in (None, ""):
            _set(ctx, inputs, sources, **{name: fallback})


def _unet_loader(ctx: BindContext, inputs: dict[str, Any], sources: dict[str, str]) -> None:
    model = getattr(ctx.config, "model", "") or ctx.widget("unet_name", "")
    if model:
        _set(ctx, inputs, sources, unet_name=model)
    weight_dtype = ctx.widget("weight_dtype", "") or inputs.get("weight_dtype") or "default"
    _set(ctx, inputs, sources, weight_dtype=weight_dtype)


def _checkpoint_loader(ctx: BindContext, inputs: dict[str, Any], sources: dict[str, str]) -> None:
    model = getattr(ctx.config, "model", "") or ctx.widget("ckpt_name", "")
    if model:
        _set(ctx, inputs, sources, ckpt_name=model)


def _primitive_string(ctx: BindContext, inputs: dict[str, Any], sources: dict[str, str]) -> None:
    _set(ctx, inputs, sources, value=ctx.prompt_text)


def _easy_styles(ctx: BindContext, inputs: dict[str, Any], sources: dict[str, str]) -> None:
    _set(ctx, inputs, sources,
         styles=ctx.style_inputs.get("styles") or ctx.widget("styles", "") or "",
         select_styles=ctx.style_inputs.get("select_styles") or ctx.widget("select_styles", "") or "")


def _resolution_selector(ctx: BindContext, inputs: dict[str, Any], sources: dict[str, str]) -> None:
    _set(ctx, inputs, sources,
         aspect_ratio=getattr(ctx.config, "aspect_ratio", "") or ctx.widget("aspect_ratio", "") or "",
         megapixels=float(getattr(ctx.config, "megapixels", 1.2) or ctx.widget("megapixels", 1.2) or 1.2),
         multiple=int(ctx.widget("multiple", 32) or 32))


def _load_image(ctx: BindContext, inputs: dict[str, Any], sources: dict[str, str]) -> None:
    if ctx.source_image:
        _set(ctx, inputs, sources, image=ctx.source_image)


def _scale_to_total_pixels(ctx: BindContext, inputs: dict[str, Any], sources: dict[str, str]) -> None:
    _set(ctx, inputs, sources, megapixels=float(getattr(ctx.config, "megapixels", 1.2)))


def _latent_upscale_by(ctx: BindContext, inputs: dict[str, Any], sources: dict[str, str]) -> None:
    p = getattr(ctx.config, "params", None) or {}
    if p.get("latent_upscale_by") is not None:
        _set(ctx, inputs, sources, scale_by=float(p["latent_upscale_by"]))


#: The registry that replaces the old ``elif node_type == ...`` chain.
#: Adding a node type is now one entry, and anything absent still gets correct
#: name-based widget binding instead of silent defaulting.
CONVERTERS: dict[str, Converter] = {
    "SaveImage": Converter(_save_image),
    "PreviewImage": Converter(_save_image),
    "KSampler": Converter(_ksampler, seed_policy="randomize"),
    "KSamplerAdvanced": Converter(_ksampler_advanced, seed_policy="randomize"),
    "UltimateSDUpscale": Converter(_ultimate_sd_upscale, seed_policy="randomize",
                                   note="基础参数错位的节点；全部参数按名映射"),
    "UltimateSDUpscaleNoUpscale": Converter(_ultimate_sd_upscale, seed_policy="randomize"),
    "UpscaleModelLoader": Converter(_upscale_model_loader, note="支持放大模型替换"),
    "ImageUpscaleWithModel": Converter(lambda ctx, inputs, sources: None),
    "Seed (rgthree)": Converter(_rgthree_seed, seed_policy="randomize"),
    "EmptyLatentImage": Converter(_empty_latent),
    "EmptySD3LatentImage": Converter(_empty_latent),
    "LatentUpscaleBy": Converter(_latent_upscale_by),
    "ImageScaleToTotalPixels": Converter(_scale_to_total_pixels),
    "VAELoader": Converter(_vae_loader),
    "CLIPLoader": Converter(_clip_loader),
    "UNETLoader": Converter(_unet_loader),
    "CheckpointLoaderSimple": Converter(_checkpoint_loader),
    "PrimitiveStringMultiline": Converter(_primitive_string),
    "easy stylesSelector": Converter(_easy_styles),
    "ResolutionSelector": Converter(_resolution_selector),
    "LoadImage": Converter(_load_image),
}


#: Keys that describe how a node looks rather than what it does. Excluded from
#: the structural fingerprint so that moving or resizing a node on the canvas
#: does not invalidate a saved replacement rule.
_COSMETIC_NODE_KEYS = frozenset({
    "pos", "size", "order", "flags", "properties", "title", "color", "bgcolor",
    "shape", "collapsed", "pin", "bounding", "select", "dragging", "hover",
})


def structural_fingerprint(workflow: dict[str, Any]) -> str:
    """Fingerprint of what a workflow *does*, ignoring how it *looks*.

    A whole-file hash is too fragile to key saved replacement rules on: dragging
    a node changes its ``pos`` and therefore the hash, so a purely cosmetic edit
    would silently disable a rule the user had saved. This hashes only the parts
    that affect compilation:

    * node id, class type and mode;
    * each node's widget values;
    * link topology expressed as ``(source node, slot) -> (target node, slot)``
      rather than by link id, because link ids are renumbered on re-export.

    Moving a node, retitling a note, or re-saving the file with different float
    formatting therefore keeps the fingerprint, while changing a parameter,
    adding a node, muting a node or rewiring a link changes it.
    """
    if not isinstance(workflow, dict):
        return ""

    nodes = workflow.get("nodes")
    if isinstance(nodes, list):
        canonical_nodes: list[Any] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            canonical_nodes.append({
                "id": str(node.get("id")),
                "type": str(node.get("type")),
                "mode": int(node.get("mode", 0) or 0),
                "widgets": list(node.get("widgets_values") or []),
            })
        canonical_nodes.sort(key=lambda item: (len(item["id"]), item["id"]))

        links: list[Any] = []
        for link in workflow.get("links") or []:
            # UI links are [link_id, src_node, src_slot, dst_node, dst_slot, type].
            if isinstance(link, list) and len(link) >= 5:
                links.append([str(link[1]), int(link[2]), str(link[3]), int(link[4])])
        links.sort(key=lambda item: (len(item[0]), item[0], item[1], len(item[2]), item[2], item[3]))

        source: Any = {"nodes": canonical_nodes, "links": links}
    else:
        # API-format graph: nodes are the top-level entries, links are inline.
        source = {}
        for node_id, node in sorted(workflow.items(), key=lambda item: (len(str(item[0])), str(item[0]))):
            if not isinstance(node, dict):
                continue
            linked = sorted(
                [[name, value[:2]] for name, value in (node.get("inputs") or {}).items()
                 if isinstance(value, list) and len(value) == 2]
            )
            source[str(node_id)] = {
                "class_type": str(node.get("class_type") or ""),
                "inputs": {name: value for name, value in (node.get("inputs") or {}).items()
                           if not (isinstance(value, list) and len(value) == 2)},
                "linked": linked,
            }

    blob = json.dumps(source, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def convert_node(
    node_id: str,
    node: dict[str, Any],
    linked: dict[str, Any],
    *,
    config: Any,
    target_size: tuple[int, int],
    output_prefix: str,
    prompt_text: str,
    source_image: str,
    registry: NodeSchemaRegistry | None = None,
    style_inputs: dict[str, Any] | None = None,
    resource_overrides: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str], list[Problem]]:
    """Compile one UI node into an API node payload.

    Returns ``(inputs, sources, problems)`` where ``sources`` records the
    provenance of every input for the audit.

    ``resource_overrides`` is ``{input_name: value}`` for *this* node, produced
    by the user's in-software replacement rules. It is applied last, and marked
    as an override, so a replacement is always visible in the audit instead of
    looking like something the workflow asked for.
    """
    registry = registry or NodeSchemaRegistry.empty()
    node_type = str(node.get("type") or "")

    binding = WidgetBinder.bind(node, registry)
    problems = list(binding.problems)

    inputs: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for name, value in binding.widgets.items():
        inputs[name] = value
        sources[name] = SOURCE_WIDGET
    # Linked inputs win over widget values, and because both are keyed by name,
    # whether a widget was promoted to a link no longer affects anything else.
    for name, value in linked.items():
        inputs[name] = copy.deepcopy(value)
        sources[name] = SOURCE_LINKED

    ctx = BindContext(
        node_type=node_type,
        node_id=node_id,
        widgets=binding.widgets,
        linked=linked,
        config=config,
        target_width=target_size[0],
        target_height=target_size[1],
        output_prefix=output_prefix,
        prompt_text=prompt_text,
        source_image=source_image,
        registry=registry,
        style_inputs=dict(style_inputs or {}),
    )
    converter = CONVERTERS.get(node_type)
    if converter is not None:
        converter.apply(ctx, inputs, sources)

    # User-chosen resource replacements go last and are tagged distinctly, so the
    # audit shows them as the user's decision rather than as something the
    # workflow declared. Silent substitution is exactly what must not happen.
    for name, value in (resource_overrides or {}).items():
        inputs[str(name)] = value
        sources[str(name)] = SOURCE_USER_REPLACED
    return inputs, sources, problems


#: Seeds that actually drive sampling, taken from the compiled graph.
SEED_INPUT_NAMES = ("seed", "noise_seed")


def graph_seeds(graph: dict[str, Any]) -> dict[str, Any]:
    """Concrete integer seeds in a compiled graph, keyed by ``node.input``.

    Only integers are reported: a seed that is still a link is supplied by
    another node, and the audit already traces where it comes from.
    """
    seeds: dict[str, Any] = {}
    for node_id in sorted(graph, key=lambda value: (len(str(value)), str(value))):
        for name in SEED_INPUT_NAMES:
            value = (graph[node_id].get("inputs") or {}).get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                seeds[f"{node_id}.{name}"] = value
    return seeds


def apply_seed(graph: dict[str, Any], seed: int) -> dict[str, Any]:
    """Pin every concrete seed in ``graph`` to ``seed``.

    Nodes are offset by one another so a workflow with several samplers does not
    get the same seed twice, which would correlate the passes. ``Seed (rgthree)``
    nodes are covered by the same walk -- their ``seed`` is already in
    :func:`graph_seeds`, so they must not be offset a second time. Linked seeds
    are left alone: their value belongs to the node that produces it.
    """
    seeds = graph_seeds(graph)
    for offset, key in enumerate(seeds):
        node_id, _, name = key.partition(".")
        graph[node_id]["inputs"][name] = int(seed) + offset
    return graph


def executed_graph(entry: dict[str, Any] | None) -> dict[str, Any]:
    """The graph ComfyUI reports it ran, taken from a ``/history/<id>`` entry.

    ``/history`` keeps the received prompt as ``[number, prompt_id, graph,
    extra_data, outputs]``; a few builds store the graph directly. Anything else
    returns ``{}``, and that distinction matters: "history did not tell us" and
    "the seed did not apply" look identical to a careless reader, and confusing
    them would report every older ComfyUI as broken.
    """
    if not isinstance(entry, dict):
        return {}
    prompt = entry.get("prompt")
    if isinstance(prompt, dict):
        return prompt
    if isinstance(prompt, (list, tuple)) and len(prompt) > 2 and isinstance(prompt[2], dict):
        return prompt[2]
    return {}


def seed_report(submitted: dict[str, Any], actual: dict[str, Any], *, available: bool = True) -> dict[str, Any]:
    """Compare the seeds we sent with the seeds that actually produced the image.

    ``available=False`` means the actual side could not be read at all; the
    caller must then say "未核对" instead of claiming the seed failed. ``effective``
    is ``None`` in that case, never ``False`` -- an unverified seed and a broken
    seed deserve different words.
    """
    if not available:
        return {"available": False, "effective": None, "mismatched": {}, "missing": [], "extra": []}
    mismatched: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for key, value in submitted.items():
        if key not in actual:
            missing.append(key)
        elif int(actual[key]) != int(value):
            mismatched[key] = {"submitted": value, "executed": actual[key]}
    extra = [key for key in actual if key not in submitted]
    return {
        "available": True,
        "effective": not mismatched and not missing and not extra,
        "mismatched": mismatched,
        "missing": missing,
        "extra": extra,
    }


def workflow_seed_map(
    workflow: dict[str, Any],
    registry: NodeSchemaRegistry | None = None,
    node_ids: Any = None,
    include_muted: bool = False,
) -> dict[str, Any]:
    """Every ``seed``/``noise_seed`` input in a workflow, whatever its format.

    ``value`` is the concrete integer set on that node, or ``None`` when the seed
    is wired from another node -- in which case the frontend's leftover widget
    value is *not* the seed and must not be reported as one. ``source`` names the
    node that owns the wire, resolved to the producing node id so a message can
    point somewhere real.

    ``node_ids`` narrows a UI workflow to one execution branch. Without it the
    answer would mix branches together: a workflow where one branch can be pinned
    and another cannot would be reported as if both were fine.
    """
    registry = registry or active_registry()
    wanted = {str(item) for item in node_ids} if node_ids is not None else None
    is_api = not isinstance(workflow.get("nodes"), list)
    found: dict[str, Any] = {}
    if is_api:
        for node_id, node in (workflow or {}).items():
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs") or {}
            for name in SEED_INPUT_NAMES:
                if name not in inputs:
                    continue
                value = inputs[name]
                if isinstance(value, list) and value:
                    found[str(node_id)] = {
                        "type": str(node.get("class_type") or ""), "name": name,
                        "value": None, "source": str(value[0]),
                    }
                elif isinstance(value, int) and not isinstance(value, bool):
                    found[str(node_id)] = {
                        "type": str(node.get("class_type") or ""), "name": name,
                        "value": value, "source": "",
                    }
        return found

    nodes = workflow.get("nodes") or []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if wanted is not None:
            if str(node.get("id")) not in wanted:
                continue
        elif not include_muted and int(node.get("mode", 0) or 0) != 0:
            continue
        entries = [item for item in (node.get("inputs") or []) if isinstance(item, dict)]
        bound = WidgetBinder.bind(node, registry).widgets
        for name in SEED_INPUT_NAMES:
            entry = next((item for item in entries if item.get("name") == name), None)
            if entry is not None and entry.get("link") is not None:
                # The frontend keeps the widget's last value in ``widgets_values``
                # even after it is promoted to a link. That leftover is not the
                # seed, and reporting it as one would be exactly the kind of
                # plausible-looking lie this module exists to prevent.
                source = _link_source(nodes, entry.get("link"))
                found[str(node.get("id"))] = {
                    "type": str(node.get("type") or ""), "name": name,
                    "value": None, "source": str(source.get("id") or "") if source else "",
                    "source_type": str(source.get("type") or "") if source else "",
                }
                continue
            value = bound.get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                found[str(node.get("id"))] = {
                    "type": str(node.get("type") or ""), "name": name,
                    "value": value, "source": "", "source_type": "",
                }
    return found


def _link_source(nodes: list[Any], link_id: Any) -> dict[str, Any] | None:
    """The node that produces ``link_id``.

    Returns ``None`` when the wire cannot be traced; an unknown source stays
    unknown instead of being replaced by a plausible-looking node.
    """
    for node in nodes:
        if not isinstance(node, dict):
            continue
        for output in node.get("outputs") or []:
            if isinstance(output, dict) and link_id in (output.get("links") or []):
                return node
    return None


def seed_plan(
    workflow: dict[str, Any],
    registry: NodeSchemaRegistry | None = None,
    node_ids: Any = None,
) -> dict[str, Any]:
    """How much of a workflow's seeding a fixed seed can actually reach.

    Computed from the workflow, not from a compiled graph, so preflight can state
    it before anything is built. A concrete integer seed can be pinned. A seed
    wired from another node belongs to that node -- and ``apply_seed`` can only
    follow the wire if that node has a seed input of its own. When nothing in the
    selected branch qualifies, asking for a fixed seed does precisely nothing,
    and that used to be completely silent.
    """
    seeds = workflow_seed_map(workflow, registry, node_ids)
    if isinstance(workflow.get("nodes"), list):
        everywhere = workflow_seed_map(workflow, registry, None, include_muted=True)
    else:
        everywhere = seeds
    pinnable = {f"{node_id}.{item['name']}": item["value"] for node_id, item in seeds.items() if item["value"] is not None}
    # The producer may sit in a muted node (branch selection is expressed with
    # ``mode``), so "can we pin it" is asked of the whole workflow, not just the
    # branch being reported on.
    controllable = {node_id for node_id, item in everywhere.items() if item["value"] is not None}
    linked: dict[str, dict[str, Any]] = {}
    uncontrollable: list[str] = []
    for node_id, item in seeds.items():
        if item["value"] is not None:
            continue
        key = f"{node_id}.{item['name']}"
        source = item["source"]
        # The producing node itself carries a concrete seed => apply_seed pins it
        # and every consumer is covered. That is the rgthree Seed shape used by
        # every real Krea2 workflow in this project.
        follows = bool(source) and source in controllable
        linked[key] = {
            "type": item["type"], "from": source,
            "from_type": item.get("source_type") or "", "controllable": follows,
        }
        if not follows:
            uncontrollable.append(key)
    warning = ""
    if uncontrollable:
        named = "、".join(
            f"{key} ← {linked[key]['from'] or '?'}"
            + (f" {linked[key]['from_type']}" if linked[key]["from_type"] else "")
            for key in uncontrollable[:3]
        )
        warning = (
            f"该工作流的种子由上游节点提供（{named}），上游节点自身也没有可写的种子参数："
            "固定种子不会生效，每次都会随机"
        )
    return {
        "pinnable": pinnable,
        "linked": linked,
        "uncontrollable": uncontrollable,
        "warning": warning,
    }


# --------------------------------------------------------------------------- #
# Compiled graph audit (fourth preflight layer)
# --------------------------------------------------------------------------- #


@dataclass
class InputTrace:
    node_id: str
    node_type: str
    name: str
    value: Any
    source: str
    ok: bool = True
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "node_id": self.node_id,
            "class_type": self.node_type,
            "input": self.name,
            "source": self.source,
            "source_cn": SOURCE_LABELS_CN.get(self.source, self.source),
            "ok": self.ok,
        }
        # Only keep serialisable, small values; links stay as compact refs.
        if isinstance(self.value, list):
            payload["value"] = self.value[:2]
            payload["value_kind"] = "link"
        else:
            text = repr(self.value)
            payload["value"] = self.value if len(text) <= 160 else text[:157] + "..."
            payload["value_kind"] = type(self.value).__name__
        if self.reason:
            payload["reason"] = self.reason
        return payload


class CompiledGraphAudit:
    """Explain and validate the graph that is about to be submitted.

    This is what turns the plan's "the page value equals the submitted graph"
    from a promise into something a test can assert and a user can read.
    """

    @classmethod
    def run(
        cls,
        graph: dict[str, Any],
        *,
        registry: NodeSchemaRegistry | None = None,
        sources: dict[str, dict[str, str]] | None = None,
        workflow_fingerprint: str = "",
        branch: str = "",
    ) -> dict[str, Any]:
        registry = registry or NodeSchemaRegistry.empty()
        sources = sources or {}
        traces: list[InputTrace] = []
        problems: list[Problem] = []
        #: Inputs whose value is not what the workflow declared: recompiled
        #: parameters and user-chosen replacements. Kept in one list so the
        #: "effective parameters" table shows everything that changed, with the
        #: source column saying who changed it.
        overrides: list[dict[str, Any]] = []
        #: The subset the user chose in the software. Surfaced separately so a
        #: replacement is never mistaken for a normal recompile value.
        replacements: list[dict[str, Any]] = []
        injected: list[dict[str, Any]] = []

        for node_id in sorted(graph, key=lambda value: (len(str(value)), str(value))):
            node = graph[node_id] or {}
            node_type = str(node.get("class_type") or "")
            node_sources = sources.get(str(node_id)) or {}

            if registry and not registry.has(node_type):
                problems.append(Problem(
                    category=ErrorCategory.MISSING_NODE,
                    title="缺少节点",
                    detail=f"提交图包含节点「{node_type}」，但当前 ComfyUI 未安装该节点类型。",
                    node_id=str(node_id),
                    node_type=node_type,
                    fixes=[f"安装提供「{node_type}」的自定义节点后重启 ComfyUI", "改选不依赖该节点的分支"],
                ))
            elif registry:
                # Required inputs that never got a value. This is the check that
                # makes a widget-binding shortfall actionable, and it runs on the
                # final graph so a converter filling the value clears it.
                for name in registry.required_input_names(node_type):
                    if name in (node.get("inputs") or {}):
                        continue
                    problems.append(Problem(
                        category=ErrorCategory.MISSING_INPUT,
                        title="缺少必需输入",
                        detail=f"节点 {node_id}（{node_type}）的必需输入 {name} 在最终提交图中没有值。",
                        node_id=str(node_id),
                        node_type=node_type,
                        input_name=name,
                        fixes=[
                            f"在工作流中为 {name} 设置控件值或连线",
                            "点击「重新检查」刷新节点定义",
                            "在 ComfyUI 中重新导出该工作流",
                        ],
                    ))

            for name, value in (node.get("inputs") or {}).items():
                source = node_sources.get(name, SOURCE_DEFAULT)
                trace = InputTrace(str(node_id), node_type, name, value, source)

                if isinstance(value, list) and len(value) == 2:
                    target = str(value[0])
                    if target not in graph:
                        trace.ok = False
                        trace.reason = f"引用了不存在的节点 {target}"
                        problems.append(Problem(
                            category=ErrorCategory.DANGLING_LINK,
                            title="连线悬空",
                            detail=f"节点 {node_id}.{name} 指向节点 {target}，但该节点不在最终提交图中。",
                            node_id=str(node_id),
                            node_type=node_type,
                            input_name=name,
                            fixes=["检查所选分支是否完整连接", "改选其他分支"],
                        ))
                elif registry and registry.has(node_type):
                    ok, reason, candidates, expected = registry.validate(node_type, name, value)
                    trace.ok = ok
                    trace.reason = reason
                    if not ok:
                        missing_resource = name in RESOURCE_INPUT_HINTS
                        problems.append(Problem(
                            category=ErrorCategory.MISSING_RESOURCE if missing_resource else ErrorCategory.VALUE_OUT_OF_RANGE,
                            title="缺少资源文件" if missing_resource else "参数超出允许范围",
                            detail=(
                                f"节点 {node_id}（{node_type}）引用的文件「{value}」不在当前 ComfyUI 的资源清单中。"
                                if missing_resource
                                else f"节点 {node_id}（{node_type}）的参数 {name} {reason}。"
                            ),
                            node_id=str(node_id),
                            node_type=node_type,
                            input_name=name,
                            received_value=value,
                            candidates=candidates,
                            expected_range=expected,
                            fixes=(
                                [f"改用本机已有候选：{'、'.join(str(item) for item in candidates[:6])}"]
                                if candidates
                                else [f"把 {name} 调整到 {expected}"] if expected
                                else ["在参数工作台修正该参数后重新检查"]
                            ),
                        ))

                traces.append(trace)
                if source in {SOURCE_OVERRIDE, SOURCE_USER_REPLACED, SOURCE_PARAM}:
                    overrides.append(trace.to_dict())
                if source == SOURCE_USER_REPLACED:
                    replacements.append(trace.to_dict())
                elif source == SOURCE_INJECTED:
                    injected.append(trace.to_dict())

        by_node: dict[str, dict[str, Any]] = {}
        for trace in traces:
            entry = by_node.setdefault(trace.node_id, {"class_type": trace.node_type, "inputs": {}})
            entry["inputs"][trace.name] = trace.to_dict()

        return {
            "workflow_fingerprint": workflow_fingerprint,
            "branch": branch,
            "node_count": len(graph),
            "input_count": len(traces),
            "source_counts": _count_sources(traces),
            "overrides": overrides,
            "replacements": replacements,
            "injected": injected,
            "problems": [problem.to_dict() for problem in problems],
            "blocking": [problem.to_dict() for problem in problems if problem.workflow_level],
            "nodes": by_node,
        }


#: Input names that address a file on disk; used to categorise audit problems.
RESOURCE_INPUT_HINTS = frozenset({
    "ckpt_name", "unet_name", "lora_name", "vae_name", "clip_name", "clip_name1",
    "clip_name2", "clip_name3", "model_name", "upscale_model_name", "control_net_name",
})


def _count_sources(traces: list[InputTrace]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for trace in traces:
        counts[trace.source] = counts.get(trace.source, 0) + 1
    return counts


def diff_against_page(audit: dict[str, Any], page_values: dict[str, dict[str, Any]]) -> list[str]:
    """Compare the page's displayed values with the submitted graph.

    Accepts ``{node_id: {input_name: value}}`` and returns human-readable
    mismatches. This is the machine check behind "页面值与最终提交图一致".
    """
    mismatches: list[str] = []
    nodes = audit.get("nodes") or {}
    for node_id, expected_inputs in page_values.items():
        actual = nodes.get(str(node_id))
        if actual is None:
            mismatches.append(f"页面上有节点 {node_id}，但提交图中不存在")
            continue
        for name, expected in expected_inputs.items():
            if actual.get("input") != name:
                continue
            seen = actual.get("value")
            if seen != expected:
                mismatches.append(f"节点 {node_id} 参数 {name}：页面显示 {expected!r}，实际提交 {seen!r}")
    return mismatches
