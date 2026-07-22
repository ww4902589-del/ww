# 丽芙·霁梦动态壁纸 v7
# Codex 一次性交付文件：最终意见、修复结论、执行合同与复查标准

> 本文件是 v7 后续工作的唯一总交付文件。
>
> 用途：一次性告诉 Codex 当前问题、最终技术路线、工具边界、素材要求、代码要求、阶段门槛、验收证据和禁止事项。
>
> 状态：`IMPLEMENTATION_NOT_COMPLETE / BLOCKED_BY_ASSET_PRODUCTION`
>
> 任何旧文档与本文件冲突时，以本文件为准。

---

# 1. 最终结论

当前 v6 不满足用户要求，不能继续通过调透明度、扩大局部裁剪区域或增加整帧叠加来修复。

已确认的问题：

1. 人物动作依赖母图局部重绘，不是真正的人物变形或骨骼运动。
2. 头、颈、肩、发丝和身体之间没有可靠联动，因此视觉上人物几乎不动。
3. `motion-blink.png` 不是独立眼睑素材，继续叠加会产生双睫毛、眼球残影和脸部灰雾。
4. 背景星河和碎片来自母图区域重复绘制，不是独立空间层，因此出现透明重影。
5. 自动语法检查和循环数学检查通过，不代表视觉效果通过。

最终技术路线已经由用户确认：

```text
人物：Live2D Cubism
眨眼：E1，定义为 Live2D 眼睑/眼睛开合参数
背景：独立星河层 + 独立碎片粒子系统
运行环境：Wallpaper Engine Web Wallpaper
渲染结构：人物使用 Cubism WebGL Canvas；背景和效果使用 Canvas 2D
```

---

# 2. 本文件的权威性

Codex 启动后必须先读取：

1. 根目录 `HANDOFF.md`
2. 本文件 `CODEX_V7_FINAL_ONE_SHOT_DELIVERY.md`
3. `AGENTS.md`
4. `PROJECT_STATUS_V7.yaml`
5. `INPUT_ASSET_CONTRACT_V7.yaml`
6. `QA_EVIDENCE_REQUIREMENTS_V7.md`

以下旧文件仅供历史追溯，不得用于决定 v7 技术实现：

- `IMPLEMENTATION_PLAN.md`
- `MOTION_DESIGN_OPTIONS.md`
- 旧版 v6 `TASKS.md` 中的 Canvas 人物切片方案
- Git 历史中的 v6 动作参数

若旧文件要求制作 `eyes-open.png / eyes-half.png / eyes-closed.png` 并覆盖人物眼睛，该要求在 v7 中失效。

---

# 3. Codex 能力边界

## 3.1 Codex 可以直接完成

- 读取和审查仓库代码
- 编写 JavaScript / TypeScript
- 接入 Cubism SDK for Web 的加载逻辑
- 创建 Canvas 分层结构
- 编写星河和碎片粒子系统
- 编写静态 fallback
- 编写资源验证、路径验证、JSON 验证和循环验证脚本
- 编写构建、打包和 QA 自动化
- 创建缺失资产报告
- 在 GitHub 中记录状态和证据路径

## 3.2 Codex 不能假定自己已经完成

除非运行环境真实提供相应 GUI 工具和操作能力，否则 Codex 不得声称已经完成：

- Photoshop 人物分层
- 遮挡区域补绘
- Cubism ArtMesh
- Warp Deformer / Rotation Deformer
- Cubism Physics
- `.cmo3` 编辑
- Runtime 模型导出
- Cubism Viewer 人工视觉验收

缺少这些真实产物时，必须记录：

```text
BLOCKED_BY_ASSET_PRODUCTION
```

不得创建伪造、空白或无法加载的 `.psd`、`.cmo3`、`.moc3`、`.model3.json`、`.physics3.json`、`.motion3.json` 来绕过阻塞。

---

# 4. 输入资源合同

## 4.1 唯一角色母图

```text
assets/master-keyframe.png
SHA-256: d782f341a7b6801b78d902686e063135e7a48a3c10700b5ef97c3ea66221d268
```

用途：

- 人物分层的唯一外观基准
- 干净背景补绘的原始参考
- 静态 fallback 参考

不得把其中已烘焙的人物与 Live2D 人物同时显示。

## 4.2 仅供动作和闭眼参考

