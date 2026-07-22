# 丽芙·霁梦动态壁纸 v7
## 未完成修改项的方法、工具与交接步骤

> 本文件只描述目前尚未真正完成的工作，以及完成这些工作的正确方法、软件、SDK、脚本和验收方式。
>
> 已确认路线：
>
> - 人物：Live2D Cubism
> - 眨眼：E1 局部眼睛素材/眼睑参数
> - 背景：独立星河层 + 碎片粒子系统
>
> 当前状态不是“成品完成”，而是“实施方法已确定”。后续 Agent 不得把文档、占位代码或自动检查通过当成视觉修改完成。

---

# 1. 尚未完成的实际工作

以下项目目前均应标记为 `TODO`：

- [ ] 从原始母图制作可导入 Live2D 的分层 PSD
- [ ] 补绘被头发、脸、颈部、手臂和裙摆遮挡的区域
- [ ] 制作独立的左眼、右眼、上下眼睑、眼白、瞳孔和睫毛素材
- [ ] 在 Cubism Editor 中完成 ArtMesh、Deformer、参数和物理绑定
- [ ] 制作 12 秒转头待机动作
- [ ] 导出 `.moc3`、`.model3.json`、纹理、`.physics3.json` 和 `.motion3.json`
- [ ] 接入 Cubism SDK for Web
- [ ] 在 Wallpaper Engine 中实际加载 Live2D 模型
- [ ] 从母图分离干净背景和独立星河层
- [ ] 切出独立透明碎片 PNG
- [ ] 编写真正的碎片粒子系统
- [ ] 删除或停用会导致重影的整帧动作图叠加
- [ ] 在 Wallpaper Engine 中逐帧验证眨眼、转头边缘和循环
- [ ] 输出最终可运行壁纸包

---

# 2. 工具清单

## 2.1 人物素材分层

首选工具：

1. **Adobe Photoshop**
2. **CLIP STUDIO PAINT**

Live2D 官方明确确认这两种工具生成的 PSD 可用于 Cubism Editor。最终导入 PSD 必须满足：

- PSD 格式
- RGB
- 8 bit/channel
- sRGB
- 每个可动部件一个合并后的图层
- 不保留图层蒙版
- 图层名不得重复
- 线稿、填色和剪贴效果在导入副本中合并

辅助工具：

- Photoshop Content-Aware Fill / Remove Tool：补绘被遮挡区域
- Photoshop Clone Stamp：修复发丝、颈部和衣物边缘
- Live2D 官方 Photoshop 预处理脚本：合并组、蒙版和剪贴层
- ImageMagick：检查透明通道、尺寸和空白边界

不建议把 Krita 或 Photopea 作为最终 PSD 交付工具；可以用于草稿和局部修图，但导入前必须在 Photoshop 或 CLIP STUDIO PAINT 中复核。

官方参考：

- https://docs.live2d.com/en/cubism-editor-manual/psd-import/
- https://docs.live2d.com/en/cubism-editor-manual/precautions-for-psd-data/
- https://docs.live2d.com/en/cubism-editor-manual/script-download/

---

## 2.2 Live2D 建模

必须使用：

- **Live2D Cubism Editor 当前稳定版**
- 不使用 alpha 版制作最终交付模型

建模功能：

- ArtMesh
- Warp Deformer
- Rotation Deformer
- Parameters
- Physics
- Animation Workspace
- Export for Runtime

官方参考：

- https://docs.live2d.com/en/cubism-editor-manual/top/
- https://docs.live2d.com/en/cubism-editor-manual/physics-operation/
- https://docs.live2d.com/en/cubism-editor-manual/eye-blink-settings/

---

## 2.3 Web 运行时接入

必须使用：

- **Cubism SDK for Web**
- **Cubism Web Framework**
- **Cubism Core for Web**
- 官方 `CubismWebSamples` 作为实现参考

注意：

- Cubism Core 不随 GitHub Framework 仓库公开提供，需要从 Live2D 官方 SDK 包中取得。
- 在把 Cubism Core 或模型文件提交到公开仓库、发布或打包前，必须由执行者检查相应许可与再分发条件。
- 不从不明 CDN 获取 Core、Framework 或模型加载器。

官方参考：

- https://docs.live2d.com/en/cubism-sdk-manual/cubism-sdk-for-web/
- https://docs.live2d.com/en/cubism-sdk-manual/model-web/
- https://docs.live2d.com/en/cubism-sdk-manual/use-framework-web/
- https://github.com/Live2D/CubismWebSamples
- https://github.com/Live2D/CubismWebFramework

---

## 2.4 背景和粒子

