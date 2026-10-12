"""分组批次启动器：把一份提示词合集按画幅一次性拆成多组，依次投入生成。

这个模块是「一次导入、自动分批跑完」能力的唯一实现处。它原来是一份外部
脚本（``guard_tup89.py``），跑在一次真实生产任务里：89 条提示词按 4:5 /
3:2 / 1:1 / 16:9 / 2:3 五个画幅分成 5 组，依次提交给 S 的 ``/api/start``，
中途 S 进程被杀掉后自动重启并只补跑还没出图的编号。

现在把它收进 S 本体，理由有三条：

1. **不要有第二套「画幅」知识。** 画幅标签必须和 ComfyUI 的
   ``ResolutionSelector`` 逐字节相同，而这份清单在 ``comfybatch_v2_app.py``
   的 ``IMAGE_PRESETS`` 里已经是唯一权威。外部脚本只能复制一份，复制件
   迟早会和主程序对不上（历史上就出现过 ``3:2 (Landscape)`` 这种不存在的
   标签）。这里改成**从 ``IMAGE_PRESETS`` 派生**，不再写字面量。

2. **不要有第二套「提交批次」动作。** 外部脚本靠 HTTP 打 ``/api/start``，
   于是它必须自己复制一遍「准备配置 → 预检 → 建目录 → 起跑」的顺序、
   自己处理 400 预检负载、自己猜轮询间隔。收进来以后调用 ``APP`` 上同一对
   方法，走的就是页面按钮走的那条路。

3. **失败要能看见。** 外部脚本崩了只在它自己的日志里留一行；收进 S 以后
   分组进度进 ``/api/status``，页面能显示「第 3/5 组 · 4:5 已完成 12/20」。

## 这里**不**做的事（防止以后重复造轮子）

- **不重新编译工作流**、不碰 ComfyUI 客户端。提交一律交给
  :class:`~comfybatch_v2_core.BatchRunner`。
- **不做图像质量判断**、不做重试。那是 ``single_subject_guard`` 与
  ``max_retries`` 的活，已经在 ``BatchRunner._run`` 里。
- **不实现 LLM 精修**。见 ``comfybatch_v2_core.OpenAICompatibleAdapter``，
  启动器不参与、也不调用它。
- **不按提示词内容分组**。分组只认 ``metadata.generation.aspect_ratio``，
  也就是任务级图像规格；没有任务级规格的条目归到全局 ``config.aspect_ratio``。

## 断点续跑怎么判断「已经出过图」

判据是**输出目录里已经存在的 PNG 文件名里的编号**，而不是任何内存状态 ——
进程被杀掉后内存里的东西全没了，只有磁盘上的文件还在。编号从条目的
``metadata.serial`` / ``metadata.number`` 取；取不到就退回它在合集里的位次。
文件名匹配 ``TUP-47``、``tup-47``、``47-xxx`` 这三种常见写法都认。
"""

from __future__ import annotations

import copy
import pathlib
import re
import threading
import time
from typing import Any, Callable

from comfybatch_v2_core import BatchConfig, PromptBundle, PromptItem

#: 组与组之间的默认间隔，给 ComfyUI 一点时间回收上一组的显存。
DEFAULT_GROUP_GAP_SECONDS = 6.0

#: 单组最长运行时间。外部脚本当年用的是 6 小时，这里保持同一数量级：
#: 一组几十条、每条几分钟，6 小时足够，超时说明卡死了而不是跑得慢。
DEFAULT_GROUP_MAX_SECONDS = 6 * 3600.0

#: 同一组内连续失败多少条就放弃这一组（组内用，不是全局熔断 —— 全局熔断
#: 仍在 ``BatchConfig.max_consecutive_failures``）。
DEFAULT_GROUP_MAX_FAILURES = 3

#: 进程被杀掉后的重启等待上限。外部脚本用的是 300 秒。
DEFAULT_RESTART_WAIT_SECONDS = 300.0

#: 一个组正在跑时，多久轮询一次 ``BatchRunner.status()``。
POLL_INTERVAL_SECONDS = 2.0