```text
assets/motion-blink.png
SHA-256: 92e547d91598f86bf6e7121bae58ae91722c59f0903537fa0ff531cbfa61ce8d

assets/motion-reach.png
SHA-256: ab2e5300b44720c5ea34d512c53036cf6388b457633e268a5fe8977b1a323010
```

这两张图：

- 可以作为姿势或闭眼线条参考
- 不得作为最终运行时叠加层
- 不得从其中裁整块脸或整块人物覆盖 Live2D
- 不得用于生成第二个可见人物

## 4.3 审计要求

正式工作开始前必须记录：

- 每张图片宽高
- 色彩空间
- Alpha 通道状态
- SHA-256
- 文件是否损坏
- 实际恢复路径

若输入哈希不一致，停止并报告，不得继续推断素材内容。

---

# 5. 最终工具链

## 5.1 人物分层和补绘

首选：

- Adobe Photoshop
- CLIP STUDIO PAINT

可辅助：

- Photoshop Remove Tool
- Content-Aware Fill
- Clone Stamp
- Live2D 官方 PSD 预处理脚本
- ImageMagick

导入 Cubism 的 PSD 必须：

- RGB
- 8 bit/channel
- sRGB
- 图层名唯一
- 可动部件独立
- 不保留会破坏导入的图层蒙版和未合并剪贴结构

## 5.2 Live2D

使用：

- Live2D Cubism Editor 稳定版
- Cubism Viewer
- Cubism SDK for Web
- Cubism Web Framework
- Cubism Core for Web

版本原则：

1. 开始工作时，把实际安装版本记录到 `qa/environment.md`。
2. 同一阶段内不得自动升级版本。
3. Framework、Core 和模型导出版本必须兼容。
4. 不从不明 CDN 加载 Core、Framework 或模型文件。

## 5.3 背景

使用：

- Photoshop / CLIP STUDIO PAINT：分离背景和碎片
- Canvas 2D：星河和粒子
- ImageMagick：透明边缘和尺寸检查
- ffmpeg：循环预览和慢放证据

默认不引入 PixiJS。只有 Canvas 2D 经真实性能测试无法满足目标时，才单独申请更换。

## 5.4 Wallpaper Engine

- 使用 Web Wallpaper
- 所有运行资源使用项目内相对路径
- 不依赖外部服务器
- 在编辑器预览和桌面实际壁纸模式中分别验证

---

# 6. 最终渲染架构

```text
wallpaper-stage
├─ background-canvas   Canvas 2D，干净背景、星河、碎片
├─ live2d-canvas       WebGL，仅绘制一个 Live2D 人物
├─ effects-canvas      Canvas 2D，点击涟漪、音频效果、局部光效
└─ title-layer         保留现有标题
```

必须满足：

- `background_clean.png` 不含人物
- `live2d-canvas` 只出现一个人物
- `effects-canvas` 不重新绘制人物脸、头、手或身体
- Live2D 加载失败时可显示单张静态 fallback
- fallback 模式不得叠加 `motion-blink.png` 或 `motion-reach.png`

禁止“双人物结构”：

```text
烘焙人物背景 + Live2D 人物 = 禁止
静态人物 fallback + Live2D 人物同时显示 = 禁止
motion-reach 人物 + Live2D 人物 = 禁止
```

---

# 7. 人物素材分层合同

至少需要：

```text
Face_Base
EyeWhite_L / EyeWhite_R
Iris_L / Iris_R
Highlight_L / Highlight_R
Eyelash_Upper_L / Eyelash_Upper_R
Eyelid_Lower_L / Eyelid_Lower_R
Eyebrow_L / Eyebrow_R
Hair_Front_Center
Hair_Front_L / Hair_Front_R
Hair_Side_L / Hair_Side_R
Hair_Back_01..03
Head_Ornament
Neck_Front
Neck_Backfill
Torso
Shoulder_L / Shoulder_R
UpperArm
Forearm
Hand
Fingers_Front / Fingers_Back
Skirt_Front / Skirt_Back
Ribbon_01 / Ribbon_02
Cloth_Backfill
```

每个会移动部件后方必须补绘完整：

- 前发移动后，额头和脸不能出现透明洞
- 头部转动后，颈部不能断裂
- 手臂移动后，躯干和袖口不能缺失
- 裙摆移动后，后层衣物不能空白

阶段 1 通过条件：

- PSD 能被 Cubism Editor 正常导入
- 所有图层名唯一
- 隐藏任一前景部件，后方没有透明洞
- Alpha 边缘无白边、黑边或脏边
- 提交分层总览和补绘证据
- 人工批准已写入 `PROJECT_STATUS_V7.yaml`