使用：

- Photoshop / CLIP STUDIO PAINT：分离背景层和碎片
- Canvas 2D：星河、碎片和星尘绘制
- ImageMagick：透明边缘检查
- ffmpeg：录制与比较 12 秒循环

默认不引入 PixiJS。只有 Canvas 2D 性能测试无法通过时，才评估 PixiJS，并单独提交性能证据。

---

## 2.5 Wallpaper Engine 验证

使用：

- Wallpaper Engine Web Wallpaper
- Wallpaper Engine 编辑器预览
- Wallpaper Engine 桌面实机运行

Web 壁纸所需 HTML、脚本、模型、纹理和图片必须放在独立项目目录及其子目录中，不依赖外部服务器或在线 CDN。

官方参考：

- https://docs.wallpaperengine.io/en/web/overview.html
- https://docs.wallpaperengine.io/en/web/first/gettingstarted.html

---

# 3. 人物 PSD 分层方法

## 3.1 建立两个 PSD

必须保留：

```text
liv_material_separation_t001.psd
liv_import_t001.psd
```

`material_separation` 保存未合并的工作层。

`import` 是供 Cubism 导入的合并层副本，不能覆盖工作 PSD。

---

## 3.2 最低分层结构

```text
Character
├─ Head
│  ├─ Face_Base
│  ├─ Ear_L
│  ├─ Ear_R
│  ├─ Neck_Backfill
│  └─ Head_Ornament
├─ Eyes
│  ├─ EyeWhite_L
│  ├─ Iris_L
│  ├─ Highlight_L
│  ├─ Eyelash_Upper_L
│  ├─ Eyelid_Lower_L
│  ├─ EyeWhite_R
│  ├─ Iris_R
│  ├─ Highlight_R
│  ├─ Eyelash_Upper_R
│  └─ Eyelid_Lower_R
├─ Eyebrows
│  ├─ Eyebrow_L
│  └─ Eyebrow_R
├─ Hair
│  ├─ Hair_Front_Center
│  ├─ Hair_Front_L
│  ├─ Hair_Front_R
│  ├─ Hair_Side_L
│  ├─ Hair_Side_R
│  ├─ Hair_Back_01
│  ├─ Hair_Back_02
│  └─ Hair_Back_03
├─ Body
│  ├─ Neck_Front
│  ├─ Torso
│  ├─ Shoulder_L
│  ├─ Shoulder_R
│  ├─ UpperArm
│  ├─ Forearm
│  ├─ Hand
│  ├─ Fingers_Front
│  └─ Fingers_Back
├─ Cloth
│  ├─ Skirt_Front
│  ├─ Skirt_Back
│  ├─ Ribbon_01
│  ├─ Ribbon_02
│  └─ Cloth_Backfill
└─ Effects
   ├─ Character_Glow
   └─ Hand_Glow
```

## 3.3 补绘规则

每个会移动的部件后方必须有完整底图：

- 头部转动后不能露出透明颈部
- 前发移动后必须存在完整额头和脸部
- 手臂移动后必须存在完整躯干与袖口
- 裙摆移动后必须存在完整后层衣物

补绘时不得改变：

- 五官比例
- 眼睛颜色
- 发饰结构
- 服装设计
- 身材比例
- 原图主色

## 3.4 资源验收

- 单独隐藏任意一个前景部件时，后方没有透明洞
- 所有图层名称唯一
- 每个图层裁切到有效像素附近，但保留变形余量
- 透明边缘无白边、黑边和半透明脏边
- PSD 可被 Cubism Editor 正常导入

---

# 4. E1 眨眼实现方法

## 4.1 不再使用的方式

立即停用：

- `motion-blink.png` 整图叠加
- 从另一张完整人物图裁一个椭圆区域覆盖脸部
- 通过降低整个眼区高度伪造闭眼
- 在原睁眼上叠加半透明闭眼睫毛

这些方式会产生双睫毛、眼球残影、眼周灰雾和脸部跳动。

## 4.2 正确素材

每只眼必须至少独立为：

- 眼白
- 瞳孔/虹膜
- 高光
- 上睫毛/上眼睑
- 下眼睑

闭眼状态由上眼睑和下眼睑的 ArtMesh 形变完成。若原图无法提供闭眼线条，需要在 Photoshop 中沿原角色眼型补绘一条独立闭眼睫毛线，但不能覆盖鼻子、脸颊、发丝或另一只眼。

## 4.3 Cubism 参数

优先使用标准参数：

```text
ParamEyeLOpen
ParamEyeROpen
```

建议范围：

