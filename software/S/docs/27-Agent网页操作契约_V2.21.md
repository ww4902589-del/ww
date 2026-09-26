# Agent 网页操作层：第一段 DOM 契约（任务 #14）

## 目的与接口

本段只稳定浏览器页面的操作接口，不新增网络路由或外部 API Key。Agent 点击与人相同的页面控件，仍由页面的 `api()` 附带 `client_id`，由现有服务端编辑租约拦截不属于本页的写入；启动仍先同步任务、执行预检，再提交到 ComfyUI。不能把 `data-agent-action` 当作绕过租约、确认或预检的另一条执行通道。

关键控件保留原 `id` 与人可见文案，新增可访问名称和稳定的 `data-agent-action`：提示词/图片导入、链接图片提取、绘画参考建任务、工作流/用途/模型选择、实际预检（含页脚快速入口）、启动批次、全部通过。只读页的「接管编辑」由 `#takeOverLeaseButton` 标识。页面 `<body data-agent-lease>` 初值为 `unknown`；收到租约快照后变为 `free`、`mine` 或 `read-only`。

`#agentActionStatus` 是持久状态区域，带 `role=status`、`aria-live=polite`、`data-agent-action` 和 `data-agent-state`；第一段覆盖接管编辑、实际预检、启动批次的 `running`、`succeeded`、`blocked`、`failed`。状态由原人工事件处理函数发布；旧 toast 仍用于短暂人工提示。预检失败不得进入 `/api/start`，只读页不得请求预检或提交。

## 验收与未完成

- DOM 契约测试检查 ID、动作名、可访问名称、页面事件路径和状态区域；浏览器行为测试检查只读阻断、预检成功、预检后启动以及失败时不提交。
- 现有服务端租约测试继续验证写入的 `client_id` 校验；本段不修改服务端租约语义。
- 这只是 #14 的第一段，不是完整的可审计 Agent 操作层。导入、任务级确认和其它写操作尚未统一发布稳定结果；持久审计日志、保留策略、跨实例冲突与完整键盘验收仍待后续独立阶段。`#agentActionStatus` 只保存页面当前结果，不能冒充审计记录。正式构建、桌面部署和 Edge 验收未完成前任务保持 🟡。