---

# 8. Live2D 参数与实际动作

必须区分“模型参数范围”和“本次动作实际关键帧”。

## 8.1 模型参数范围

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

## 8.2 本次 12 秒 motion 的实际建议值

```text
0.0s:
ParamAngleX = 0
ParamAngleY = 0
ParamAngleZ = 0
ParamBodyAngleX = 0
ParamEyeBallX = 0

2.0s:
保持接近初始状态

5.2s:
ParamAngleX = +8
ParamAngleY = -2
ParamAngleZ = +1
ParamBodyAngleX = -2
ParamEyeBallX = +0.20
ParamArmReach = 0.35

8.2s:
保持上述注视状态

12.0s:
全部回到 0 秒值
```

说明：

- `ParamAngleX` 的 `+8` 是 Cubism 参数值，不等于现实中旋转 8 度。
- 不得直接把参数拉到 `+30` 作为本次动作。
- 最终值应在 Cubism Viewer 中依据脸型稳定性微调，但不得改变角色设计。

## 8.3 联动顺序

```text
眼球少量先动
→ 头部转向晶体
→ 颈部和身体轻微反向补偿
→ 前发轻跟随
→ 长发、飘带、裙摆通过 Physics 延迟
```

不得只旋转整张头部贴图。

## 8.4 呼吸

- 6 秒周期
- 12 秒内两次
- 主要改变胸肩和身体参数
- 不得用整个人物上下漂移代替呼吸

---

# 9. E1 眨眼的唯一含义

v7 中：

```text
E1 = Live2D 眼睑 ArtMesh + ParamEyeLOpen / ParamEyeROpen
```

E1 不再表示：

- 三张半透明眼睛 PNG 覆盖
- 椭圆裁切脸部
- 压缩整块眼区高度
- 在睁眼上再叠闭眼睫毛

每只眼至少分离：

- 眼白
- 虹膜/瞳孔
- 高光
- 上睫毛/上眼睑
- 下眼睑

参数：

```text
ParamEyeLOpen / ParamEyeROpen
1.0 = 睁眼
0.5 = 半闭
0.0 = 闭眼
```

时间：

```text
第一次中心：2.80 秒附近
第二次中心：8.10 秒附近
单次总时长：0.14–0.20 秒
```

建议曲线：

```text
1.0 → 0.5 → 0.0 → 0.5 → 1.0
```

眨眼门槛：

- 无双睫毛
- 无虹膜残影
- 无灰色眼眶
- 无矩形裁剪边缘
- 鼻子和嘴不移动
- 发丝和头饰不闪烁
- 左右眼默认同步

任意一项失败，阶段 2 不得通过。

---

# 10. Runtime 导出合同

真实 Cubism 输出至少包含：

```text
assets/live2d/liv/
├─ liv.moc3
├─ liv.model3.json
├─ liv.physics3.json
├─ liv.cdi3.json
├─ textures/
│  ├─ texture_00.png
│  └─ ...
└─ motions/
   └─ idle_turn.motion3.json
```

允许存在：

```text
liv.pose3.json
expressions/*.exp3.json
```

要求：

- `liv.model3.json` 中路径全部为项目内相对路径
- 模型先在 Cubism Viewer 加载成功
- 纹理无错位
- Physics 正常
- 12 秒动作真实闭环
- 没有真实导出文件时不得进入“人物接入完成”状态

---

# 11. Cubism Web 接入要求

目录建议：

```text
src/live2d/
├─ live2d-app.ts
├─ live2d-model.ts
├─ live2d-motion-controller.ts
└─ live2d-config.ts

src/background/
├─ starfield.js
└─ fragments.js
```

加载顺序：

```text
初始化 Framework
→ 创建 WebGL Canvas
→ 加载 model3.json
→ 加载 moc3、纹理、physics、motion
→ 创建模型实例
→ 更新 motion
→ 更新 eye blink
→ 更新 physics
→ 绘制模型
```

每帧顺序：

```text
1. deltaTime
2. 12 秒时间轴
3. motion
4. blink
5. physics
6. Live2D draw
7. background/fragment draw
8. interaction/audio/effects
```

Live2D 可见后，旧人物绘制必须停用。

## Cubism Core 许可处理

- 不得未经确认把 Cubism Core 提交到公开仓库
- 必须记录实际 SDK/Core 来源和版本
- 公开仓库中可保留明确的本地注入说明
- 缺少 Core 时状态为 `BLOCKED_BY_CUBISM_CORE`
- 占位说明不等于运行时接入完成