```text
0.0 = 完全闭眼
0.5 = 半闭
1.0 = 正常睁眼
```

在 Cubism Editor 中把左右眼参数登记为 Eye Blink 目标。

## 4.4 眨眼节奏

12 秒内两次：

```text
2.80 秒附近
8.10 秒附近
```

单次约 0.14–0.20 秒：

```text
1.0 → 0.5 → 0.0 → 0.5 → 1.0
```

左右眼可以有不超过 0.01 秒的微小偏移，但默认同步。

## 4.5 眨眼验收门槛

任何一项失败都不能继续合并背景：

- 无双睫毛
- 无眼球残影
- 无灰色眼眶
- 无脸部矩形边缘
- 鼻子和嘴不移动
- 发丝与头饰不闪烁
- 闭眼后仍保持原角色眼型

---

# 5. Live2D 转头、呼吸和物理实现

## 5.1 参数表

建议使用：

```text
ParamAngleX       -30 .. 30
ParamAngleY       -10 .. 10
ParamAngleZ        -5 .. 5
ParamBodyAngleX    -8 .. 8
ParamBodyAngleY    -4 .. 4
ParamBodyAngleZ    -3 .. 3
ParamBreath         0 .. 1
ParamEyeBallX      -1 .. 1
ParamEyeBallY      -1 .. 1
ParamEyeLOpen       0 .. 1
ParamEyeROpen       0 .. 1
ParamArmReach       0 .. 1
ParamHandRelax      0 .. 1
```

参数 ID 必须在模型配置和 Web 控制代码中保持一致。

## 5.2 转头制作

主动作是向右侧晶体观察，不是左右摇头。

12 秒时间轴：

```text
0.0–2.0   初始待机
2.0–5.2   头部向晶体方向转动
5.2–8.2   保持注视
8.2–12.0  回到初始状态
```

联动顺序：

```text
眼球先移动少量
→ 头部 ParamAngleX/Y
→ 颈部和身体反向补偿
→ 前发轻微跟随
→ 长发通过 Physics 延迟
```

不得只旋转整个头部贴图。

## 5.3 呼吸

- 6 秒周期
- 12 秒内完成两次
- 肩膀与胸部变化轻微
- 不允许人物整体上下漂浮代替呼吸

## 5.4 头发和裙摆物理

在 Cubism Editor 的 Physics 设置中：

输入：

- ParamAngleX
- ParamAngleY
- ParamBodyAngleX

输出：

- HairFrontSwing
- HairSideSwing
- HairBackSwing01..03
- RibbonSwing01..02
- SkirtSwing

建议 Physics 计算目标为 60 FPS，并在 30 FPS 下再次检查稳定性。

物理要求：

- 头先动，发尾后动
- 发尾不穿脸
- 发饰不跟长发大幅摆动
- 裙摆不产生橡皮拉伸
- 第 0 秒和第 12 秒状态一致

---

# 6. Live2D 导出物

在 Cubism Editor 使用 Export for Runtime，最低输出：

```text
assets/live2d/liv/
├─ liv.moc3
├─ liv.model3.json
├─ liv.physics3.json
├─ liv.cdi3.json
├─ textures/
│  ├─ texture_00.png
│  └─ texture_01.png
└─ motions/
   └─ idle_turn.motion3.json
```

可选：

```text
liv.pose3.json
expressions/*.exp3.json
```

`liv.model3.json` 必须正确引用所有相对路径。

导出后先用 Cubism Viewer 检查：

- 模型加载
- 纹理无错位
- 转头边缘
- 眨眼
- Physics
- 12 秒循环

---

# 7. Cubism SDK for Web 接入方法

## 7.1 目录建议

```text
projects/liv_jimeng_dream_journey/
├─ index.html
├─ styles.css
├─ assets/
│  ├─ live2d/liv/...
│  └─ background/...
├─ vendor/
│  └─ live2d/
│     ├─ Core/
│     └─ Framework/
└─ src/
   ├─ live2d/
   │  ├─ live2d-app.ts
   │  ├─ live2d-model.ts
   │  ├─ live2d-motion-controller.ts
   │  └─ live2d-config.ts
   ├─ background/
   │  ├─ starfield.js
   │  └─ fragments.js
   └─ runtime/
      └─ wallpaper-runtime.js
```

如果公开仓库许可不适合提交 Cubism Core，则保留：

```text
vendor/live2d/Core/PLACE_CORE_HERE.md
```

并在本地构建说明中记录所需版本，不能使用伪造空文件冒充已接入。

## 7.2 Canvas 层级

