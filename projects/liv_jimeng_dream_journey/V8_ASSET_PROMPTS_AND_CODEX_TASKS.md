# 丽芙·霁梦动态壁纸 v8
# 资产生成提示词包 + Codex 任务分发清单

> 目标：以“多张透明 PNG + 分层帧切换”的方式制作动态壁纸素材。
>
> 本文件把视觉约束、资产清单、分组提示词、单图差异、Codex 任务拆分和验收标准合并成一次性交付规范。

---

## 1. 参考图与总约束

本方案参考两张图：

- 图 A：站姿版，人物向右侧晶体伸手，长发与飘带横向展开，记忆晶片较多。
- 图 B：坐姿版，人物坐姿漂浮，画面更柔和，右侧晶体更亮，晶片数量更集中。

### 必须保留

- 蓝白冷色主调
- 晶体梦境感
- 星空与空灵氛围
- 白发、头饰、晶体感服装
- “丽芙·霁梦”的识别度

### 必须避免

- 光噪
- 发光脏点
- 闪粉感亮粒
- 锁链纹路
- 机械束缚感图样
- 整图拉伸冒充动作
- 半透明覆盖冒充眨眼

### 允许调整

- 人物姿态、头部方向、手臂动作
- 背景层隐现
- 记忆晶片数量与内容
- 晶体门结构和构图

---

## 2. 效率最高的资产生产方式

不要给 80 多张图片分别写完全不同的大段提示词。正确路线：

1. 固定统一总风格提示词；
2. 按 Base、Head、Eyes、Arm、Hair、Cloth、Background、Fragments、Memories 分组；
3. 每组共用一段提示词；
4. 每张图只补充“与上一帧的差异”；
5. Codex 按文件名和时间轴调度。

这样能提高角色一致性、出图效率、后期替换效率和资产管理效率。

---

## 3. 资产目录

```text
assets/frame-v8/
├─ references/
│  ├─ reference_A_standing.svg
│  └─ reference_B_sitting.svg
├─ base/
│  ├─ background_clean.png
│  ├─ body_base.png
│  ├─ title_base.png
│  └─ crystal_base.png
├─ head/
│  ├─ head_00_front.png
│  ├─ head_01_turn.png
│  ├─ head_02_turn.png
│  ├─ head_03_turn.png
│  ├─ head_04_turn.png
│  ├─ head_05_hold.png
│  ├─ head_06_return.png
│  ├─ head_07_return.png
│  └─ head_08_front.png
├─ eyes/
│  ├─ eye_open.png
│  ├─ eye_half.png
│  ├─ eye_closed.png
│  ├─ eye_half_return.png
│  └─ eye_open_hold.png
├─ arm/
│  ├─ arm_00_rest.png
│  ├─ arm_01_lift.png
│  ├─ arm_02_lift_more.png
│  ├─ arm_03_extend.png
│  ├─ arm_04_extend_more.png
│  ├─ arm_05_hold.png
│  ├─ arm_06_wrist_turn.png
│  ├─ arm_07_return.png
│  └─ arm_08_rest.png
├─ hair/
│  ├─ hair_back_00.png ... hair_back_04.png
│  └─ hair_front_00.png ... hair_front_04.png
├─ cloth/
│  ├─ skirt_00.png ... skirt_03.png
│  └─ ribbon_00.png ... ribbon_03.png
├─ background/
│  ├─ starfield_far_00.png ... 03.png
│  ├─ starfield_mid_00.png ... 03.png
│  ├─ starfield_near_00.png ... 03.png
│  └─ crystal_glow_00.png ... 03.png
├─ fragments/
│  ├─ frag_lt_00.png ... 02.png
│  ├─ frag_top_00.png ... 02.png
│  ├─ frag_mid_00.png ... 02.png
│  └─ frag_rb_00.png ... 02.png
├─ memories/
│  └─ memory_01.png ... memory_06.png
└─ manifests/
   ├─ asset-checklist-v8.json
   ├─ sprite-layout-v8.json
   ├─ frame-timeline-v8.json
   └─ z-order-v8.json
```

