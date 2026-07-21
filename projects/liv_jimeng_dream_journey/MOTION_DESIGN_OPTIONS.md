# 丽芙·霁梦动态壁纸 v6.1 动作设计选项

## 当前问题诊断

根据 QA 包分析，当前版本的问题不是参数不足，而是实现方式不匹配目标。

已确认：

- 人物动作主要依赖整图区域重绘，不是真正的人物骨骼/分层动画。
- `motion-blink.png` 与原眨眼资源相同，无法提供真正闭眼变化。
- 当前眨眼和动作叠加容易产生整脸重影。
- 背景运动使用母图裁剪叠加，视觉上像透明重影，而不是独立空间运动。

现版本必须先选择运动实现路线，再由 Agent 执行。

---

# 人物运动方案（选择一项）

## A. Canvas 分层切片方案（推荐，低风险）

方法：

使用 Photoshop / Photopea / Krita 将人物拆分为透明 PNG：

- 头部
- 眼睛
- 前发
- 后发
- 手臂
- 手指
- 裙摆
- 飘带

实现：

Canvas 2D + 旋转中心 + mask 融合。

动作：

- 转头 3°
- 眼睛局部切换眨眼
- 头发延迟摆动
- 手指轻微展开

工具：

- Photoshop Generative Fill / Remove Background
- Photopea
- Krita
- Adobe After Effects 导出透明层

Skill：

- image segmentation
- alpha mask extraction
- canvas transform

优点：稳定，适合 Wallpaper Engine Web。
缺点：需要人工切图。

---

## B. Live2D Cubism 方案（质量最高）

方法：

将人物制作成 Live2D 模型。

需要：

- PSD 分层文件
- Cubism Editor

制作：

- Face XY
- Eye Blink
- Hair Physics
- Cloth Physics
- Arm Physics

导出：

Cubism Web SDK。

优点：接近官方动态立绘。
缺点：需要大量制作时间。

---

## C. Spine 2D 骨骼方案

方法：

建立骨骼动画。

适合：

- 手臂
- 裙摆
- 飘带
- 头发

工具：

Spine 2D。

优点：动作自然。
缺点：人物脸部仍需切层。

---

## D. AI 视频运动方案（不推荐最终版）

工具：

- Runway
- Stable Video Diffusion
- AnimateDiff

作用：生成参考动作。

禁止直接作为 Wallpaper Engine 视频。

原因：

- 人脸漂移
- 五官变化
- 循环困难

只能用于动作参考。

---

# 背景运动方案

## 1. 星河独立层方案（推荐）

拆分：

- 星空背景层
- 星点层
- 星云层

实现：

Canvas transform rotate + parallax。

动作：

12 秒旋转 4-8°。

工具：

Photoshop 分离天空
Canvas 2D。

---

## 2. 碎片粒子系统

方法：

重新生成晶体碎片透明 PNG。

每块拥有：

- position
- depth
- velocity
- rotation
- phase

工具：

PixiJS ParticleContainer（若允许替换渲染）
或 Canvas Particle System。

---

## 3. 晶体内部流动

方法：

制作：

- crystal mask
- glow map
- flow texture

实现：

shader 或 Canvas gradient。

工具：

After Effects Flow Map
或 Canvas clipping。

---

# 眨眼实现选择

## E1. 局部眼睛 PNG（推荐）

资源：

- eyes-open.png
- eyes-half.png
- eyes-close.png

只替换眼睛区域。

不会产生脸部重影。

## E2. AI 修复眼睛

使用 Photoshop Neural Filter 或局部 inpainting。

风险：角色一致性。

## E3. 放弃真实眨眼，仅使用睫毛闪动

最低风险。

---

# 推荐组合

## 方案 R1（推荐）

人物：A Canvas 分层

背景：星河独立层 + 碎片粒子

眨眼：E1 局部眼睛 PNG

工具：

Photoshop/Krita + Canvas 2D

目标：

达到官方动态壁纸级别，同时保持 Web Wallpaper Engine。

---

## 方案 R2（最高质量）

人物：B Live2D

背景：AE 分层 + Canvas 合成

眨眼：Live2D EyeBlink

---

# 执行规则

用户选择前：

Agent 不修改代码。

选择后：

1. 建立资源清单。
2. 创建子代理负责资源处理。
3. 主代理只负责 Canvas 集成。
4. 每个阶段提交独立 commit。
5. 未通过视觉检查不得进入下一阶段。
