# 丽芙·霁梦动态壁纸 v6
## Codex 详细任务清单、文件树与变量命名规范

> 适用工程：`liv_jimeng_dream_journey`  
> 工程类型：Wallpaper Engine Web 壁纸  
> 核心技术：HTML / CSS / JavaScript / Canvas 2D  
> 循环时长：12 秒  
> 主动作：人物缓慢转头  
> 眨眼：频率保持不变，但改为局部眼部动画  
> 背景增强：星河转动、碎片分层浮动、晶体内部流光

---

# 1. 最终目标

将现有“静态母图 + 整帧动作图透明叠加”的实现，升级为“分区动画 + 独立背景运动 + 局部眨眼”的结构。

最终效果应满足：

1. 人物动作比当前版本更明显，但仍然克制、优雅。
2. 人物以转头为主，呼吸、手臂、手指、头发、裙摆为辅助动作。
3. 眨眼仍维持 12 秒循环内两次，但不得出现眼周虚影、睫毛双层和脸部闪烁。
4. 背景中星河、记忆碎片和晶体都应具有可感知运动。
5. 所有运动首尾位置、速度和亮度一致，形成真正无缝循环。
6. 保留现有鼠标互动、点击涟漪、音频响应、标题、属性面板和静谧模式。
7. 不重构为 WebGL、Three.js、视频壁纸或场景壁纸。

---

# 2. Agent 防跑偏总规则

Codex 和所有子代理开始前必须读取 `AGENTS.md`、`TASKS.md` 和本文件。

## 2.1 允许修改

- `styles.css`，仅在新增调试面板或图层样式确有需要时修改
- `project.json`，仅在新增用户可调参数时修改
- `assets/` 下新增的透明 PNG、JSON 遮罩数据和调试资源
- `src/` 下的人物、眨眼、背景和渲染模块
- `scripts/` 下的资源检查、预览和打包脚本
- `qa/` 下的验收记录

## 2.2 禁止修改

- 不得替换当前角色母图或重新设计人物
- 不得改变人物服装、脸型、头饰和主体配色
- 不得删除现有交互、音频、标题和设置功能
- 不得引入 React、Vue、Three.js、PixiJS 等大型框架
- 不得将项目改为视频壁纸
- 不得为了“动作更明显”让人物大幅摇摆
- 不得通过重复叠加完整动作图解决人物动作
- 不得新增与当前任务无关的剧情、文字、UI 和功能
- 不得在未完成单模块验收前同时重写其他模块
- 不得自动修改发布包中的旧版本；只修改 v6 工作副本

## 2.3 固定执行顺序

1. 创建工作副本和备份
2. 建立 Agent 约束文件
3. 资源检查
4. 人物动作模块
5. 局部眨眼模块
6. 背景运动模块
7. 综合调参
8. 性能测试
9. 无缝循环测试
10. 打包 v6

任何子代理不得跳过顺序直接进行综合重构。

---

# 3. 推荐文件树

```text
liv_jimeng_dream_journey_v6/
├─ AGENTS.md
├─ TASKS.md
├─ CHANGELOG_AGENT.md
├─ IMPLEMENTATION_PLAN.md
├─ README.md
├─ index.html
├─ styles.css
├─ project.json
│
├─ assets/
│  ├─ base/
│  │  ├─ master-keyframe.png
│  │  ├─ motion-reach.png
│  │  └─ motion-blink-original.png
│  ├─ character/
│  │  ├─ head/
│  │  ├─ eyes/
│  │  ├─ torso/
│  │  ├─ arm/
│  │  ├─ hair/
│  │  └─ cloth/
│  ├─ background/
│  │  ├─ background-clean.png
│  │  ├─ starfield-overlay.png
│  │  ├─ starfield-mask.png
│  │  ├─ crystal.png
│  │  ├─ crystal-mask.png
│  │  ├─ crystal-flow-mask.png
│  │  └─ fragments/
│  └─ debug/
│     ├─ region-guide.png
│     └─ pivot-guide.json
│
├─ src/
│  ├─ config/
│  ├─ core/
│  ├─ character/
│  ├─ background/
│  ├─ interaction/
│  └─ debug/
├─ scripts/
└─ qa/
```

如果复杂拆分影响 Wallpaper Engine 兼容性，允许保持现有五个经典脚本入口，不引入打包器；新增功能应继续通过普通 `<script>` 顺序加载。

---

# 4. 软件与工具分工

## 4.1 Photoshop / Krita / Photopea

只用于：

- 人物区域分层
- 制作眼睛睁眼、半闭、闭眼三帧
- 制作柔和边缘遮罩
- 分离晶体和重点记忆碎片
- 输出带透明通道的 PNG

不得用于：

- 重画人物五官
- 改变角色设计
- 自动生成新服装
- 大规模补绘背景

