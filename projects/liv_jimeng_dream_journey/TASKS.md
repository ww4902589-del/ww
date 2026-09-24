# 丽芙·霁梦动态壁纸 v6 — Codex 任务清单

## 最终目标

- 保留 Wallpaper Engine Web 壁纸、Canvas 2D、鼠标互动、点击涟漪、音频响应、艺术标题、画质档位和静谧模式。
- 将人物主动作确定为缓慢转头；呼吸、手臂前送、手指舒展、发丝、裙摆和飘带作为辅助动作。
- 12 秒循环内仍眨眼两次，但用局部眼睛素材和遮罩重做，消除虚影。
- 增强星河缓慢旋转、碎片前中后景浮动、晶体内部流光和局部星尘汇聚。
- 所有动画在 0 秒和 12 秒的位置、速度、透明度和亮度一致。

## 阶段 0：恢复与基线

- [ ] 根据 `assets/ASSET_MANIFEST.md` 恢复三张 PNG 与 `preview.jpg`。
- [ ] 保存原版工程副本。
- [ ] 在 Wallpaper Engine 中录制原版 12 秒预览。
- [ ] 记录高、中、低画质下的帧率、加载时间和内存占用。
- [ ] 单独保存现有眨眼的局部截图，作为虚影对照。

## 阶段 1：运动配置集中化

建立统一配置对象，至少包含：

```js
const motionTuning = {
  loopDurationSeconds: 12,
  character: {
    headRotationDeg: 3.2,
    headShiftXPx: 6,
    headShiftYPx: 3,
    shoulderLiftPx: 3,
    armReachDistancePx: 9,
    wristRotationDeg: 3.5,
    hairFrontDriftPx: 3,
    hairBackDriftPx: 14,
    skirtDriftPx: 12,
    ribbonDriftPx: 20
  },
  blink: {
    centersSeconds: [2.8, 8.1],
    durationSeconds: 0.16
  },
  background: {
    starfieldRotationDeg: 6,
    fragmentNearAmplitudePx: 18,
    fragmentMidAmplitudePx: 10,
    fragmentFarAmplitudePx: 5,
    crystalGlowAmount: 0.16
  }
};
```

- [ ] 参数不得散落在多个函数中。
- [ ] 像素、角度和秒数必须在变量名中注明单位。

## 阶段 2：人物资源分区

建议准备：

```text
assets/character/head/char-head-base.png
assets/character/head/char-head-mask.png
assets/character/head/char-neck-blend-mask.png
assets/character/eyes/eyes-open.png
assets/character/eyes/eyes-half.png
assets/character/eyes/eyes-closed.png
assets/character/eyes/eyes-mask.png
assets/character/arm/char-arm-reach.png
assets/character/arm/char-hand.png
assets/character/hair/hair-front.png
assets/character/hair/hair-mid.png
assets/character/hair/hair-back.png
assets/character/cloth/skirt-front.png
assets/character/cloth/ribbons.png
```

- [ ] 记录每个资源相对母图的归一化坐标。
- [ ] 记录每个图层的旋转中心。
- [ ] 头颈、手臂和裙摆边缘必须使用柔和融合遮罩。
- [ ] 关闭全部动作时，结果应与静态母图一致。

## 阶段 3：转头主动作

时间轴：

- 0.0–2.0 秒：初始姿态与轻呼吸。
- 2.0–5.2 秒：缓慢向右前方转头。
- 5.2–8.2 秒：保持注视晶体。
- 8.2–12.0 秒：缓慢回位。

验收：

- [ ] 头部旋转在 2–4 度内。
- [ ] 头颈连接自然，不露底图。
- [ ] 肩颈有轻微反向补偿，避免贴纸旋转感。
- [ ] 瞳孔偏移不超过 1–2 px。
- [ ] 脸型、头饰和五官不变形。

## 阶段 4：辅助人物动作

- [ ] 呼吸周期建议 6 秒，12 秒完成两次。
- [ ] 肩部起伏 2–4 px，躯干缩放不超过 0.3%。
- [ ] 手臂向晶体前送 6–12 px，不做明显挥手。
- [ ] 手腕旋转 2–5 度，五指做轻收—舒展。
- [ ] 前发小幅跟随，长发和飘带延迟跟随。
- [ ] 发丝不得穿脸，手部不得双影、断腕或被特效遮挡。