```html
<div id="wallpaper-stage">
  <canvas id="background-canvas"></canvas>
  <canvas id="live2d-canvas"></canvas>
  <canvas id="effects-canvas"></canvas>
  <div id="title"></div>
</div>
```

层级：

```text
background-canvas  z-index 0
live2d-canvas      z-index 10
effects-canvas     z-index 20
title              z-index 30
```

人物不能继续烘焙在背景母图上。干净背景必须不含人物，Live2D 模型只在人物 Canvas 上绘制。

## 7.3 加载流程

```text
初始化 Cubism Framework
→ 创建 WebGL Canvas
→ 加载 liv.model3.json
→ 根据 model3.json 加载 moc3、纹理、physics 和 motion
→ 创建模型实例
→ 播放 idle_turn.motion3.json
→ 每帧更新 motion
→ 更新 blink
→ 更新 physics
→ 绘制模型
```

模型文件必须使用项目内相对路径。不得依赖在线 URL。

## 7.4 运行循环

每帧顺序：

```text
1. 计算 deltaTime
2. 更新 12 秒循环时钟
3. 更新动作参数
4. 更新眨眼参数
5. 更新物理
6. 绘制 Live2D
7. 绘制独立背景和粒子
8. 更新交互与音频响应
```

不得同时在旧 `src/10-character.js` 中再画一次完整人物。

## 7.5 旧代码停用

当 Live2D 成功加载后：

- 删除或禁用完整 `master-keyframe.png` 人物绘制
- 禁用 `motion-reach.png` 人物区域叠加
- 禁用 `motion-blink.png` 绘制
- 保留鼠标、音频、点击涟漪和标题逻辑

在 Live2D 模型加载失败时，可以显示单张静态母图作为 fallback，但 fallback 状态不能叠加任何动作图。

---

# 8. 星河独立层制作方法

## 8.1 输出资源

```text
assets/background/
├─ background_clean.png
├─ starfield_far.png
├─ starfield_mid.png
├─ starfield_near.png
├─ nebula_overlay.png
└─ starfield_config.json
```

`background_clean.png`：

- 不含人物
- 不含要移动的主要星点
- 不含独立碎片
- 人物原位置必须补绘完整背景

星河层：

- PNG 透明背景
- 不包含人物轮廓
- 不包含标题
- 尺寸应比画面略大，旋转时不露出空角

## 8.2 绘制方式

每个星河层独立：

```js
ctx.save();
ctx.translate(pivotX, pivotY);
ctx.rotate(rotationRadians);
ctx.translate(-pivotX, -pivotY);
ctx.drawImage(layer, x, y, width, height);
ctx.restore();
```

12 秒循环必须使用周期函数：

```js
const phase = (elapsedSeconds % 12) / 12;
const rotation = Math.sin(phase * Math.PI * 2) * amplitude;
```

若目标是单向旋转并在 12 秒回位，不得在末尾瞬间重置；应使用往返曲线或制作无接缝可循环纹理。

## 8.3 参数

```text
far:  1–2°，视差最小
mid:  2–4°
near: 3–6°，透明度较低
```

星河运动不得带动人物、晶体和标题。

---

# 9. 碎片粒子系统实现方法

## 9.1 素材

至少切出 8–16 块独立碎片：

```text
assets/background/fragments/
├─ near_01.png
├─ near_02.png
├─ near_03.png
├─ mid_01.png
├─ mid_02.png
├─ mid_03.png
├─ far_01.png
└─ fragments.json
```

不得从母图每帧裁剪同一块背景来模拟碎片。

## 9.2 配置

```json
{
  "id": "near_01",
  "image": "near_01.png",
  "depth": 1.0,
  "xNormalized": 0.72,
  "yNormalized": 0.24,
  "amplitudeXPx": 18,
  "amplitudeYPx": 12,
  "rotationAmplitudeDeg": 4,
  "phaseOffset": 0.13,
  "opacity": 0.82
}
```

## 9.3 循环算法

```js
const localPhase = phase * Math.PI * 2 + fragment.phaseOffset * Math.PI * 2;
const x = baseX + Math.sin(localPhase) * amplitudeX;
const y = baseY + Math.cos(localPhase * 2) * amplitudeY;
const rotation = Math.sin(localPhase + 0.7) * rotationAmplitude;
```

所有位置、旋转和透明度都必须是 12 秒周期函数，不能使用持续累加的 `x += velocity` 作为最终循环逻辑。

## 9.4 深度

```text
far:
- 小
- 慢
- 低对比

mid:
- 中等
- 中速

near:
- 大
- 略快
- 不遮挡脸、眼睛和手
```

## 9.5 性能

