# 丽芙·霁梦动态壁纸 v8
# 多图片分层动画方案与部件相对位置

> 本方案替代 v7 Live2D 路线。
>
> 目标：不再依赖 Cubism 导出包和分层 PSD，而是通过多张透明 PNG、局部关键帧图片和明确的旋转中心，制作可见的人物动作、眨眼与背景运动。
>
> 基准画布：`1920 × 1080`
>
> 唯一外观基准：`assets/master-keyframe.png`

---

# 1. 核心实现原则

采用“多图片精灵分层 + 关键帧切换 + 关节级旋转”方案。

人物不再通过整张母图拉伸或椭圆裁剪运动，而是拆成：

```text
后发
后裙
身体底层
左臂
胸肩
前裙
飘带
肩部遮盖
上臂
前臂
手和手指
头部
前发
眼睛帧
```

每个部件使用透明 PNG。代码只对独立部件进行平移、旋转和帧切换。

推荐所有透明 PNG 保持 `1920×1080` 原画布尺寸，并保留在原位置。这样每张图都以 `(0, 0)` 绘制，不需要重新计算位置，旋转时只使用本文件给出的 Canvas 旋转中心。

为节省显存，也允许裁切到部件边界；裁切模式必须使用本文件给出的 `bbox` 与 `pivotLocal`。

---

# 2. 两种素材输出模式

## 2.1 推荐：同画布透明层

每张 PNG：

```text
width: 1920
height: 1080
position: x=0, y=0
background: transparent
```

优点：

- 不会放错位置；
- 不会因裁切边缘产生位移；
- 所有关键帧天然对齐；
- Codex 只需统一 `drawImage(layer, 0, 0, width, height)`。

## 2.2 可选：裁切精灵

每张图裁切到 `bbox`：

```text
x = bbox.x
 y = bbox.y
pivotLocalX = pivotCanvasX - bbox.x
pivotLocalY = pivotCanvasY - bbox.y
```

绘制时：

```js
ctx.translate(x + pivotLocalX, y + pivotLocalY);
ctx.rotate(rotationRadians);
ctx.translate(-pivotLocalX, -pivotLocalY);
ctx.drawImage(sprite, 0, 0);
```

---

# 3. 人物部件位置表

所有坐标基于 `1920×1080` 母图。

| 部件 ID | bbox x | bbox y | 宽 | 高 | Canvas 旋转中心 X | Canvas 旋转中心 Y | 局部旋转中心 X | 局部旋转中心 Y | 建议层级 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `hair_back_far` | 0 | 80 | 760 | 650 | 650 | 295 | 650 | 215 | 10 |
| `hair_back_lower` | 245 | 430 | 780 | 580 | 690 | 530 | 445 | 100 | 15 |
| `skirt_back` | 250 | 540 | 650 | 540 | 690 | 620 | 440 | 80 | 20 |
| `lower_hair` | 530 | 520 | 510 | 470 | 740 | 600 | 210 | 80 | 25 |
| `left_arm_down` | 420 | 430 | 280 | 340 | 635 | 455 | 215 | 25 | 30 |
| `torso_chest` | 590 | 340 | 330 | 330 | 748 | 610 | 158 | 270 | 40 |
| `skirt_front` | 540 | 525 | 540 | 500 | 745 | 610 | 205 | 85 | 50 |
| `ribbon_blue` | 420 | 630 | 330 | 410 | 650 | 665 | 230 | 35 | 55 |
| `shoulder_cover` | 700 | 330 | 250 | 200 | 815 | 420 | 115 | 90 | 60 |
| `upper_arm_reach` | 790 | 330 | 285 | 150 | 815 | 410 | 25 | 80 | 70 |
| `forearm_reach` | 995 | 280 | 235 | 155 | 1015 | 380 | 20 | 100 | 80 |
| `hand_fingers` | 1125 | 275 | 140 | 125 | 1150 | 345 | 25 | 70 | 90 |
| `head_full` | 590 | 90 | 320 | 350 | 742 | 405 | 152 | 315 | 100 |
| `hair_front` | 620 | 165 | 290 | 260 | 748 | 305 | 128 | 140 | 110 |
| `face_eye_patch` | 710 | 260 | 135 | 90 | 775 | 315 | 65 | 55 | 120 |

归一化坐标已写入：

```text
assets/sprite-v8/sprite-layout-v8.json
```

这些坐标是第一版制作坐标。透明素材切出后，允许根据 Alpha 有效像素外扩 `8–24 px`，但 Canvas 旋转中心不得随意改变。

---

# 4. 必须制作的透明图片

建议目录：