## 阶段 5：局部眨眼独立里程碑

实现：

```text
睁眼 → 半闭 → 闭眼 → 半闭 → 睁眼
```

- [ ] 第一次中心约 2.8 秒。
- [ ] 第二次中心约 8.1 秒。
- [ ] 每次总时长 0.12–0.18 秒。
- [ ] 使用局部眼睛贴图替换与柔和遮罩。
- [ ] 禁止将完整 `motion-blink.png` 透明叠加到脸部。

必须逐帧确认：

- [ ] 无双睫毛。
- [ ] 无眼球残影。
- [ ] 无眼周发灰。
- [ ] 无脸、发丝或头饰闪烁。
- [ ] 鼻子和嘴不移动。
- [ ] 裁切边缘不可见。

未通过时停止，不进入背景阶段。

## 阶段 6：星河与碎片

- [ ] 星河只作用于独立星河层，不旋转整张母图。
- [ ] 12 秒累计旋转 4–8 度，中心靠近右侧晶体。
- [ ] 前景碎片移动 10–24 px，旋转 ±2–6 度。
- [ ] 中景碎片移动 6–14 px，旋转 ±1–4 度。
- [ ] 远景碎片移动 3–8 px，旋转 ±0.5–2 度。
- [ ] 每块碎片使用不同相位和方向。
- [ ] 不遮挡人物脸、手和标题。

推荐数据结构：

```js
const fragmentDefinitions = [
  {
    id: 'fragment-near-01',
    depth: 'near',
    positionNormalized: { x: 0.72, y: 0.26 },
    pivotNormalized: { x: 0.5, y: 0.5 },
    floatAmplitudePx: { x: 18, y: 12 },
    rotationAmplitudeDeg: 4.5,
    phaseOffset: 0.12,
    alpha: 0.88
  }
];
```

## 阶段 7：晶体与粒子

- [ ] 晶体边缘亮度呼吸 10%–20%。
- [ ] 内部数据纹缓慢向上或向中心流动。
- [ ] 人物手接近时适度增强，不能压过人物面部。
- [ ] 保留现有点击涟漪和音频叠加。
- [ ] 晶体附近星尘略活跃，人物面部附近密度降低。
- [ ] 高、中、低画质档和静谧模式继续有效。

## 阶段 8：综合时间轴

```text
0.0–2.0 秒：轻呼吸、星河启动、碎片错相浮动。
2.0–5.2 秒：缓慢转头、手臂前送、2.8 秒第一次眨眼。
5.2–8.2 秒：保持注视、手指舒展、晶体共鸣、8.1 秒第二次眨眼。
8.2–12.0 秒：人物与背景逐层回位，发丝和布料延迟回摆。
```

## 阶段 9：验证

人物：

- [ ] 转头可感知但不夸张。
- [ ] 头颈、手臂、五指和裙摆结构稳定。
- [ ] 人物脸始终为第一焦点。

循环：

- [ ] 第 0 秒与第 12 秒位置一致。
- [ ] 第 0 秒与第 12 秒速度一致。
- [ ] 第 0 秒与第 12 秒亮度一致。
- [ ] 没有闪切和突然回弹。

性能：

- [ ] 不在每帧创建大型数组或离屏画布。
- [ ] 图片和遮罩只加载、缓存一次。
- [ ] 关闭功能后跳过对应计算。
- [ ] 三档画质稳定，无持续内存增长。

回归：

- [ ] 鼠标视差正常。
- [ ] 点击涟漪正常。
- [ ] 音频响应正常。
- [ ] 标题和设置面板正常。
- [ ] 静谧模式和超宽屏裁切正常。

## 失败回退

- 头部切片明显：减小角度、扩大颈部融合区；不得回退为整帧混合。
- 眨眼仍有虚影：取消透明插值，改成更小区域的局部帧切换；不得恢复完整眨眼图叠加。
- 背景难以完整分层：仅分离星河叠加层和 4–8 块关键碎片；不得旋转母图。
- 性能不足：依次减少粒子、模糊半径、次要碎片和晶体流纹；不要先删除人物主动作和局部眨眼。