## 4.2 VS Code + Codex

用于代码修改、模块拆分、验证脚本、修改记录和静态检查。

## 4.3 ImageMagick

用于检查图片尺寸、Alpha 通道、色彩空间、异常空图和过大资源：

```bash
magick identify assets/**/*.png
```

## 4.4 ffmpeg

用于输出 12 秒预览与首尾循环差异检查：

```bash
ffmpeg -framerate 60 -i frames/frame-%05d.png -c:v libx264 -pix_fmt yuv420p preview-v6.mp4
```

## 4.5 Wallpaper Engine

必须进行最终编辑器和桌面实机测试，包括高、中、低质量档，互动、音频、静谧模式和超宽屏适配。

---

# 5. 子代理配置

## 5.1 Motion Director

- 读取任务文件并分配子任务
- 只合并通过验收的模块
- 维护 `CHANGELOG_AGENT.md`
- 不直接重写所有代码

## 5.2 Asset Preparation Agent

只允许操作：

- `assets/character/`
- `assets/background/`
- `assets/debug/`
- 区域和锚点配置

输出图层、遮罩、归一化坐标、旋转中心和边缘融合说明。不得修改动画逻辑。

## 5.3 Character Motion Agent

只负责人头转动、肩颈联动、呼吸、手臂、手腕、发丝、裙摆和飘带；不得修改背景运动和眨眼素材。

## 5.4 Blink Agent

只允许处理眼部素材、眨眼函数和时间配置。必须单独验收后再合并。

## 5.5 Background Motion Agent

只负责星河、碎片、晶体流光和粒子局部引导；不得修改人物和眨眼。

## 5.6 QA & Performance Agent

默认只检查，不修改创作参数。输出问题等级、复现步骤、推荐修复位置和是否允许进入下一阶段。

---

# 6. 详细实施步骤

## 阶段 0：建立工作副本

- 复制当前工程为 v5 备份
- 新建 v6 工作目录
- 校验原工程能够正常启动
- 记录原版 FPS、加载时间和内存占用
- 保存原版 12 秒预览和眨眼局部截图

验收：原工程未被覆盖，v6 可独立启动，基线预览可用于对比。

## 阶段 1：约束与任务追踪

- 确认 `AGENTS.md`、`TASKS.md`、`CHANGELOG_AGENT.md` 和 QA 文件
- 每个子代理仅修改授权目录
- 每次提交记录修改目的和涉及文件

## 阶段 2：资源盘点与分层

- 检查并逐像素对齐 `master-keyframe.png`、`motion-reach.png`、`motion-blink.png`
- 标记头部、肩部、手臂、手部、发丝、裙摆、晶体和碎片区域
- 输出 `region-guide.png` 和 `pivot-guide.json`
- 制作局部眼睛三帧与柔和边缘遮罩
- 分离关键背景层

验收：新增 PNG 带 Alpha；坐标、偏移和锚点明确；头颈无硬边；眼睛不改变脸型和瞳孔位置。

## 阶段 3：人物基础分区渲染

建立统一图层绘制接口，支持锚点、旋转、位移、缩放、透明度和遮罩。关闭动作时必须与原图视觉一致。

```js
drawLayer({
  image,
  sourceRect,
  destinationRect,
  pivot,
  translateX,
  translateY,
  rotation,
  scaleX,
  scaleY,
  alpha,
  mask
});
```

禁止整张 `motion-reach` 长时间高透明度叠加。

## 阶段 4：转头主动作

时间轴：

| 时间 | 动作 |
|---|---|
| 0.0–2.0 秒 | 初始姿态和轻呼吸 |
| 2.0–5.2 秒 | 缓慢向右前方转头 |
| 5.2–8.2 秒 | 保持注视，达到峰值 |
| 8.2–12.0 秒 | 缓慢回到初始状态 |

参数：

- 头部旋转 2°–4°
- 头部水平位移 4–8 px
- 头部垂直位移 2–4 px
- 颈肩反向补偿 0.5°–1.5°
- 肩部位移 1–3 px
- 视线局部偏移最多 1–2 px

验收：无贴纸旋转感；颈部不拉伸或露底图；脸型、头饰、眼睛不变形；动作可感知但不晃眼。

## 阶段 5：呼吸、手臂和手指

- 呼吸周期建议 6 秒，12 秒内两次
- 胸肩起伏 2–4 px
- 躯干缩放不超过 0.3%
- 手臂向晶体前送 6–12 px，旋转 1°–3°
- 手腕旋转 2°–5°
- 手指只做轻收和舒展，不做明显挥手

验收：手臂是辅助动作；五指清晰；手腕无断裂；特效不遮手。

## 阶段 6：头发、裙摆和飘带

