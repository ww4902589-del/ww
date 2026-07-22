# HANDOFF_FINAL_V8 — 丽芙·霁梦动态壁纸最终 Codex 交接说明

## 交接状态

当前状态：

`CODEX_HANDOFF_READY`

注意：这代表工程框架已经准备完成，不代表最终壁纸已经生产完成。

生产完成必须等待：

- 真实透明 PNG 分层资产；
- 自动验证通过；
- 浏览器运行通过；
- Wallpaper Engine 回归通过；
- 人工视觉验收通过。

---

## 已确定技术路线

使用：

- Wallpaper Engine Web wallpaper；
- Canvas 2D 分层渲染；
- 多张透明 PNG 帧序列；
- 12 秒循环；
- 24 FPS；
- 288 runtime frames。

不使用：

- 整图拉伸动画；
- crop 模拟动作；
- 无 PSD/Cubism 条件下强行 Live2D；
- 单张背景平移替代动态环境。

---

## Codex 第一原则

先运行诊断链，再进入真实素材。

顺序：

1. 阅读 `PROJECT_STATUS_V8.yaml`；
2. 阅读 `CODEX_FIRST_RUN_V8.md`；
3. 运行诊断素材生成；
4. 运行诊断 QA；
5. 修复工程问题；
6. 导入真实 PNG。

---

## 已完成工程模块

- V8 manifest 系统；
- 79 项资产合同；
- Frame Loader；
- Timeline；
- Background Engine；
- Layer Manager；
- Canvas Renderer；
- Production / Diagnostic 双模式；
- PNG 自动检查；
- QA 报告基础。

---

## 当前唯一主要阻塞

真实美术资产不存在。

需要制作：

- 头部方向帧；
- 眼睛闭合序列；
- 手臂关节序列；
- 头发层；
- 衣物层；
- 星河层；
- 碎片层；
- 晶体层。

---

## 最终验收标准

人物：

- 转头必须来自头部结构变化；
- 手臂必须存在肩、肘、腕变化；
- 眨眼必须无重影；
- 手指必须自然。

背景：

- 星河分层运动；
- 碎片独立漂浮；
- 晶体产生阶段变化；
- 背景可以隐现。

画面：

- 无光噪；
- 无锁链纹路；
- 无透明边缘污染；
- 无 AI 身份漂移。

---

## 禁止完成声明

在真实素材和 QA 证据缺失前，不允许提交：

`PRODUCTION_READY`

正确状态只能是：

`BLOCKED_BY_ASSET_PRODUCTION`