```text
assets/sprite-v8/
├─ background/
│  ├─ background_clean.png
│  ├─ starfield_far.png
│  ├─ starfield_mid.png
│  ├─ starfield_near.png
│  └─ nebula_overlay.png
├─ character/
│  ├─ body_static.png
│  ├─ hair_back_far.png
│  ├─ hair_back_lower.png
│  ├─ lower_hair.png
│  ├─ skirt_back.png
│  ├─ left_arm_down.png
│  ├─ torso_chest.png
│  ├─ skirt_front.png
│  ├─ ribbon_blue.png
│  ├─ shoulder_cover.png
│  ├─ upper_arm_reach.png
│  ├─ forearm_reach.png
│  ├─ hand_fingers.png
│  ├─ head_full.png
│  └─ hair_front.png
├─ eyes/
│  ├─ eye_open.png
│  ├─ eye_half.png
│  └─ eye_closed.png
├─ arm-frames/
│  ├─ arm_00_rest.png
│  ├─ arm_01_lift.png
│  ├─ arm_02_extend.png
│  ├─ arm_03_hold.png
│  ├─ arm_04_wrist.png
│  └─ arm_05_return.png
├─ head-frames/
│  ├─ head_00_front.png
│  ├─ head_01_turn_25.png
│  ├─ head_02_turn_50.png
│  ├─ head_03_turn_75.png
│  └─ head_04_turn_100.png
├─ fragments/
│  ├─ fragment_left_top_01.png
│  ├─ fragment_left_top_02.png
│  ├─ fragment_center_top.png
│  ├─ fragment_center_mid.png
│  ├─ fragment_right_mid.png
│  ├─ fragment_left_bottom.png
│  └─ fragment_config.json
└─ sprite-layout-v8.json
```

---

# 5. 关键帧图片制作规则

## 5.1 头部关键帧

不使用程序缩放脸部制造转头。

生成或绘制 5 张保持角色一致的头部透明图片：

```text
head_00_front      初始方向
head_01_turn_25    轻微转向晶体
head_02_turn_50    转向一半
head_03_turn_75    接近目标
head_04_turn_100   注视晶体
```

所有图片必须：

- 使用同一 1920×1080 透明画布；
- 颈部连接点保持在 `(742, 405)`；
- 五官比例、发饰、眼睛颜色和脸型不变；
- 只改变头部朝向、前发透视和少量面部透视；
- 不改变躯干和背景。

播放时采用帧选择或很短的局部交叉过渡。禁止把完整画面进行透明叠加。

## 5.2 手臂关键帧

为避免“整块手臂平移”，推荐同时保留独立上臂、前臂和手部，并增加 6 张完整手臂局部关键帧作为校正：

```text
arm_00_rest     原始伸手姿态
arm_01_lift     肩部轻抬，上臂先动
arm_02_extend   肘部展开，前臂前送
arm_03_hold     指向晶体
arm_04_wrist    手腕转动，手指舒展
arm_05_return   回程过渡
```

关节基准：

```text
肩：815, 410
肘：1015, 380
腕：1150, 345
```

程序运动范围：

```text
上臂：-2° 到 +5°
前臂：-4° 到 +7°
手腕：-5° 到 +6°
手指：在 arm_03 / arm_04 图片中切换
```

动作必须能看出肩、肘、腕的先后变化。仅平移整个手臂图片不合格。

## 5.3 眼睛帧

只制作角色眼睛局部透明图片：

```text
eye_open.png
eye_half.png
eye_closed.png
```

使用 `face_eye_patch` 对齐：

```text
bbox: x=710, y=260, width=135, height=90
pivot: 775, 315
```

眼睛帧不得包含：

- 鼻子；
- 嘴；
- 大面积脸颊；
- 前发；
- 头饰。

眨眼使用不透明帧替换，而不是把闭眼图半透明叠在睁眼上。

---

# 6. 12 秒时间轴

## 6.1 人物

```text
0.00–1.80s
头部 head_00；手臂 arm_00；轻微发丝和裙摆运动

1.80–3.20s
头部 head_00 → head_01 → head_02
肩部和上臂开始运动
2.80s 第一次眨眼

3.20–5.20s
头部 head_02 → head_03 → head_04
手臂 arm_01 → arm_02 → arm_03

5.20–7.60s
头部保持 head_04
手臂 arm_03 → arm_04
手腕转动，手指舒展

7.60–8.40s
短暂保持
8.10s 第二次眨眼

8.40–12.00s
所有头部帧反向播放
手臂 arm_04 → arm_03 → arm_02 → arm_01 → arm_00
发丝、裙摆和飘带延迟回位
```

## 6.2 眨眼

每次眨眼约 `0.18–0.22s`：

```text
open → half → closed → half → open
```

单帧显示建议：

```text
open:   2 帧
half:   2 帧
closed: 2 帧
half:   2 帧
open:   回到常态
```

60 FPS 时闭眼必须至少保留 2 帧，确保肉眼可见。

---

# 7. 背景相对位置与移动范围

背景透明层统一使用 1920×1080 画布，绘制位置均为 `(0,0)`。

主要视觉区域：