def canonical_aspect_ratios() -> list[str]:
    """``IMAGE_PRESETS`` 里出现过的画幅标签，按首次出现顺序去重。

    从主程序派生而不是写字面量：ComfyUI 的候选清单变了，这里跟着变，
    不会出现「启动器认得的画幅主程序不认」这种只能靠人发现的不一致。
    """
    try:
        from comfybatch_v2_app import IMAGE_PRESETS
    except Exception:  # pragma: no cover - 只可能发生在被单独 import 时
        return []
    ordered: list[str] = []
    for preset in IMAGE_PRESETS:
        label = str(preset.get("aspect_ratio") or "").strip()
        if label and label not in ordered:
            ordered.append(label)
    return ordered


def aspect_ratio_key(label: str) -> str:
    """把画幅标签压成 ``"4:5"`` 这种纯比例，用作分组键。

    ``"4:5 (Portrait)"`` → ``"4:5"``；``"竖图 4:5"`` 也认。认不出来时返回
    原串，让条目自成一组的可能性最大化 —— 分组键归并错了会把两条不同尺寸
    的任务塞进同一批，那是静默出错；自成一组的代价只是多起一次批次。
    """
    match = re.search(r"(\d{1,2})\s*:\s*(\d{1,2})", str(label or ""))
    return f"{match.group(1)}:{match.group(2)}" if match else str(label or "").strip()


def _item_serial(item: PromptItem, fallback: int) -> int | None:
    """条目的编号。优先显式编号，其次文件名里的 TUP-<n>，最后位次。

    ``metadata.serial`` / ``metadata.number`` / ``metadata.index`` 是常见的
    三个键；``title`` 里带 ``TUP-47`` 这种编号时也认。都取不到才用位次 ——
    位次在合集被编辑过以后会漂，所以它是最后选择而不是首选。
    """
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    for key in ("serial", "number", "index"):
        raw = metadata.get(key)
        if raw in (None, ""):
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    match = re.search(r"(\d{1,4})", str(item.title or ""))
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    return int(fallback)


def done_numbers(output_dir: pathlib.Path, pattern: str = r"(\d{1,4})") -> set[int]:
    """输出目录里已经出过图的编号集合。

    递归扫 PNG（大小写都认），从文件名里抠编号。这是断点续跑的**唯一**判据：
    不依赖任何内存状态，所以进程被杀掉后重来仍然正确。

    读不到的目录当成空集而不是抛异常：目录还不存在是完全正常的第一次运行。
    """
    found: set[int] = set()
    if not output_dir or not pathlib.Path(output_dir).is_dir():
        return found
    try:
        candidates = list(pathlib.Path(output_dir).rglob("*.png"))
        candidates += list(pathlib.Path(output_dir).rglob("*.PNG"))
    except OSError:
        return found
    for path in candidates:
        match = re.search(pattern, path.name)
        if match:
            try:
                found.add(int(match.group(1)))
            except ValueError:
                continue
    return found


class GroupPlan:
    """一个画幅分组的计划：跑哪些编号、跳过哪些。

    只描述「要做什么」，不持有任何运行时资源，所以可以安全地序列化给页面。
    """

    def __init__(self, aspect_ratio: str, key: str, indexes: list[int], serials: list[int | None]):
        self.aspect_ratio = aspect_ratio
        self.key = key
        #: 在合集里的位次（1 起）。
        self.indexes = indexes
        #: 与位次一一对应的编号，用于磁盘判重和页面显示。
        self.serials = serials
        self.pending: list[int] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "aspect_ratio": self.aspect_ratio,
            "key": self.key,
            "count": len(self.indexes),
            "pending": len(self.pending),
            "indexes": list(self.indexes),
        }