---

## 4. 所有图片共用的总风格提示词

> 以蓝白冷色调、星空梦境、晶体幻想、轻盈发丝与布料、精致二次元人物立绘为基准，保留白发、黑蓝晶体头饰、冰晶感服装、空灵而克制的气质。整体为高完成度二次元插画质感，轮廓清晰，材质通透，画面干净。允许人物姿态变化、背景层隐现、记忆碎晶数量变化，但必须保持“丽芙·霁梦”的视觉识别度。禁止光噪、禁止发光脏点、禁止闪粉感亮粒、禁止锁链纹路、禁止机械束缚感图样、禁止重影式眨眼、禁止不自然手指。

---

## 5. 分组提示词与单帧差异

### 5.1 Base

**background_clean.png**

> 制作不含人物的深邃星空与远景银河底层，画面干净、有空间层次，保留背景显隐余地；禁止光噪、随机发光脏粒和锁链纹路。

**body_base.png**

> 制作人物静态身体底层，保留服装核心结构、胸口与腰部晶体装饰、主裙体块与角色识别度；不包含独立眼睛帧与大动作手臂帧。

**title_base.png**

> 制作右下角“丽芙·霁梦”标题，字体空灵精致，微发光但不过曝，允许轻微显隐。

**crystal_base.png**

> 制作右侧大型晶体主体，轮廓锐利优雅，内部结构抽象、梦幻、通透；禁止链节式纹路和随机发光噪点。

### 5.2 Head

组提示词：

> 在 1920×1080 透明画布上制作角色头部帧图，只表现头部、颈部连接与相关前发，保持白发、黑蓝晶体头饰、淡紫眼睛、清冷安静神情与稳定脸型。头部逐步转向右侧晶体，再回到初始。禁止背景污染、光噪与链状装饰。

单帧差异：

- head_00_front：初始朝向。
- head_01_turn：轻微转头。
- head_02_turn：转头约 25%。
- head_03_turn：转头约 50%。
- head_04_turn：转头约 75%。
- head_05_hold：到达目标角度并稳定注视。
- head_06_return：开始回位。
- head_07_return：接近初始。
- head_08_front：恢复初始，与 head_00 基本一致。

### 5.3 Eyes

组提示词：

> 只制作眼睛局部帧，保留紫色眼瞳、干净眼线与睫毛结构，保证位置完全一致。禁止模糊覆盖、双睫毛、眼区灰雾与发光脏点。

- eye_open：正常睁眼。
- eye_half：半闭过渡。
- eye_closed：完整闭眼。
- eye_half_return：闭眼后回开。
- eye_open_hold：眨眼结束后的稳定睁眼。

### 5.4 Arm

组提示词：

> 制作角色右臂动作帧，表现从初始到向右侧晶体伸手、到达目标、手腕轻转、手指舒展，再回到初始。必须体现肩、肘、前臂、手腕、手指的层级变化，不能只是整块平移；手指自然，不畸形。

- arm_00_rest：起始。
- arm_01_lift：肩部起势。
- arm_02_lift_more：上臂带动，肘部变化。
- arm_03_extend：前臂前送。
- arm_04_extend_more：接近目标。
- arm_05_hold：稳定指向晶体。
- arm_06_wrist_turn：手腕轻转，手指舒展。
- arm_07_return：回位过渡。
- arm_08_rest：回到起始。

### 5.5 Hair

后发组：

> 表现大面积长发轻盈、丝滑、失重漂浮般的摆动。保持大形体流向，不杂乱，不加发光噪粒。

前发组：

> 配合头部方向变化，保持覆盖关系稳定，不遮坏眼睛，不闪烁，不出现脏边。

帧差异统一为：00 初始、01 轻摆、02 中摆、03 较大摆动、04 峰值。

### 5.6 Cloth

