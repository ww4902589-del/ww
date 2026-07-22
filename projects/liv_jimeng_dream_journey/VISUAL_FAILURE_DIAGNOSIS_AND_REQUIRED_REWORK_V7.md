# 丽芙·霁梦动态壁纸 v7
# 视觉失败诊断与强制返工意见

> 记录来源：用户实际观看反馈 + 当前 GitHub 代码复查。
>
> 当前判定：`VISUAL_QA_FAILED`
>
> 本文件不是调参建议，而是对当前动画结构的否决。后续 Codex 必须停止沿用现有“母图裁剪、缩放、透明叠加”方案。

---

# 1. 用户确认的实际问题

当前运行结果存在以下明显缺陷：

1. 所谓人物运动主要表现为整幅图画或局部图块的拉伸、缩放和轻微漂移。
2. 人物没有真实的身体运动感。
3. 手臂没有挥动，也没有肩、肘、腕和手指的关节链条。
4. 看不到骨骼或 Deformer 驱动形成的自然结构变化。
5. 没有可见眨眼。
6. 背景运动过弱，难以察觉。

这些问题说明当前版本没有完成用户选择的 Live2D Cubism 路线。

---

# 2. 当前代码为何必然产生“拉伸图画”效果

## 2.1 整张母图仍在缩放和漂移

当前 `drawImageLayer()` 会先计算：

```js
const breath = 1 + Math.sin(loopPhase * TAU) * 0.0028 * motion;
const r = coverRect(config.scale * breath, px + orbitX, py + orbitY);
ctx.drawImage(art, r.x, r.y, r.w, r.h);
```

这不是人物呼吸，而是整幅图像被放大、缩小和整体移动。

因此用户看到的是：

```text
图画拉伸
而不是胸腔、肩部和身体的局部呼吸
```

必须停止把整张母图的缩放当成人物运动。

## 2.2 人物局部动作仍是“完整图片 + 椭圆裁剪”

当前 `drawTransformedImageRegion()` 的核心仍然是：

```js
ctx.clip();
ctx.translate(...);
ctx.rotate(...);
ctx.scale(...);
ctx.drawImage(image, r.x, r.y, r.w, r.h);
```

即：

1. 在画面上建立椭圆裁剪区域；
2. 把一张完整人物图重新绘制进去；
3. 对整个裁剪区做位移、旋转或缩放。

这不是：

- Live2D ArtMesh
- Warp Deformer
- Rotation Deformer
- 骨骼/关节层级
- 独立肩、上臂、肘、前臂、腕和手指

所以即使数值变化，视觉上也只会像贴纸块移动或局部图画拉伸。

## 2.3 手臂没有关节链

当前手臂动作仅为：

```js
translateXPx: reachDistancePx * actionAmount
translateYPx: -1.5 * actionAmount
rotationRadians: arm.rotationDeg * actionAmount
```

而且绘制来源仍是完整 `motionArt` 的椭圆区域。

这意味着：

- 肩关节没有单独旋转
- 上臂没有独立变形
- 肘部没有弯曲参数
- 前臂没有延迟跟随
- 手腕没有单独转动
- 手指没有开合

“整块手臂区域平移几像素”不能被判定为挥动或骨骼动作。

## 2.4 当前眨眼不是真实眼睑动画

当前 `drawBlinkLocal()`：

- 从完整 `blinkArt` 中裁一块眼区；
- 用椭圆 Clip 覆盖；
- 对眼区做垂直缩放；
- 重新绘制到原脸上。

这不是 Live2D 眼睑网格闭合。

当 `blinkArt` 没有真实、干净、独立的闭眼素材时，结果会是：

- 完全看不到眨眼；
- 眼区仅轻微压缩；
- 或出现双睫毛、残影、灰雾。

因此当前眨眼实现必须删除，不能继续增大 Alpha 或扩大裁剪范围。

## 2.5 背景运动仍来自母图重复绘制

当前星河与碎片代码仍然：

```js
ctx.clip();
ctx.rotate(...);
ctx.drawImage(art, ...);
```