---

# 12. 星河独立层

需要真实输出：

```text
assets/background/
├─ background_clean.png
├─ starfield_far.png
├─ starfield_mid.png
├─ starfield_near.png
├─ nebula_overlay.png
└─ starfield_config.json
```

要求：

- `background_clean.png` 不含人物
- 人物原位置补绘完整
- 星河 PNG 不含人物轮廓和标题
- 旋转时不露空角
- 不能旋转整张母图

运动建议：

```text
far:  1–2° 往返幅度
mid:  2–4° 往返幅度
near: 3–6° 往返幅度，透明度更低
```

使用 12 秒周期函数；0 秒和 12 秒的位置、速度和透明度必须一致。

---

# 13. 碎片粒子系统

每个碎片必须是独立透明图片，不得每帧从母图裁切。

```text
assets/background/fragments/
├─ near_01.png
├─ near_02.png
├─ mid_01.png
├─ mid_02.png
├─ far_01.png
└─ fragments.json
```

配置至少包含：

```json
{
  "id": "near_01",
  "image": "near_01.png",
  "depth": "near",
  "xNormalized": 0.72,
  "yNormalized": 0.24,
  "amplitudeXPx": 18,
  "amplitudeYPx": 12,
  "rotationAmplitudeDeg": 4,
  "phaseOffset": 0.13,
  "opacity": 0.82
}
```

循环必须由相位函数决定，禁止最终实现使用持续累加的：

```js
x += velocity;
```

质量档：

```text
高：12–16 块
中：8–12 块
低：4–8 块
```

近景碎片不得遮挡脸、眼睛、手和标题。

---

# 14. 保留功能

以下功能不得删除：

- 鼠标视差
- 点击涟漪
- 音频响应
- 艺术标题
- 画质档位
- 静谧模式
- 超宽屏适配

可重构其调用方式，但功能必须通过回归测试。

---

# 15. 阶段执行与阻塞规则

## Stage 0：输入审计

Codex 可执行。

输出：

- 资源路径、尺寸、哈希
- 工具和版本清单
- 缺失文件清单
- 当前阻塞状态

## Stage 1：PSD 分层和补绘

需要 Photoshop/CLIP STUDIO 等 GUI 制作。

通过条件：

- 真实 PSD
- 图层总览
- 遮挡补绘证据
- Cubism 导入测试
- 人工批准

缺失时：

```text
BLOCKED_BY_ASSET_PRODUCTION
```

## Stage 2：Cubism 模型

需要 Cubism Editor。

通过条件：

- 真实模型工作文件或受控交付记录
- Runtime 导出
- 转头
- E1 眨眼
- Physics
- Cubism Viewer 证据
- 人工批准

缺失时不得假实现。

## Stage 3：Web 接入

Codex 可执行，但必须存在真实 Runtime 模型和可用 Core。

通过条件：

- 模型真实加载
- 只显示一个人物
- 静态 fallback 正常
- 旧人物叠加已停用

## Stage 4：背景

Codex 和图像工具协作。

通过条件：

- 干净背景
- 独立星河层
- 独立碎片 PNG
- Canvas 运动可见
- 不出现母图重影

## Stage 5：综合 QA

通过条件：

- Wallpaper Engine 实机
- 两次眨眼逐帧证据
- 0/12 秒闭环证据
- 高中低画质
- 回归功能
- 最终发布包

任何阶段不得仅凭“代码已写”自动进入下一阶段。

---

# 16. 固定 QA 证据

必须创建或引用：

```text
qa/environment.md
qa/stage-0/input-audit.md
qa/stage-1/psd-layer-overview.png
qa/stage-1/occlusion-repaint-report.md
qa/stage-2/cubism-viewer.png
qa/stage-2/parameter-table.md
qa/stage-2/blink-first-slow.mp4
qa/stage-2/blink-second-slow.mp4
qa/stage-3/model-load-console.txt
qa/stage-3/single-character-proof.png
qa/stage-4/background-layers.png
qa/stage-4/fragments-preview.mp4
qa/stage-5/loop-12s.mp4
qa/stage-5/frame-0-vs-frame-12-diff.png
qa/stage-5/performance.md
qa/stage-5/regression-checklist.md
```

没有相应证据时，对应验收项不得勾选。

禁止伪造截图、视频、控制台日志和性能数据。

---

# 17. 状态文件更新规则

