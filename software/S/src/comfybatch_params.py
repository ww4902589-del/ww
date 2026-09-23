"""Declarative parameter workbench.

The plan's central idea is that this should be a *workbench*, not a fixed-field
batch submitter: the user must be able to see and change the parameters that
actually reach ComfyUI, with four scopes (task / selection / batch / workflow
default).

Everything here is driven by one declarative registry so that a single
definition simultaneously:

* renders the UI control (label, kind, bounds, candidates);
* validates the value against the live ``object_info`` schema;
* writes the value onto the right nodes of the compiled graph;
* explains itself in the audit, tagged as coming from the workbench.

Adding a parameter is one entry in :data:`PARAMS` -- there is no second place to
update, which is what keeps the page, the validation and the submitted graph from
drifting apart.

Parameters are applied by **node class** and, where a workflow has more than one
sampler, by **stage** (一采 / 二采), resolved from the latent chain rather than
from node ids, because node ids carry no execution meaning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from comfybatch_errors import ErrorCategory, Problem
from comfybatch_nodeschema import SOURCE_PARAM

GROUP_COMMON = "common"
GROUP_ADVANCED = "advanced"

GROUP_LABELS_CN = {GROUP_COMMON: "常用参数", GROUP_ADVANCED: "高级参数"}

KIND_INT = "int"
KIND_FLOAT = "float"
KIND_BOOL = "bool"
KIND_ENUM = "enum"
KIND_STRING = "string"

SAMPLER_TYPES = ("KSampler", "KSamplerAdvanced")

#: Input names that make a node a "sampler" for stage ordering.
_LATENT_INPUTS = ("latent_image", "samples")


@dataclass(frozen=True)
class Target:
    """One input on one node class that a parameter writes to."""

    class_type: str
    input_name: str


@dataclass(frozen=True)
class ParamSpec:
    key: str
    label: str
    group: str
    kind: str
    help: str
    targets: tuple[Target, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    #: For enums: where to read the legal candidates from the live schema.
    candidate_from: tuple[str, str] | None = None
    #: ``None`` applies to every matching node; ``1``/``2`` restrict to a stage.
    stage: int | None = None
    #: Whether this may be set per task, per batch, or both.
    task_scope: bool = True

    def to_dict(self, candidates: list[Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "group": self.group,
            "group_cn": GROUP_LABELS_CN.get(self.group, self.group),
            "kind": self.kind,
            "help": self.help,
            "task_scope": self.task_scope,
            "stage": self.stage,
        }
        if self.minimum is not None:
            payload["min"] = self.minimum
        if self.maximum is not None:
            payload["max"] = self.maximum
        if self.step is not None:
            payload["step"] = self.step
        if candidates:
            payload["options"] = list(candidates)[:512]
        if self.targets:
            payload["targets"] = [{"class_type": t.class_type, "input_name": t.input_name} for t in self.targets]
        return payload


#: The parameter workbench. Common first, advanced second; every entry names the
#: exact node inputs it writes to so nothing is guessed at apply time.
PARAMS: dict[str, ParamSpec] = {
    # ---------------------------------------------------------------- 常用
    "base_width": ParamSpec(
        key="base_width", label="初始宽度", group=GROUP_COMMON, kind=KIND_INT,
        help="Latent 的起始宽度（像素）。留空表示沿用工作流自身的设定。",
        targets=(Target("EmptyLatentImage", "width"), Target("EmptySD3LatentImage", "width")),
        minimum=64, maximum=8192, step=8,
    ),
    "base_height": ParamSpec(
        key="base_height", label="初始高度", group=GROUP_COMMON, kind=KIND_INT,
        help="Latent 的起始高度（像素）。留空表示沿用工作流自身的设定。",
        targets=(Target("EmptyLatentImage", "height"), Target("EmptySD3LatentImage", "height")),
        minimum=64, maximum=8192, step=8,
    ),
    "batch_size": ParamSpec(
        key="batch_size", label="每任务张数", group=GROUP_COMMON, kind=KIND_INT,
        help="一次提交生成几张。数量越多显存占用越高。",
        targets=(Target("EmptyLatentImage", "batch_size"), Target("EmptySD3LatentImage", "batch_size")),
        minimum=1, maximum=16, step=1, task_scope=False,
    ),
    "steps": ParamSpec(
        key="steps", label="采样步数", group=GROUP_COMMON, kind=KIND_INT,
        help="作用于全部采样器。要分别设置一采/二采请用下面的「一采步数」「二采步数」。",
        targets=(Target("KSampler", "steps"), Target("KSamplerAdvanced", "steps")),
        minimum=1, maximum=200, step=1,
    ),
    "cfg": ParamSpec(
        key="cfg", label="CFG", group=GROUP_COMMON, kind=KIND_FLOAT,
        help="提示词引导强度。越高越贴合提示词，过高会发灰或崩坏。",
        targets=(Target("KSampler", "cfg"), Target("KSamplerAdvanced", "cfg")),
        minimum=0.0, maximum=30.0, step=0.1,
    ),
    "denoise": ParamSpec(
        key="denoise", label="降噪", group=GROUP_COMMON, kind=KIND_FLOAT,
        help="重绘幅度。1.0 全量重绘，越低越接近原图。",
        targets=(Target("KSampler", "denoise"), Target("KSamplerAdvanced", "denoise")),
        minimum=0.0, maximum=1.0, step=0.01,
    ),
    "upscale_by": ParamSpec(
        key="upscale_by", label="放大倍数", group=GROUP_COMMON, kind=KIND_FLOAT,
        help="高清重绘分支的放大倍数。预计输出尺寸会按它换算。",
        targets=(Target("UltimateSDUpscale", "upscale_by"), Target("UltimateSDUpscaleNoUpscale", "upscale_by")),
        minimum=1.0, maximum=8.0, step=0.05, task_scope=False,
    ),
    "upscale_model": ParamSpec(
        key="upscale_model", label="放大模型", group=GROUP_COMMON, kind=KIND_ENUM,
        help="本机可用的放大模型。列表来自 ComfyUI 的真实资源清单。",
        targets=(Target("UpscaleModelLoader", "model_name"),),
        candidate_from=("UpscaleModelLoader", "model_name"), task_scope=False,
    ),
    "tile_size": ParamSpec(
        key="tile_size", label="分块尺寸", group=GROUP_COMMON, kind=KIND_INT,
        help="分块放大的块边长。显存不足时调小。",
        targets=(Target("UltimateSDUpscale", "tile_width"), Target("UltimateSDUpscale", "tile_height")),
        minimum=64, maximum=4096, step=64, task_scope=False,
    ),
    "latent_upscale_by": ParamSpec(
        key="latent_upscale_by", label="潜空间放大倍数", group=GROUP_COMMON, kind=KIND_FLOAT,
        help="二采前的潜空间放大。影响预计输出尺寸。",
        targets=(Target("LatentUpscaleBy", "scale_by"),),
        minimum=1.0, maximum=3.0, step=0.05, task_scope=False,
    ),
    # ---------------------------------------------------------------- 高级
    "steps_stage1": ParamSpec(
        key="steps_stage1", label="一采步数", group=GROUP_ADVANCED, kind=KIND_INT,
        help="第一个采样器的步数。",
        targets=(Target("KSampler", "steps"), Target("KSamplerAdvanced", "steps")),
        minimum=1, maximum=200, step=1, stage=1,
    ),
    "steps_stage2": ParamSpec(
        key="steps_stage2", label="二采步数", group=GROUP_ADVANCED, kind=KIND_INT,
        help="第二个采样器的步数。二采流程才有。",
        targets=(Target("KSampler", "steps"), Target("KSamplerAdvanced", "steps")),
        minimum=1, maximum=200, step=1, stage=2,
    ),
    "sampler_name": ParamSpec(
        key="sampler_name", label="采样器", group=GROUP_ADVANCED, kind=KIND_ENUM,
        help="候选来自当前 ComfyUI 的采样器清单。",
        targets=(Target("KSampler", "sampler_name"), Target("KSamplerAdvanced", "sampler_name")),
        candidate_from=("KSampler", "sampler_name"),
    ),
    "scheduler": ParamSpec(
        key="scheduler", label="调度器", group=GROUP_ADVANCED, kind=KIND_ENUM,
        help="噪声调度方式。候选来自当前 ComfyUI。",
        targets=(Target("KSampler", "scheduler"), Target("KSamplerAdvanced", "scheduler")),
        candidate_from=("KSampler", "scheduler"),
    ),
    "start_at_step": ParamSpec(
        key="start_at_step", label="起始步", group=GROUP_ADVANCED, kind=KIND_INT,
        help="从第几步开始采样（高级采样器）。",
        targets=(Target("KSamplerAdvanced", "start_at_step"),),
        minimum=0, maximum=10000, step=1, task_scope=False,
    ),
    "end_at_step": ParamSpec(
        key="end_at_step", label="结束步", group=GROUP_ADVANCED, kind=KIND_INT,
        help="在第几步结束采样（高级采样器）。",
        targets=(Target("KSamplerAdvanced", "end_at_step"),),
        minimum=0, maximum=10000, step=1, task_scope=False,
    ),
    "add_noise": ParamSpec(
        key="add_noise", label="是否加噪", group=GROUP_ADVANCED, kind=KIND_ENUM,
        help="高级采样器是否在开始时添加噪声。",
        targets=(Target("KSamplerAdvanced", "add_noise"),),
        candidate_from=("KSamplerAdvanced", "add_noise"),
    ),
    "return_with_leftover_noise": ParamSpec(
        key="return_with_leftover_noise", label="保留残余噪声", group=GROUP_ADVANCED, kind=KIND_ENUM,
        help="二采衔接用。二采要求这里为 enable。",
        targets=(Target("KSamplerAdvanced", "return_with_leftover_noise"),),
        candidate_from=("KSamplerAdvanced", "return_with_leftover_noise"),
    ),
    "mode_type": ParamSpec(
        key="mode_type", label="分块顺序", group=GROUP_ADVANCED, kind=KIND_ENUM,
        help="分块放大的遍历顺序。",
        targets=(Target("UltimateSDUpscale", "mode_type"),),
        candidate_from=("UltimateSDUpscale", "mode_type"), task_scope=False,
    ),
    "mask_blur": ParamSpec(
        key="mask_blur", label="遮罩模糊", group=GROUP_ADVANCED, kind=KIND_INT,
        help="分块接缝处的遮罩模糊半径。",
        targets=(Target("UltimateSDUpscale", "mask_blur"),),
        minimum=0, maximum=256, step=1, task_scope=False,
    ),
    "tile_padding": ParamSpec(
        key="tile_padding", label="分块边距", group=GROUP_ADVANCED, kind=KIND_INT,
        help="分块之间的重叠边距。越大接缝越少，越慢。",
        targets=(Target("UltimateSDUpscale", "tile_padding"),),
        minimum=0, maximum=256, step=1, task_scope=False,
    ),
    "seam_fix_mode": ParamSpec(
        key="seam_fix_mode", label="接缝模式", group=GROUP_ADVANCED, kind=KIND_ENUM,
        help="接缝修复方式。",
        targets=(Target("UltimateSDUpscale", "seam_fix_mode"),),
        candidate_from=("UltimateSDUpscale", "seam_fix_mode"), task_scope=False,
    ),
    "seam_fix_denoise": ParamSpec(
        key="seam_fix_denoise", label="接缝降噪", group=GROUP_ADVANCED, kind=KIND_FLOAT,
        help="接缝修复时的降噪强度。",
        targets=(Target("UltimateSDUpscale", "seam_fix_denoise"),),
        minimum=0.0, maximum=1.0, step=0.01, task_scope=False,
    ),
    "seam_fix_width": ParamSpec(
        key="seam_fix_width", label="接缝宽度", group=GROUP_ADVANCED, kind=KIND_INT,
        help="接缝修复的宽度。",
        targets=(Target("UltimateSDUpscale", "seam_fix_width"),),
        minimum=0, maximum=256, step=1, task_scope=False,
    ),
    "force_uniform_tiles": ParamSpec(
        key="force_uniform_tiles", label="统一分块", group=GROUP_ADVANCED, kind=KIND_BOOL,
        help="强制等大分块，边缘不额外扩展。",
        targets=(Target("UltimateSDUpscale", "force_uniform_tiles"),), task_scope=False,
    ),
    "tiled_decode": ParamSpec(
        key="tiled_decode", label="分块解码", group=GROUP_ADVANCED, kind=KIND_BOOL,
        help="解码时分块处理，省显存但可能略慢。",
        targets=(Target("UltimateSDUpscale", "tiled_decode"),), task_scope=False,
    ),
}

TASK_KEYS = tuple(key for key, spec in PARAMS.items() if spec.task_scope)
BATCH_ONLY_KEYS = tuple(key for key, spec in PARAMS.items() if not spec.task_scope)


def registry_payload(registry: Any = None) -> dict[str, Any]:
    """The workbench definition, with enum candidates read from the live schema."""
    groups: dict[str, list[dict[str, Any]]] = {GROUP_COMMON: [], GROUP_ADVANCED: []}
    for spec in PARAMS.values():
        candidates: list[Any] = []
        if spec.candidate_from and registry is not None:
            try:
                candidates = list(registry.combo_candidates(*spec.candidate_from))
            except Exception:  # noqa: BLE001 - candidates are best-effort context
                candidates = []
        groups.setdefault(spec.group, []).append(spec.to_dict(candidates))
    return {
        "groups": [{"id": gid, "label": GROUP_LABELS_CN[gid], "params": items} for gid, items in groups.items() if items],
        "task_scoped_keys": list(TASK_KEYS),
        "batch_only_keys": list(BATCH_ONLY_KEYS),
    }


def coerce(spec: ParamSpec, value: Any) -> Any:
    """Convert ``value`` to the spec's kind, or raise ``ValueError``."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        if spec.kind == KIND_INT:
            number = int(float(value))
        elif spec.kind == KIND_FLOAT:
            number = float(value)
        elif spec.kind == KIND_BOOL:
            if isinstance(value, str):
                return value.strip().lower() in {"true", "1", "yes", "on", "是", "启用"}
            return bool(value)
        else:
            return str(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{spec.label} 需要一个{'整数' if spec.kind == KIND_INT else '数字'}，收到 {value!r}") from exc
    if spec.minimum is not None and number < spec.minimum:
        raise ValueError(f"{spec.label} 不能小于 {spec.minimum}（收到 {number}）")
    if spec.maximum is not None and number > spec.maximum:
        raise ValueError(f"{spec.label} 不能大于 {spec.maximum}（收到 {number}）")
    return number


def resolve(overrides: dict[str, Any] | None, registry: Any = None) -> tuple[dict[str, Any], list[Problem]]:
    """Validate a raw override mapping into typed values.

    Unknown keys are reported rather than ignored: a typo in a parameter name
    must not look like "the setting had no effect".
    """
    values: dict[str, Any] = {}
    problems: list[Problem] = []
    for raw_key, raw_value in (overrides or {}).items():
        key = str(raw_key)
        spec = PARAMS.get(key)
        if spec is None:
            problems.append(Problem(
                category=ErrorCategory.PARAMETER_MISALIGNED,
                title="未知的参数名",
                detail=f"参数工作台里没有名为「{key}」的参数，已忽略以免改写错误的节点输入。",
                input_name=key,
                severity="warning",
                fixes=["检查参数名拼写", "在参数工作台里选择已有参数"],
            ))
            continue
        try:
            coerced = coerce(spec, raw_value)
        except ValueError as exc:
            problems.append(Problem(
                category=ErrorCategory.VALUE_OUT_OF_RANGE,
                title="参数超出允许范围",
                detail=str(exc),
                input_name=key,
                received_value=raw_value,
                expected_range=(
                    f"{spec.minimum if spec.minimum is not None else '-∞'} ~ {spec.maximum if spec.maximum is not None else '+∞'}"
                ),
                fixes=[f"把{spec.label}调整到允许范围内"],
            ))
            continue
        if coerced is None:
            continue
        if spec.kind == KIND_ENUM and registry is not None and spec.candidate_from:
            try:
                candidates = list(registry.combo_candidates(*spec.candidate_from))
            except Exception:  # noqa: BLE001
                candidates = []
            if candidates and coerced not in candidates:
                problems.append(Problem(
                    category=ErrorCategory.VALUE_OUT_OF_RANGE,
                    title="参数不在候选列表中",
                    detail=f"{spec.label} 的「{coerced}」不在当前 ComfyUI 的候选列表里。",
                    input_name=key,
                    received_value=coerced,
                    candidates=candidates,
                    fixes=[f"改用允许的候选值：{'、'.join(str(item) for item in candidates[:8])}"],
                ))
                continue
        values[key] = coerced
    return values, problems


def sampler_stages(graph: dict[str, Any]) -> dict[str, int]:
    """Map sampler node id -> 1-based stage, ordered along the latent chain.

    A workflow with a second sampling pass feeds the first sampler's latent into
    the second. Deriving the order from that chain is what makes 一采步数 and
    二采步数 meaningful; node ids carry no execution meaning, so they cannot be
    used for this.
    """
    samplers = [node_id for node_id, node in graph.items() if node.get("class_type") in SAMPLER_TYPES]
    if not samplers:
        return {}

    def upstream_samplers(start: str) -> set[str]:
        """Samplers reachable by walking backwards from a node's latent input."""
        found: set[str] = set()
        pending = [start]
        seen: set[str] = set()
        while pending:
            node_id = pending.pop()
            if node_id in seen or node_id not in graph:
                continue
            seen.add(node_id)
            node = graph[node_id]
            if node.get("class_type") in SAMPLER_TYPES and node_id != start:
                found.add(node_id)
            for name in _LATENT_INPUTS:
                value = (node.get("inputs") or {}).get(name)
                if isinstance(value, list) and len(value) == 2:
                    pending.append(str(value[0]))
        return found

    depth = {node_id: len(upstream_samplers(node_id)) for node_id in samplers}
    ordered = sorted(samplers, key=lambda node_id: (depth[node_id], len(node_id), node_id))
    return {node_id: index + 1 for index, node_id in enumerate(ordered)}


def apply_params(
    graph: dict[str, Any],
    overrides: dict[str, Any] | None,
    registry: Any = None,
    sources: dict[str, dict[str, str]] | None = None,
    source_tag: str = SOURCE_PARAM,
) -> list[Problem]:
    """Write validated parameters onto the compiled graph.

    Returns the problems found while resolving (unknown names, out-of-range
    values). Applied inputs are recorded in ``sources`` so the audit shows them
    as the user's workbench choices rather than as workflow values.
    """
    values, problems = resolve(overrides, registry)
    if not values:
        return problems

    stages = sampler_stages(graph) if any(PARAMS[key].stage for key in values) else {}
    by_class: dict[str, list[str]] = {}
    for node_id, node in graph.items():
        by_class.setdefault(str(node.get("class_type") or ""), []).append(node_id)

    matched: set[str] = set()
    skipped: list[tuple[str, str, str, str]] = []
    for key, value in values.items():
        spec = PARAMS[key]
        for target in spec.targets:
            for node_id in by_class.get(target.class_type, []):
                if spec.stage is not None and stages.get(node_id) != spec.stage:
                    continue
                node = graph[node_id]
                inputs = node.setdefault("inputs", {})
                current = inputs.get(target.input_name)
                if isinstance(current, list) and len(current) == 2:
                    # A linked input belongs to the node that produces it.
                    # Overwriting it would silently break the wiring, so it is
                    # skipped -- but the user has to be told, otherwise a setting
                    # that "did nothing" looks like a bug in the software.
                    driver = graph.get(str(current[0]), {})
                    skipped.append((spec.label, key, node_id, str(driver.get("class_type") or current[0])))
                    continue
                inputs[target.input_name] = value
                matched.add(key)
                if sources is not None:
                    sources.setdefault(node_id, {})[target.input_name] = source_tag

    # A parameter that matched nothing at all is worth saying out loud too: the
    # chosen branch may simply not contain that node kind.
    for key, spec in PARAMS.items():
        if key not in values or key in matched or any(entry[1] == key for entry in skipped):
            continue
        problems.append(Problem(
            category=ErrorCategory.PARAMETER_MISALIGNED,
            title="参数在当前分支没有对应节点",
            detail=f"「{spec.label}」在当前选中的分支里找不到可写入的节点，已忽略。",
            input_name=key,
            received_value=values[key],
            severity="warning",
            fixes=["确认当前分支是否包含该参数对应的节点", "把参数改成当前分支支持的项"],
        ))

    for label, _key, node_id, driver in skipped[:8]:
        problems.append(Problem(
            category=ErrorCategory.PARAMETER_MISALIGNED,
            title="参数未生效：该输入由其他节点驱动",
            detail=(
                f"「{label}」对应的输入（节点 {node_id}）是指向「{driver}」的连线，"
                f"它的值由那个节点决定，所以这里改不了。"
            ),
            node_id=node_id,
            node_type=driver,
            input_name=_key,
            severity="warning",
            fixes=[
                "改用驱动它的那个节点的参数（例如尺寸由 ResolutionSelector 驱动时，请改「画幅」与「百万像素」）",
                "在 ComfyUI 里断开该连线后重新导出工作流",
            ],
        ))
    return problems
