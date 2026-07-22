# TASKS_V8 — 丽芙·霁梦多图片帧动画执行任务

## Task 1 — Asset validation

目标：防止 Codex 在缺少真实素材时伪造完成。

创建：

- `scripts/verify_frame_assets.js`

功能：

- 检查 manifest 中所有文件
- 检查 PNG 存在性
- 检查透明通道
- 检查尺寸一致性
- 检查命名规则

缺失时返回：

`BLOCKED_BY_ASSET_PRODUCTION`

---

## Task 2 — Frame runtime loader

创建：

- `runtime/frame-loader.js`

要求：

- 根据 12 秒循环时间计算当前帧
- 支持 288 runtime frames
- 缓存图片资源
- 支持缺失资源安全退出

禁止：

- 整图缩放模拟动作
- crop 动画模拟人物运动

---

## Task 3 — Character layer scheduler

管理：

- head
- eyes
- arm
- hair
- cloth

要求：

角色动作必须来自不同透明层帧。

重点检查：

- 头部方向变化
- 手臂结构变化
- 手指稳定
- 眨眼无重影

---

## Task 4 — Background scheduler

管理：

- starfield_far
- starfield_mid
- starfield_near
- crystal_glow
- fragments
- memories

要求：

背景必须独立运动。

禁止：

所有背景层同步平移。

---

## Task 5 — Interaction preservation

保留：

- mouse parallax
- click ripple
- audio response
- title display
- quality switch
- quiet mode
- ultrawide support

---

## Task 6 — QA

输出：

`QA_RESULT_V8.md`

必须记录：

- 是否存在光噪
- 是否存在锁链纹路
- 眨眼是否清晰
- 手臂是否真实变化
- 背景是否明显运动
- 是否完成 12 秒闭环

未通过时禁止标记完成。