| 背景部件 | bbox | 旋转中心 | 建议运动 |
|---|---|---|---|
| 主晶体 | `x=1430,y=15,w=410,h=835` | `1630,425` | 呼吸亮度，不整体大幅移动 |
| 顶部记忆晶片 | `x=1000,y=70,w=290,h=245` | `1145,192` | `±6px, ±2°` |
| 中部晶片 | `x=1050,y=495,w=230,h=205` | `1165,598` | `±10px, ±3°` |
| 右中晶片 | `x=1190,y=510,w=230,h=205` | `1305,612` | `±12px, ±4°` |
| 左下晶片 | `x=0,y=650,w=250,h=330` | `125,815` | `±16px, ±4°` |
| 左上碎片群 | `x=80,y=15,w=570,h=270` | `365,150` | 分成至少 4 张独立 PNG |

星河层：

```text
far:  2–4 px 视差，1.5–2.5° 往返
mid:  5–9 px 视差，3–4.5° 往返
near: 9–15 px 视差，4.5–6° 往返
```

碎片：

```text
far:  4–8 px
mid:  10–18 px
near: 18–30 px
```

所有运动必须使用 12 秒周期函数，不能使用无限累加速度。

---

# 8. 层级顺序

推荐从后到前：

```text
0   background_clean
5   starfield_far
8   starfield_mid
10  hair_back_far
12  fragments_far
15  hair_back_lower
18  starfield_near
20  skirt_back
25  lower_hair
30  left_arm_down
40  torso_chest
50  skirt_front
55  ribbon_blue
60  shoulder_cover
70  upper_arm_reach
80  forearm_reach
90  hand_fingers
100 head_full / head-frame
110 hair_front
120 eye-frame
130 fragments_near
140 crystal glow and interaction effects
150 title
```

---

# 9. 代码实现方式

每个部件只加载一次：

```js
const sprites = await loadSpriteManifest('assets/sprite-v8/sprite-layout-v8.json');
```

绘制独立部件：

```js
function drawSpritePart(ctx, part, transform) {
  const pivotX = part.pivotCanvasPx.x;
  const pivotY = part.pivotCanvasPx.y;

  ctx.save();
  ctx.translate(pivotX + transform.translateXPx, pivotY + transform.translateYPx);
  ctx.rotate(transform.rotationDeg * Math.PI / 180);
  ctx.translate(-pivotX, -pivotY);
  ctx.drawImage(part.image, part.drawX, part.drawY);
  ctx.restore();
}
```

关键帧选择必须是互斥绘制：

```js
const headFrame = selectFrame(headFrames, timeline.headProgress);
ctx.drawImage(headFrame, 0, 0);
```

不允许同时绘制两张完整头部帧并长期半透明混合。若做过渡，只能在 `2–4` 帧内局部交叉，且素材 Alpha 边界完全一致。

---

# 10. 旧代码停用

必须停用：

```text
drawTransformedImageRegion() 对完整母图的裁剪重绘
drawBlinkLocal() 使用 motion-blink.png 的实现
drawImageLayer() 中整幅图的 breath 缩放
drawStarfieldRotation() 中再次绘制 art
drawFloatingFragments() 中再次绘制 art
```

`master-keyframe.png` 只允许：

- 作为分层制作参考；
- 在全部精灵资源加载失败时显示单张静态 fallback。

不得在精灵人物后方继续显示带人物的母图。

---

# 11. 资产验收

每个透明部件必须：

- 背景完全透明；
- 无矩形底色；
- 无白边、黑边；
- 与原图位置对齐；
- 关节后方有补绘或遮盖层；
- 单独显示时不包含其他完整人物部位；
- 关闭所有运动时，与母图外观基本一致。

头部帧、手臂帧和眼睛帧必须制作对齐叠图，检查相同锚点是否保持稳定。

---

# 12. 最终验收

完成必须同时满足：

- 人物不再整体拉伸；
- 头部方向变化可见；
- 手臂能看到肩、肘、腕的联动；
- 手指至少有一次舒展变化；
- 12 秒内有两次明确闭眼；
- 眨眼无双睫毛和脸部残影；
- 背景观看 2 秒内可感知运动；
- 星河、碎片不是母图重复裁剪；
- 0 秒和 12 秒完全闭环；
- 鼠标、点击、音频、标题、静谧模式保留。

---

# 13. Codex 启动指令

```text
读取 HANDOFF.md、AGENTS.md、MULTI_IMAGE_SPRITE_ANIMATION_V8.md 和 PROJECT_STATUS_V8.yaml。
停止 Live2D/Cubism 资产等待路线。
采用多图片透明精灵和局部关键帧路线。
先建立 sprite-layout-v8.json 加载器和 fail-closed 资产检查。
没有真实透明分层图片时，不得重新使用完整母图裁剪来冒充精灵层。
人物、眨眼和背景必须分别验收。
```