`PROJECT_STATUS_V7.yaml` 必须成为机器可读的阶段状态。

建议结构：

```yaml
version: v7
status: BLOCKED_BY_ASSET_PRODUCTION
implementation: NOT_COMPLETE
current_stage: 0
next_allowed_stage: 1

approvals:
  stage_1_assets:
    approved: false
    approved_by: null
    approved_at: null
  stage_2_cubism:
    approved: false
    approved_by: null
    approved_at: null

blockers:
  - layered_psd_missing
  - cubism_runtime_model_missing
  - cubism_core_not_injected
```

Codex不得自行把 `approved: false` 改成 `true`，除非用户或指定审批者明确批准并留下记录。

---

# 18. 禁止事项

- 禁止继续增大旧透明叠加的 Alpha
- 禁止继续扩大旧椭圆眨眼裁剪区
- 禁止用完整动作图覆盖人物
- 禁止用母图旋转冒充星河旋转
- 禁止同时显示烘焙人物和 Live2D 人物
- 禁止把三张眼睛 PNG 叠在 Live2D 眼睛上
- 禁止制造空 `.moc3` 或伪造模型 JSON
- 禁止把目录结构或占位文件称为模型接入完成
- 禁止把语法检查通过称为视觉验收通过
- 禁止绕过 Stage 1、Stage 2 的人工资产门槛
- 禁止在未检查许可前向公开仓库提交 Cubism Core
- 禁止使用在线 CDN 作为 Wallpaper Engine 最终依赖

---

# 19. 最终完成判定

只有全部满足，才可写“修改完成”：

- 真实 Live2D Runtime 模型存在并成功加载
- 人物明显但自然地向晶体转头
- 头、颈、肩、头发和身体联动自然
- 12 秒内两次 E1 眨眼
- 无双睫毛、眼球残影和脸部闪烁
- 背景中只有一个人物
- 星河来自独立图层
- 碎片来自独立透明资源
- 第 0 秒和第 12 秒位置、速度、透明度、亮度闭环
- 高、中、低画质可运行
- 鼠标、点击、音频、标题、静谧模式和超宽屏正常
- 有完整 QA 证据
- 有最终 Wallpaper Engine 发布包

未满足时必须使用准确状态：

```text
方法已交付，实际制作未完成
```

或：

```text
BLOCKED_BY_ASSET_PRODUCTION
BLOCKED_BY_CUBISM_CORE
BLOCKED_BY_VISUAL_QA
```

---

# 20. 本次复查结果

已修复的执行偏差来源：

- 已允许 Live2D WebGL，不再与旧 `AGENTS.md` 的 WebGL 禁令冲突
- 已明确旧 v6 Canvas 人物切片路线失效
- 已把 E1 锁定为 Live2D 眼睑参数
- 已禁止双人物绘制
- 已明确 Codex 与 GUI 美术工具的能力边界
- 已加入真实资产阻塞状态
- 已加入 Cubism Core 许可和缺失处理
- 已把模型参数范围与实际 motion 关键帧分开
- 已定义机器可读审批状态
- 已固定 QA 证据文件
- 已禁止把占位结构和自动检查称为完成

仍然存在且无法仅靠代码解决的真实阻塞：

- 分层 PSD 尚未制作
- 遮挡区域尚未补绘
- Cubism 模型尚未制作
- Runtime 模型尚未导出
- Cubism Core 尚未按许可和版本完成本地注入
- Wallpaper Engine 最终视觉 QA 尚未完成

因此本次交付的准确结论是：

```text
Codex 执行意见与防偏差合同已一次性交付完成；壁纸实际修改仍未完成。
```

---

# 21. 给 Codex 的启动指令

```text
读取 HANDOFF.md 和 CODEX_V7_FINAL_ONE_SHOT_DELIVERY.md。
不要继续调整 v6 的透明叠加代码。
先执行 Stage 0 输入审计，并更新 PROJECT_STATUS_V7.yaml。
检查分层 PSD、Cubism Runtime 模型和 Cubism Core 是否真实存在。
若任一关键资产缺失，输出 BLOCKED_BY_ASSET_PRODUCTION 或 BLOCKED_BY_CUBISM_CORE，列出缺失项，并停止人物实现。
不得伪造模型资产。
在阻塞期间，可以准备失败即关闭的 Web 加载骨架、背景星河/碎片代码和 QA 验证脚本，但不得声称人物修改完成。
每阶段只在真实证据和人工批准存在后继续。
```