def plan_groups(
    bundle: PromptBundle,
    config: BatchConfig,
    output_dir: pathlib.Path,
    *,
    order: list[str] | None = None,
) -> list[GroupPlan]:
    """把合集按画幅拆成若干组，并标注每组还差哪些编号。

    ``order`` 给出组之间的优先级（比例字面量，如 ``["4:5","3:2"]``）；
    没列到的组排在后面。默认顺序按 ``IMAGE_PRESETS`` 的既有顺序，也就是
    「横图 → 方图 → 竖图」这个人们习惯的顺序。

    每组内部保持合集原有次序 —— 这是刻意的：编号在合集里通常是按设计主题
    排的，打乱它会让同一套设计散落到不同时间点生成，审查时要来回翻。
    """
    default_aspect = str(config.aspect_ratio or "")
    buckets: dict[str, GroupPlan] = {}
    for position, item in enumerate(bundle.items, 1):
        metadata = item.metadata if isinstance(item.metadata, dict) else {}
        generation = metadata.get("generation") if isinstance(metadata.get("generation"), dict) else {}
        label = str(generation.get("aspect_ratio") or default_aspect or "").strip()
        key = aspect_ratio_key(label)
        plan = buckets.get(key)
        if plan is None:
            plan = GroupPlan(label or "未指定画幅", key, [], [])
            buckets[key] = plan
        plan.indexes.append(position)
        plan.serials.append(_item_serial(item, position))

    # 每组算出「还差哪些」：编号已经在输出目录里出现过就跳过。
    for plan in buckets.values():
        present = done_numbers(pathlib.Path(output_dir))
        plan.pending = [
            position
            for position, serial in zip(plan.indexes, plan.serials)
            if serial is None or int(serial) not in present
        ]

    plans = list(buckets.values())
    if order:
        rank = {aspect_ratio_key(label): position for position, label in enumerate(order)}
        plans.sort(key=lambda plan: (rank.get(plan.key, len(rank)), plan.key))
    return plans