- 前发：1–4 px，0.3°–1.0°
- 中层发束：4–10 px，0.8°–2.0°
- 长发尾部：8–18 px，1.5°–3.5°
- 裙摆：8–16 px，1.0°–3.0°
- 飘带：12–24 px，2.0°–5.0°

各层错相，头部先动、发丝后跟随；首尾必须同时闭环。

## 阶段 7：局部眨眼

保留两次眨眼：中心时间 2.8 秒和 8.1 秒，每次 0.12–0.18 秒。

帧序列：

```text
睁眼 → 半闭 → 闭眼 → 半闭 → 睁眼
```

优先实现：Canvas 局部眼部贴图替换 + 柔和遮罩。

禁止：完整 `motion-blink.png` 在脸部范围内透明叠加。

逐帧验收：无双睫毛、无眼球残影、无眼周发灰、无脸部闪烁、无发丝跳动、闭眼不改变鼻口位置。眨眼未通过，不进入背景合并。

## 阶段 8：星河转动

- 不旋转整张母图
- 只旋转或位移独立星河层
- 12 秒总旋转 4°–8°
- 旋转中心位于右侧晶体附近
- 可叠加 3–10 px 弧形位移

验收：星河流动可感知；人物和文字不旋转；四角不露空；不眩晕。

## 阶段 9：碎片分层浮动

```js
const fragmentDefinitions = [
  {
    id: 'fragment-near-01',
    depth: 'near',
    imageKey: 'fragmentNear01',
    position: { x: 0.72, y: 0.26 },
    pivot: { x: 0.5, y: 0.5 },
    floatAmplitude: { x: 18, y: 12 },
    rotationAmplitudeDeg: 4.5,
    phaseOffset: 0.12,
    alpha: 0.88
  }
];
```

幅度：

- 前景 10–24 px，旋转 ±2°–±6°
- 中景 6–14 px，旋转 ±1°–±4°
- 远景 3–8 px，旋转 ±0.5°–±2°

验收：不同方向和速度；不遮脸、手和标题；前中后景明确；12 秒准确回位。

## 阶段 10：晶体内部流光

- 边缘亮度呼吸
- 内部数据纹缓慢向上或向中心流动
- 人物手靠近时适度增强
- 保留点击涟漪
- 亮度脉冲 10%–20%
- 与人物动作峰值错开 0.2–0.5 秒
- 不增加大面积暖金色

验收：晶体仍是第二焦点；不压过脸；流光不遮回应手；点击和音频响应正常。

## 阶段 11：粒子和星尘

- 保留现有粒子系统
- 增加晶体局部吸引中心
- 晶体附近稍强、面部附近降低密度
- 保持高、中、低三档质量
- 静谧模式继续降低运动

## 阶段 12：综合时间轴

```text
0.0–2.0 秒
- 人物轻呼吸
- 星河开始缓慢流动
- 碎片错相浮动
- 发丝和裙摆进入循环

2.0–5.2 秒
- 人物缓慢转头
- 手臂轻微前送
- 2.8 秒第一次眨眼
- 晶体流光逐渐增强

5.2–8.2 秒
- 转头达到峰值并保持
- 手指舒展
- 星河和碎片运动最容易感知
- 晶体产生轻度共鸣
- 8.1 秒第二次眨眼

8.2–12.0 秒
- 头部、肩部和手臂缓慢回位
- 发丝和裙摆延迟回摆
- 晶体亮度回落
- 碎片、星河和粒子回到初始相位
```

---

# 7. 变量命名与配置

使用完整英文单词；时间统一以秒为单位并使用 `Seconds` 后缀；配置层角度使用 `Deg`；像素使用 `Px`；强度统一 0–1。

推荐名称：

```js
loopDurationSeconds
elapsedSeconds
loopPhase
headRotationDeg
headShiftXPx
headShiftYPx
neckCounterRotationDeg
shoulderLiftPx
torsoBreathScale
armReachDistancePx
wristRotationDeg
fingerRelaxAmount
hairFrontDriftPx
hairMidDriftPx
hairBackDriftPx
skirtDriftPx
ribbonDriftPx
blinkCentersSeconds
blinkDurationSeconds
starfieldRotationDeg
fragmentFloatAmplitudePx
fragmentRotationAmplitudeDeg
crystalGlowIntensity
crystalFlowOffset
particleAttractionStrength
```

集中配置：