星河透明度约为：

```text
0.10 * motionAmount
```

碎片透明度约为：

```text
0.20–0.34
```

但它们都不是独立透明星河或独立碎片图片，而是从完整母图中裁区重绘。

结果：

- 运动幅度受限，否则立即产生重影；
- 透明度必须很低，否则画面出现双层；
- 透明度过低后，背景运动几乎不可见；
- 无法形成真正的前、中、后景深度。

所以“背景不明显”不是单纯参数过小，而是素材结构错误。

---

# 3. 返工结论

当前人物动画代码不应继续调参。

必须停止：

- 整张母图呼吸缩放
- 椭圆裁剪整图实现头部运动
- 椭圆裁剪整图实现手臂运动
- 完整 blink 图局部覆盖
- 从完整母图裁区模拟星河和碎片

以下函数在 v7 Live2D 成功接入后必须停用或删除：

```text
drawTransformedImageRegion()
drawBlinkLocal()
drawCharacterAction() 中基于 motionArt 的人物绘制
drawImageLayer() 中整图 breath 缩放
drawStarfieldRotation() 中重绘 art 的方案
drawFloatingFragments() 中重绘 art 的方案
```

现有代码可保留为 `legacy-v6-rejected/` 对照，不得继续作为发布运行路径。

---

# 4. 人物真实运动的最低要求

用户要求的是“人物真实运动”，不是图片拉伸。

最终人物必须由真实 Live2D 模型驱动，并至少存在以下结构：

```text
Head
Neck
Torso
Shoulder
UpperArm
Elbow
Forearm
Wrist
Hand
Fingers
FrontHair
SideHair
BackHair
Skirt
Ribbons
```

## 4.1 头部

必须使用：

```text
ParamAngleX
ParamAngleY
ParamAngleZ
ParamBodyAngleX
```

头部转动时：

- 眼球先少量移动；
- 脸部网格发生透视形变；
- 头饰和前发跟随；
- 颈部和躯干做反向补偿；
- 长发通过 Physics 延迟。

禁止只旋转整块头部 PNG。

## 4.2 手臂挥动/伸展

必须表现为清晰的关节链：

```text
肩部启动
→ 上臂旋转
→ 肘部弯曲
→ 前臂延迟跟随
→ 手腕转动
→ 手指舒展或轻收
```

建议建立自定义参数：

```text
ParamShoulderLift       0 .. 1
ParamUpperArmRotation  -1 .. 1
ParamElbowBend          0 .. 1
ParamForearmReach       0 .. 1
ParamWristRotation     -1 .. 1
ParamFingerOpen         0 .. 1
```

12 秒动作建议：

```text
0.0–2.0s
手臂初始状态，轻呼吸

2.0–5.2s
肩部先起，上臂向晶体方向抬起，肘部逐渐展开，前臂前送

5.2–7.6s
保持伸展，手腕做一次小幅可见转动，手指由轻收变为舒展

7.6–8.2s
短暂保持

8.2–12.0s
手指、手腕、前臂、肘、上臂、肩部按相反顺序自然回位
```

动作必须可见，但不得像机械挥手。关键是让观众能辨认出：

- 上臂角度发生变化；
- 肘部发生弯曲或展开；
- 手腕有独立转动；
- 手指有独立变化。

仅做 6–12 px 的整块平移不合格。

## 4.3 呼吸

呼吸必须通过局部模型参数完成：

- 胸腔轻微扩张；
- 肩部轻微上升；
- 颈部和头部微弱补偿；
- 衣物和发丝稍有延迟。

整张背景或整个人物 Canvas 的 Scale 不得作为呼吸主体。

---

# 5. 真实眨眼的强制要求

v7 的 E1 唯一实现为：

```text
Live2D 眼睑 ArtMesh
+
ParamEyeLOpen / ParamEyeROpen
```

必须分离：

- 眼白
- 虹膜/瞳孔
- 高光
- 上睫毛/上眼睑
- 下眼睑

时间：