裙摆组：

> 表现层层花边轻盈漂浮，不得像整块布缩放；保持蓝白冰晶质感。

飘带组：

> 表现失重空间中的流动和拖尾，方向明确、线条流畅；禁止链条样或节状重复图样。

帧差异统一为：00 初始、01 轻摆、02 中摆、03 峰值。

### 5.7 Background

**远景星层**

> 星点稀疏、结构清晰、氛围深远，允许轻微明暗与显隐；禁止闪粉噪声。

**中景星层**

> 具有星河流向与空间层次，亮度略高但仍干净，允许轻微相位变化。

**近景星层**

> 服务于近景运动与背景显隐，不遮挡人物，亮度克制，不杂乱闪烁。

**晶体流光**

> 表现有结构的晶体呼吸和流动能量，不使用锁链纹路，不爆闪，不制造噪点发光。

00~03 依次为初始、轻微偏移、更明显流动、峰值相位。

### 5.8 Fragments

> 制作不同区域的晶体碎片组。碎片必须有明确晶体结构与大小对比，是晶体而非亮片或闪粉；允许位置和透明度变化，但不得链状串联。

- frag_lt：左上。
- frag_top：上方。
- frag_mid：中部。
- frag_rb：右下。
- 00：初始位置；01：中间位置；02：峰值位置。

### 5.9 Memories

> 制作晶体切面记忆晶片，内部为角色的不同记忆神态；晶片边缘通透清晰，内部画面温柔细腻，不喧宾夺主，不加光噪或锁链图样。

- memory_01：温柔注视。
- memory_02：沉静思考。
- memory_03：闭眼或祈愿。
- memory_04：轻微回望。
- memory_05：柔和微笑或平静。
- memory_06：悲伤、思念或希望等较强情绪。

---

## 6. Codex 任务拆分

### Stage 1：参考和约束固化

1. 保存两张仓库内参考预览图。
2. 将本文件作为 v8 资产生产规范。
3. 在 HANDOFF 中明确：禁止光噪、禁止锁链纹路、允许背景隐现。

### Stage 2：资产清单和校验

1. 创建目录结构。
2. 创建 `asset-checklist-v8.json`。
3. 编写 `scripts/verify_frame_assets.js` 检查文件齐全、尺寸、Alpha 和命名。
4. 缺资产时输出 `BLOCKED_BY_ASSET_PRODUCTION`。

### Stage 3：时间轴和层级

1. 创建 `frame-timeline-v8.json`。
2. 创建 `z-order-v8.json`。
3. 创建 `sprite-layout-v8.json`。
4. 固定 12 秒、24 FPS、288 运行帧。

### Stage 4：播放引擎

1. 按运行帧选择 head、eyes、arm、hair、cloth、background、fragments、memories。
2. 实现背景显隐。
3. 禁止回退到整图裁切和透明重叠方案。

### Stage 5：验收

1. 无光噪和发光脏点。
2. 无锁链纹路和链状装饰。
3. 两次眨眼清晰。
4. 手臂体现肩、肘、腕变化。
5. 背景 2 秒内可感知运动。
6. 第 0 秒与第 12 秒闭环。

---

## 7. 时间轴摘要

- 时长：12 秒
- 帧率：24 FPS
- 总运行帧：288
- 0.0–2.0s：待机
- 2.0–5.0s：转头和手臂展开
- 2.8s：第一次眨眼
- 5.0–7.67s：保持和细节变化
- 8.1s：第二次眨眼
- 9.0–12.0s：回位与闭环

---

## 8. 验收标准

只有全部满足才算通过：

1. 无光噪。
2. 无锁链纹路。
3. 头部变化可见。
4. 手臂动作可见，不是整块平移。
5. 眨眼清晰且无重影。
6. 背景运动清晰。
7. 正确使用背景显隐。
8. 角色识别度稳定。
9. 循环首尾闭环。
10. 不使用整图拉伸冒充动作。