高质量：12–16 块碎片

中质量：8–12 块

低质量：4–8 块

图片只加载一次，粒子对象复用，每帧不得创建大量新数组或 Canvas。

---

# 10. Skill / Agent 分工

## 10.1 Asset Separation Agent

需要的能力：

- Photoshop 图层分离
- 遮挡区域补绘
- Alpha 边缘处理
- Live2D PSD 规范

只提交：

- PSD 图层清单
- PNG 预览
- 遮挡补绘说明
- 素材验收截图

不得修改运行时代码。

## 10.2 Cubism Rig Agent

需要的能力：

- Live2D ArtMesh
- Warp/Rotation Deformer
- ParamAngleX/Y/Z
- Eye Blink
- Physics
- Motion 制作

只提交：

- `.cmo3` 工作文件的交付记录
- Runtime 导出文件
- 参数表
- Cubism Viewer 预览

不得修改背景 Canvas。

## 10.3 Web Integration Agent

需要的能力：

- TypeScript/JavaScript
- WebGL
- Cubism SDK for Web
- Wallpaper Engine Web Wallpaper

负责：

- SDK 初始化
- 模型加载
- motion、blink 和 physics 更新
- Canvas 分层
- fallback

不得修改模型纹理和人物设计。

## 10.4 Background Agent

需要的能力：

- Canvas 2D
- 粒子系统
- 图像分层
- 周期动画

负责：

- 星河层
- 碎片粒子
- 质量档位

不得修改 Live2D 参数。

## 10.5 QA Agent

只负责：

- 对比 v6 和 v7
- 逐帧检查眼睛
- 检查人物边缘
- 检查第 0/12 秒闭环
- 检查 Wallpaper Engine FPS、内存和交互

未获得授权不得直接重写动画参数。

---

# 11. 固定执行顺序

## 阶段 0：冻结失败版本

- 备份当前 v6
- 保存重影和眨眼消失的截图/视频
- 禁止继续在 v6 上调透明度

## 阶段 1：人物素材分层

- 完成 PSD
- 输出分层截图
- 通过补绘和 Alpha 验收
- 停止并等待人工确认

## 阶段 2：Cubism 模型

- 建立网格和 Deformer
- 完成转头
- 完成 E1 眨眼
- 完成 Physics
- 在 Cubism Viewer 单独验收
- 未通过不得进入 Web 接入

## 阶段 3：Web 接入

- 引入 SDK
- 加载模型
- 保留静态 fallback
- 确认旧人物叠加已停用

## 阶段 4：背景

- 制作干净背景
- 制作星河层
- 制作碎片 PNG
- 接入 Canvas 粒子系统

## 阶段 5：综合与 QA

- Wallpaper Engine 实机
- 逐帧眨眼
- 12 秒循环
- 高中低质量
- 鼠标、点击、音频、标题回归

每一阶段单独提交，禁止一次提交全部未验证内容。

---

# 12. 验证命令和检查工具

## 图片检查

```bash
magick identify assets/live2d/liv/textures/*.png
magick identify assets/background/*.png
magick identify assets/background/fragments/*.png
```

检查：

- 尺寸
- Alpha
- 色彩空间
- 空图
- 异常超大资源

## JavaScript / TypeScript

```bash
npm ci
npm run typecheck
npm run build
```

或对非 TypeScript 文件：

```bash
node --check src/background/starfield.js
node --check src/background/fragments.js
```

## 12 秒预览

```bash
ffmpeg -framerate 60 -i frames/frame-%05d.png -c:v libx264 -pix_fmt yuv420p preview-v7.mp4
```

应额外输出：

- 2.70–2.95 秒眼睛慢放
- 8.00–8.25 秒眼睛慢放
- 0 秒与 12 秒差异图

---

# 13. 最终完成判定

只有全部满足才可写“修改完成”：

- [ ] 仓库或受控交付物中存在真实 Live2D Runtime 模型
- [ ] Wallpaper Engine 实际显示 Live2D 人物，不是静态母图叠加
- [ ] 人物能够明显但自然地转向晶体
- [ ] 12 秒内有两次局部眨眼
- [ ] 眼睛无重影
- [ ] 人物边缘无切片或双影
- [ ] 星河是独立图层
- [ ] 碎片使用独立透明素材
- [ ] 第 0 秒与第 12 秒闭环
- [ ] 高中低质量均能运行
- [ ] 鼠标、点击、音频和标题未被破坏
- [ ] 生成最终可导入 Wallpaper Engine 的发布包

在这些条件未满足前，HANDOFF 和 PR 必须写明“方法已交接，实际制作未完成”。
