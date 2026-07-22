# TASKS_V8 — 丽芙·霁梦多图片帧动画执行任务

## 状态说明

- `DONE_CODE`：代码和工程合同已经写入仓库，但仍需在本地执行验证。
- `BLOCKED_ASSET`：必须等待真实透明 PNG 素材，代码代理不得伪造完成。
- `PENDING_QA`：必须通过浏览器与 Wallpaper Engine 实机检查。
- `DONE_VERIFIED`：只有在存在真实执行证据后才能使用。

当前总体状态：

`CODEX_HANDOFF_READY / ENGINE_INTEGRATED / BLOCKED_BY_ASSET_PRODUCTION`

---

## Task 1 — Asset contract and validation

**状态：`DONE_CODE`**

已实现：

- `assets/frame-v8/manifests/asset-checklist-v8.json`
- `assets/frame-v8/manifests/asset-spec-v8.json`
- `runtime/frame-loader.js`
- `scripts/verify_frame_assets.js`

能力：

- 精确检查 79 项资产合同；
- 区分 73 项必需资产与 6 项可选回忆晶片；
- 检查 PNG 签名、尺寸、命名和透明通道；
- 拒绝全透明占位图和重复帧冒充动画；
- 必需素材缺失时返回 `BLOCKED_BY_ASSET_PRODUCTION`。

Codex 下一步：运行脚本并修复真实执行发现的问题，不要重新创建另一套加载器或验证器。

---

## Task 2 — Frame runtime loader and timeline

**状态：`DONE_CODE`**

已实现：

- `runtime/timeline.js`
- `runtime/frame-loader.js`
- `runtime/v8-runtime.js`
- `assets/frame-v8/manifests/frame-map-v8.json`
- `assets/frame-v8/manifests/frame-timeline-v8.json`

合同：

- 12 秒循环；
- 24 FPS；
- 288 个运行帧；
- 状态由 `(elapsedSeconds % 12)` 推导；
- 解码后的图片必须缓存；
- 必需资源失败时只能进入静态回退。

禁止：

- 整图缩放模拟人物动作；
- crop 动画模拟头部或手臂运动；
- 使用同一帧复制成完整序列。

---

## Task 3 — Character layer scheduler

**代码状态：`DONE_CODE`**  
**素材状态：`BLOCKED_ASSET`**

管理：

- `head_00`—`head_08`
- 五个眼睛状态帧
- `arm_00`—`arm_08`
- 前后头发序列
- 裙摆与飘带序列

真实素材验收必须确认：

- 头部方向发生结构变化；
- 手臂至少改变肩、肘、腕和手指三个结构区域；
- 两次眨眼分别位于约 2.8 秒和 8.1 秒；
- 眨眼没有睫毛重影、虹膜残留或脸部矩形补丁；
- 手指自然，人物身份不漂移。

---

## Task 4 — Background scheduler

**代码状态：`DONE_CODE`**  
**素材状态：`BLOCKED_ASSET`**

已实现：

- `runtime/background-engine.js`
- 远、中、近星场独立阶段；
- 晶体基础层与发光阶段；
- 四组碎片区域；
- 六个可选回忆晶片；
- 受控出现和消失；
- 无随机噪点生成。

真实素材验收必须确认：

- 两秒内可以感知背景运动；
- 远、中、近层不锁步；
- 碎片和回忆晶片是独立资产；
- 没有光噪、脏点、锁链或束缚纹样。

---

## Task 5 — Wallpaper Engine interaction preservation

**代码状态：`DONE_CODE`**  
**回归状态：`PENDING_QA`**

已接入：

- 鼠标视差；
- 点击涟漪；
- 音频响应；
- 标题显示；
- 画质切换；
- 静谧模式；
- 画面缩放和水平位置；
- 超宽屏 cover 渲染。

必须在 Wallpaper Engine 中逐项回归，不能仅凭代码存在标记通过。

---

## Task 6 — Diagnostic pipeline

**状态：`DONE_CODE`**

已实现：

- `scripts/generate_diagnostic_assets_v8.js`
- `runtime/config-v8-diagnostic.js`
- `scripts/run_v8_diagnostic_qa.js`
- `index.html?v8mode=diagnostic`
- 诊断状态面板

诊断素材只验证路径、图层、时间轴和渲染器，不代表最终人物美术完成，也不得复制到生产资产目录。

首轮执行命令见：

- `CODEX_FIRST_RUN_V8.md`

---

## Task 7 — Production asset import

**状态：`BLOCKED_ASSET`**

需要导入：

- 73 项真实必需 PNG；
- 最多 6 项可选回忆晶片 PNG；
- 所有文件均为 1920×1080；
- 除 `background_clean` 外均需透明通道；
- 文件路径和 ID 必须完全匹配 `asset-checklist-v8.json`。

禁止用诊断几何图、空 PNG、整图裁切或重复帧解除阻塞。

---

## Task 8 — Final QA and release

**状态：`PENDING_QA`**

必须输出：

- `QA_RESULT_V8.md`
- `assets/frame-v8/production/manual-review-v8.json`
- 浏览器控制台检查记录；
- Wallpaper Engine 实机回归结果；
- 0 秒与 12 秒闭环证据。

最终拒绝条件：

- 人物动作来自整图移动；
- 手臂没有关节结构变化；
- 眨眼产生重影；
- 背景只是单张纹理同步平移；
- 存在光噪或锁链纹样；
- 使用诊断素材冒充最终美术；
- 没有真实执行证据却标记 `PRODUCTION_READY`。