```js
const motionTuning = {
  loopDurationSeconds: 12,
  character: {
    head: {
      rotationDeg: 3.2,
      shiftXPx: 6,
      shiftYPx: 3,
      turnStartSeconds: 2.0,
      turnPeakSeconds: 5.2,
      returnStartSeconds: 8.2
    },
    breathing: {
      cycleSeconds: 6,
      shoulderLiftPx: 3,
      torsoScaleAmount: 0.0025
    },
    arm: {
      reachDistancePx: 9,
      rotationDeg: 2.0,
      wristRotationDeg: 3.5
    },
    hair: {
      frontDriftPx: 3,
      midDriftPx: 8,
      backDriftPx: 14
    },
    cloth: {
      skirtDriftPx: 12,
      ribbonDriftPx: 20
    }
  },
  blink: {
    centersSeconds: [2.8, 8.1],
    durationSeconds: 0.16
  },
  background: {
    starfieldRotationDeg: 6,
    crystalGlowAmount: 0.16,
    fragmentNearAmplitudePx: 18,
    fragmentMidAmplitudePx: 10,
    fragmentFarAmplitudePx: 5
  }
};
```

---

# 8. 推荐函数

```js
loadMotionAssets()
updateAnimationState()
renderWallpaperFrame()
calculateLoopPhase()
calculateQuietModeBlend()
drawCharacterBase()
drawCharacterRigMotion()
drawHeadMotion()
drawBreathingMotion()
drawArmMotion()
drawHairMotion()
drawClothMotion()
drawBlinkLocal()
drawBackgroundBase()
drawStarfieldRotation()
drawFloatingFragments()
drawCrystalFlow()
drawMotionParticles()
drawMaskedLayer()
calculateOscillation()
calculateCyclicEase()
convertDegreesToRadians()
mapNormalizedRectToCanvas()
```

---

# 9. 建议提交里程碑

1. `chore: create v6 working copy and agent guardrails`
2. `feat: add layered asset manifest and debug region guides`
3. `feat: implement subtle head turn and breathing motion`
4. `feat: add arm, hair and cloth secondary motion`
5. `fix: replace full-frame blink blend with local eye animation`
6. `feat: add rotating starfield and layered fragment motion`
7. `feat: add crystal flow and localized particle guidance`
8. `perf: optimize canvas layers and quality presets`
9. `test: validate seamless 12-second loop and Wallpaper Engine behavior`
10. `release: package liv_jimeng_dream_journey v6`

---

# 10. QA 验收标准

## 人物

- 转头清晰但不过大
- 头颈连接自然
- 呼吸没有明显缩放感
- 手臂与手指结构稳定
- 头发和裙摆错相运动
- 面部始终是第一焦点

## 眨眼

- 两次眨眼均保留
- 无虚影、双睫毛、眼周闪烁、脸型变化和发丝跳变

## 背景

- 星河运动可感知
- 碎片具有前中后景和不同相位
- 晶体流光稳定
- 背景不抢人物、不眩晕

## 循环

- 第 0 秒与第 12 秒位置、速度、亮度一致
- 不存在突然回弹或闪切

## 性能与回归

- 高、中、低质量稳定
- 静谧模式正常
- 无粒子或数组泄漏
- 图片只加载一次
- 鼠标视差、点击涟漪、音频响应、标题、设置和超宽屏裁切正常

---

# 11. 性能控制

1. 缓存静态遮罩，禁止每帧重复创建。
2. 预计算裁切区域，避免每帧创建大量对象。
3. 复用粒子对象。
4. 低质量模式降低粒子、模糊、晶体流光层和前景碎片。
5. 关闭某功能后跳过对应绘制函数。
6. 不引入重型框架和未经验证的 WebGL 重构。

---

# 12. 失败回退策略

## 头部切片感明显

减小旋转和位移，扩大颈部融合遮罩，保留肩部联动；不得回退为整帧动作图高透明度叠加。

## 眨眼仍有虚影

停止透明混合，改为局部帧切换，缩小替换区域并重新制作三帧；不得继续使用完整 blink 图。

## 背景无法完整分层

仅新增透明星河层并分离 4–8 块关键碎片，母图继续作为底层；不得旋转整张母图。

## 性能不足

优先降低粒子、模糊、次要碎片和晶体流纹层，不得先删除转头主动作和局部眨眼。

---

# 13. 最终交付

```text
丽芙霁梦_溯梦归途_WallpaperEngine_v6.zip
丽芙霁梦_v6_12秒预览.mp4
丽芙霁梦_v5_v6_对比.mp4
qa/performance-report.md
qa/visual-checklist.md
CHANGELOG_AGENT.md
```

发布包不得包含临时截图、调试日志、旧版备份、未使用资源或源编辑文件。

---

# 14. Codex 首次执行指令

```text
先读取 AGENTS.md、TASKS.md、IMPLEMENTATION_PLAN.md 和 qa/acceptance.md。
不要立即修改代码。

第一步只完成：
1. 检查现有工程结构和资源。
2. 记录人物、眨眼和背景动画的当前实现方式。
3. 输出拟修改文件清单。
4. 输出风险清单。
5. 确认不会删除现有交互、音频、标题、设置面板和静谧模式。

完成分析后停止，等待批准。不要自动全面重构。
```