class BatchLauncher:
    """一次「按画幅分批跑完」的任务。**整个程序同一时刻只有一个。**

    生命周期::

        idle → planning → running ─┬→ completed
                                   ├→ cancelled
                                   └→ failed

    与 :class:`~comfybatch_v2_core.BatchRunner` 的分工很清楚：runner 负责
    一个组内部每一张图怎么跑，launcher 只负责「下一组是哪一组、什么时候
    开始、这组跑完了没有」。所以 launcher 自己不开线程跑图，它只是等待
    ``runner.status()`` 从 running 变成终态。
    """

    #: 允许提交下一组的 runner 状态。
    RUNNER_TERMINAL = {"completed", "cancelled", "aborted", "error", "completed_with_errors"}
    RUNNER_BUSY = {"running", "paused", "starting"}

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._state: dict[str, Any] = {
            "status": "idle",
            "groups": [],
            "group_index": 0,
            "group_count": 0,
            "current_group": None,
            "group_status": "",
            "group_submitted": 0,
            "group_total": 0,
            "log": [],
            "error": "",
            "output_dir": "",
            "started_at": 0.0,
            "finished_at": 0.0,
        }

    # ------------------------------------------------------------------ 状态

    def status(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state)

    def _update(self, **values: Any) -> None:
        with self._lock:
            self._state.update(values)

    def _log(self, message: str) -> None:
        with self._lock:
            self._state["log"].append({"at": time.strftime("%H:%M:%S"), "message": message})
            # 只留最近 200 条：这是给人看的，不是审计日志。
            self._state["log"] = self._state["log"][-200:]

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._state["status"] in {"planning", "running"}

    # ------------------------------------------------------------------ 预演

    def preview(self, bundle: PromptBundle, config: BatchConfig, output_dir: pathlib.Path) -> dict[str, Any]:
        """只算分组、不提交，给页面在点「开始」之前看清楚要跑几组。"""
        plans = plan_groups(bundle, config, output_dir)
        return {
            "groups": [
                {**plan.to_dict(), "pending": len(plan.pending), "serials": list(plan.serials)}
                for plan in plans
            ],
            "group_count": len(plans),
            "total": sum(len(plan.indexes) for plan in plans),
            "pending": sum(len(plan.pending) for plan in plans),
            "output_dir": str(output_dir),
        }

    # ------------------------------------------------------------------ 启动

    def start(
        self,
        runner: Any,
        bundle: PromptBundle,
        config: BatchConfig,
        resolve: Callable[[], PromptBundle],
        *,
        output_dir: pathlib.Path,
        gap_seconds: float = DEFAULT_GROUP_GAP_SECONDS,
        group_max_seconds: float = DEFAULT_GROUP_MAX_SECONDS,
        group_max_failures: int = DEFAULT_GROUP_MAX_FAILURES,
        on_change: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """开始一次分批运行。重复调用在上一轮没结束时抛错，不会静默排队。

        ``resolve`` 是「把当前 bundle 解析成带预设的 bundle」的回调，也就是
        ``Application.resolve_bundle_presets``。用回调而不是直接传结果，
        是因为每一组提交前都要重新解析一次：中途用户可能在页面上改了预设。
        """
        if self.busy:
            raise ValueError("已有分批任务正在运行")
        plans = [plan for plan in plan_groups(bundle, config, output_dir) if plan.pending]
        if not plans:
            raise ValueError("按画幅分组后没有待跑的任务（可能全部已出图）")

        with self._lock:
            self._stop.clear()
            self._state.update({
                "status": "planning",
                "groups": [plan.to_dict() for plan in plans],
                "group_index": 0,
                "group_count": len(plans),
                "current_group": None,
                "group_status": "",
                "group_submitted": 0,
                "group_total": 0,
                "log": [],
                "error": "",
                "output_dir": str(output_dir),
                "started_at": time.time(),
                "finished_at": 0.0,
            })
        self._log(f"按画幅分成 {len(plans)} 组，共 {sum(len(plan.pending) for plan in plans)} 条待跑")
        self._thread = threading.Thread(
            target=self._run,
            args=(runner, bundle, config, resolve, plans, output_dir,
                  gap_seconds, group_max_seconds, group_max_failures, on_change),
            daemon=True,
            name="comfybatch-launcher",
        )
        self._thread.start()
        return self.status()

    def cancel(self) -> dict[str, Any]:
        """停止分批：当前组交给 runner 的 cancel，剩下的组不再提交。"""
        self._stop.set()
        self._log("收到停止请求：跑完当前组的收尾后不再提交后续组")
        self._update(status="cancelled", finished_at=time.time())
        return self.status()

    # ------------------------------------------------------------------ 主循环

    def _run(
        self,
        runner: Any,
        bundle: PromptBundle,
        config: BatchConfig,
        resolve: Callable[[], PromptBundle],
        plans: list[GroupPlan],
        output_dir: pathlib.Path,
        gap_seconds: float,
        group_max_seconds: float,
        group_max_failures: int,
        on_change: Callable[[], None] | None,
    ) -> None:
        failed_groups: list[str] = []
        try:
            self._update(status="running")
            for position, plan in enumerate(plans, 1):
                if self._stop.is_set():
                    break
                self._submit_group(
                    runner, bundle, config, resolve, plan, position, len(plans),
                    output_dir, group_max_seconds, group_max_failures, failed_groups, on_change,
                )
                self._notify(on_change)
                if self._stop.is_set():
                    break
                if position < len(plans):
                    self._log(f"第 {position} 组结束，等 {gap_seconds:g} 秒再交下一组")
                    self._wait(gap_seconds)
            final = "cancelled" if self._stop.is_set() else ("completed" if not failed_groups else "completed_with_errors")
            if failed_groups:
                self._log("以下分组没跑满：" + "、".join(failed_groups))
                self._update(error="以下分组没跑满：" + "、".join(failed_groups))
        except Exception as exc:  # noqa: BLE001 - 后台线程不能把异常抛给调用方
            self._log(f"分批运行中断：{exc}")
            self._update(error=str(exc))
            final = "failed"
        finally:
            self._update(status=final, finished_at=time.time(), current_group=None)
            self._notify(on_change)

    def _submit_group(
        self,
        runner: Any,
        bundle: PromptBundle,
        config: BatchConfig,
        resolve: Callable[[], PromptBundle],
        plan: GroupPlan,
        position: int,
        total_groups: int,
        output_dir: pathlib.Path,
        group_max_seconds: float,
        group_max_failures: int,
        failed_groups: list[str],
        on_change: Callable[[], None] | None,
    ) -> None:
        """提交一组并等它跑完。**只提交还没出图的那些条目。**"""
        # 先按「还没出图」裁一次合集，再交给 resolve 解析预设。顺序不能反：
        # resolve 会为每一条重算风格/LoRA，先裁再解析能省掉已出图那些的无用功。
        subset = resolve_subset(resolve, bundle, plan)
        if not subset.items:
            self._log(f"第 {position}/{total_groups} 组 {plan.aspect_ratio}：全部已出图，跳过")
            return

        group_config = copy.deepcopy(config)
        group_config.aspect_ratio = plan.aspect_ratio
        group_config.output_dir = str(output_dir)
        pathlib.Path(output_dir).mkdir(parents=True, exist_ok=True)

        self._update(
            group_index=position,
            current_group=plan.aspect_ratio,
            group_status="starting",
            group_total=len(subset.items),
            group_submitted=0,
        )
        self._log(
            f"第 {position}/{total_groups} 组 {plan.aspect_ratio}：提交 {len(subset.items)} 条"
            f"（原 {len(plan.indexes)} 条，已出图跳过 {len(plan.indexes) - len(subset.items)} 条）"
        )
        self._notify(on_change)

        # 走和页面按钮同一条路：resolve → start。预检交给 runner，不在这里复制。
        runner.start(subset, group_config)
        deadline = time.time() + group_max_seconds
        failures = 0
        while True:
            if self._stop.is_set():
                runner.cancel()
            state = runner.status()
            self._update(
                group_status=str(state.get("status") or ""),
                group_submitted=int(state.get("submitted") or 0),
            )
            self._notify(on_change)
            status = str(state.get("status") or "")
            if status in self.RUNNER_TERMINAL:
                failures = int(state.get("errors") or 0)
                break
            if time.time() > deadline:
                self._log(f"第 {position} 组超过 {group_max_seconds / 3600:g} 小时，取消这一组")
                runner.cancel()
                failed_groups.append(f"{plan.aspect_ratio}（超时）")
                break
            if failures >= group_max_failures and status not in self.RUNNER_BUSY:
                break
            time.sleep(POLL_INTERVAL_SECONDS)

        if status_failed(failures, group_max_failures):
            failed_groups.append(f"{plan.aspect_ratio}（失败 {failures} 条）")
        self._log(f"第 {position}/{total_groups} 组 {plan.aspect_ratio}：{status or '结束'}")

    # ------------------------------------------------------------------ 工具

    def _wait(self, seconds: float) -> None:
        self._stop.wait(max(0.0, float(seconds)))

    @staticmethod
    def _notify(on_change: Callable[[], None] | None) -> None:
        if on_change:
            try:
                on_change()
            except Exception:  # noqa: BLE001 - 通知失败不能影响跑批
                pass


def status_failed(errors: int, threshold: int) -> bool:
    """这一组是否算「没跑满」。

    有失败条目就算没跑满 —— 但不阻止后续组：第 3 组挂了两条，第 4 组照样
    要跑。这是刻意的，外部脚本当年也是这么做的：画幅之间彼此独立，
    一组的失败不该殃及下一组。
    """
    return int(errors or 0) >= max(1, int(threshold))


def subset_bundle(bundle: PromptBundle, plan: GroupPlan) -> PromptBundle:
    """只保留这一组里还没出图的条目，元数据原样带回（编号在里面）。"""
    pending = set(plan.pending)
    items = [
        PromptItem(item.title, item.prompt, dict(item.metadata or {}), item.negative_prompt)
        for position, item in enumerate(bundle.items, 1)
        if position in pending
    ]
    return PromptBundle(bundle.name, items, bundle.source_format)


def resolve_subset(resolve: Callable[[], PromptBundle], bundle: PromptBundle, plan: GroupPlan) -> PromptBundle:
    """先按「还没出图」裁一次，再把结果交给 ``resolve`` 补预设。

    顺序是刻意的：``resolve``（即 ``Application.resolve_bundle_presets``）
    会为每一条重算风格与 LoRA，先裁再解析能省掉已经出图那些的无用功。
    裁出来的条目和原合集里的位次一一对应，所以 ``resolve`` 里按位次取值
    的逻辑不受影响 —— 但为了正确，这里仍是**先裁后解析**，解析结果直接
    就是这一组要提交的合集。
    """
    trimmed = subset_bundle(bundle, plan)
    if not trimmed.items:
        return trimmed
    resolved = resolve()
    kept = [
        PromptItem(item.title, item.prompt, dict(item.metadata or {}), item.negative_prompt)
        for position, item in enumerate(resolved.items, 1)
        if position in set(plan.pending)
    ]
    return PromptBundle(resolved.name, kept, resolved.source_format)