```text
第一次：中心约 2.80 秒
第二次：中心约 8.10 秒
单次：0.16–0.22 秒
```

建议曲线：

```text
1.0 → 0.65 → 0.20 → 0.0 → 0.35 → 0.75 → 1.0
```

验收时必须逐帧确认：

- 能清楚看见眼睛闭合；
- 闭眼维持至少 1–2 帧；
- 无双睫毛；
- 无虹膜残留；
- 无脸部裁剪边缘；
- 鼻子、嘴和发丝不跳动。

在没有真实眼睑模型前，不允许用旧 blink 图伪造“完成”。

---

# 6. 背景运动必须达到可感知标准

背景不能再依靠低透明度母图重绘。

必须制作：

```text
background_clean.png
starfield_far.png
starfield_mid.png
starfield_near.png
nebula_overlay.png
独立 fragment PNG 8–16 块
```

建议视觉幅度（1920×1080）：

```text
星河 far：  1.5–2.5° 往返 + 2–4 px 视差
星河 mid：  3–4.5° 往返 + 5–9 px 视差
星河 near： 4.5–6° 往返 + 9–15 px 视差

碎片 far：  4–8 px
碎片 mid：  10–18 px
碎片 near： 18–30 px
近景碎片旋转：±3–6°
```

背景验收标准：

- 观看 2 秒内能察觉至少一个背景层正在移动；
- 观看完整 12 秒能分辨前、中、后景速度差异；
- 不通过整图重影制造运动；
- 不遮挡人物脸、眼睛、手和标题；
- 0 秒和 12 秒位置、速度、透明度一致。

晶体流光可以加强，但必须是独立渐变、纹理或 Shader/Canvas 流光，不得再次移动完整母图。

---

# 7. Codex 下一次执行要求

Codex 不得继续在当前 v6 人物函数中增加位移、旋转、缩放和 Alpha。

固定执行顺序：

1. 将当前运行路径标记为 `LEGACY_V6_VISUAL_REJECTED`。
2. 检查真实分层 PSD 是否存在。
3. 检查真实 Cubism Runtime 模型是否存在。
4. 检查 Cubism Core 是否按许可完成本地注入。
5. 缺失任一项时更新状态为阻塞，不得伪造模型。
6. 仅在真实模型存在后接入 Live2D 人物。
7. 删除可见的旧人物叠加路径。
8. 使用独立背景层和碎片素材重做背景。
9. 在 Wallpaper Engine 中录制真实 12 秒运行证据。

允许在资产阻塞期间完成：

- Live2D 加载器骨架；
- 资源缺失时 fail-closed；
- 独立背景粒子引擎；
- QA 自动化；
- 资产检查脚本。

但不得写“人物运动已完成”。

---

# 8. 强制 QA 证据

下一次提交必须至少提供：

```text
qa/stage-2/cubism-viewer.png
qa/stage-2/arm-chain-preview.mp4
qa/stage-2/blink-first-slow.mp4
qa/stage-2/blink-second-slow.mp4
qa/stage-3/single-character-proof.png
qa/stage-4/background-motion-12s.mp4
qa/stage-5/wallpaper-engine-12s.mp4
qa/stage-5/frame-0-vs-frame-12-diff.png
```

其中：

- `arm-chain-preview.mp4` 必须能看到肩、肘、腕和手指的顺序运动；
- 两个 blink 慢放必须能看到完整闭眼；
- `single-character-proof.png` 必须证明背景中没有第二个烘焙人物；
- 背景视频必须不依赖鼠标移动，也能看出持续运动。

没有这些证据，视觉验收不得通过。

---

# 9. 当前最终判定

```text
当前版本不是 Live2D 人物动画。
当前人物运动是整图/局部图块变换。
当前手臂没有真实关节链。
当前眨眼未完成。
当前背景运动结构和可见度均不合格。
```

因此当前状态必须保持：

```text
IMPLEMENTATION_NOT_COMPLETE
VISUAL_QA_FAILED
BLOCKED_BY_ASSET_PRODUCTION
```

不得把现有结果重新打包后称为 v7 成品。
