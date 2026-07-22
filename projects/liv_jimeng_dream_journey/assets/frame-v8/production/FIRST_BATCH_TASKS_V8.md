# V8 第一批母版素材任务清单

## 目标

本批次只建立能够验证角色一致性、图层拼接、锚点和透明边缘的最小真实素材集。未通过本批次验收前，不得扩展到完整头部、手臂、头发或背景序列。

## 输入依据

执行前必须读取：

1. `references/reference_A_standing.svg`
2. 用户提供的原始高清参考图
3. `manifests/sprite-layout-v8.json`
4. `production/PROMPT_TEMPLATES_V8.md`
5. `production/ASSET_PRODUCTION_GUIDE_V8.md`
6. `scripts/verify_frame_assets.js`

仓库中的 SVG 仅是缩略预览，不能代替用户提供的原始高清参考图。

## 本批次必须交付的 6 个 PNG

| 顺序 | 输出文件 | 目录 | 作用 | 前置条件 |
|---:|---|---|---|---|
| 1 | `body_base.png` | `assets/frame-v8/base/` | 静态躯干母层 | 角色母图已批准 |
| 2 | `head_00.png` | `assets/frame-v8/head/` | 初始头部母帧 | `body_base.png` 已完成 |
| 3 | `arm_00.png` | `assets/frame-v8/arm/` | 初始手臂母帧 | `body_base.png` 已完成 |
| 4 | `hair_back_00.png` | `assets/frame-v8/hair/` | 后发母帧 | `head_00.png` 已完成 |
| 5 | `hair_front_00.png` | `assets/frame-v8/hair/` | 前发母帧 | `head_00.png` 已完成 |
| 6 | `eye_open.png` | `assets/frame-v8/eyes/` | 睁眼母帧 | `head_00.png` 已完成 |

所有文件必须为：

- 1920×1080；
- 8-bit RGBA PNG；
- 全画布对齐；
- 非所属区域透明；
- 不允许空透明占位；
- 不允许黑底、白底或半透明矩形补丁。

---

## Task A — 角色母图冻结

### 操作

- [ ] 选定唯一角色母图。
- [ ] 确认脸型、五官、发型、发饰、服装和身体比例。
- [ ] 确认人物在 1920×1080 画布中的最终位置。
- [ ] 记录头颈、肩、肘、腕锚点。
- [ ] 确认画面中不存在锁链、链环、束缚装置或发光脏点。

### 通过条件

- [ ] 角色身份与参考图一致。
- [ ] 人物比例自然，不出现头身失衡。
- [ ] 手部清晰，手指数目和结构正常。
- [ ] 服装暴露程度不增加。
- [ ] 母图获得人工批准。

### 失败处理

角色身份、比例或服装结构不一致时，停止后续拆层并返回母图修正。

---

## Task B — `body_base.png`

### 只允许包含

- 躯干主体；
- 不参与独立动画的固定服装区域；
- 与其他层连接所需的少量接缝补全。

### 必须移除并透明化

- 头部和颈部上方独立区域；
- 独立手臂；
- 前发、后发；
- 裙摆和飘带；
- 眼睛局部层；
- 晶体、碎片、记忆晶片、标题和背景。

### 验收

- [ ] 画布为 1920×1080。
- [ ] 非人物区域透明。
- [ ] 与 `head_00.png` 的颈部接缝无破洞。
- [ ] 与 `arm_00.png` 的肩部接缝无破洞。
- [ ] 不包含重复头部或重复手臂。
- [ ] 不含白边、黑边、发光噪点。

---

## Task C — `head_00.png`

### 内容

- 初始头部；
- 必要的颈部连接区；
- 与头部不可分离的局部发饰；
- 眼睛区域应为透明孔位或采用项目约定的可替换结构，不能同时烘焙完整眼睛并叠加 `eye_open.png`。

### 锚点

- 颈部连接点使用 `sprite-layout-v8.json` 中 `head.neck_anchor`。
- 与躯干连接误差不得超过 3 px。

### 验收

- [ ] 脸型和五官身份与母图一致。
- [ ] 发饰形状、位置和色彩一致。
- [ ] 颈部无断层。
- [ ] 不出现双眼、双睫毛或脸部矩形补丁。
- [ ] 非头部区域透明。

---

## Task D — `arm_00.png`

### 内容

- 初始姿态的独立手臂；
- 对应袖口、手腕饰品和手指；
- 仅保留与手臂结构直接相关的内容。

### 锚点

- 肩点固定在 `sprite-layout-v8.json` 中 `arm.joint_anchors.shoulder`。
- 与躯干连接误差不得超过 3 px。

### 验收

- [ ] 肩、肘、腕结构清楚。
- [ ] 手指数目正确。
- [ ] 手指不粘连、不缺失、不增生。
- [ ] 袖口和饰品与母图一致。
- [ ] 非手臂区域透明。

---

## Task E — `hair_back_00.png`

### 内容

只包含人物后发及其必要根部连接区域。

### 验收

- [ ] 发根与头部位置一致。
- [ ] 总发量、长度和发色与母图一致。
- [ ] 不包含脸、躯干、前发或背景。
- [ ] 透明边缘无灰边、白边或黑边。

---

## Task F — `hair_front_00.png`

### 内容

只包含前发、刘海和必须与前发绑定的局部发饰。

### 验收

- [ ] 不遮挡后续眼睛替换区域。
- [ ] 与 `head_00.png` 拼接无跳变。
- [ ] 不包含完整脸部或背景。
- [ ] 发丝边缘干净。

---

## Task G — `eye_open.png`

### 内容

只包含睁眼状态所需的眼球、眼睑和睫毛局部。

### 验收

- [ ] 眼位与头部完全对齐，误差不超过 1 px。
- [ ] 左右眼大小、角度和瞳色与参考一致。
- [ ] 不包含鼻、嘴、脸颊或前发。
- [ ] 不出现双虹膜、双睫毛或灰色眼雾。
- [ ] 边界不能形成矩形脸部补丁。

---

## Task H — 首次组合预览

按正式 z-order 合成：

```text
background_clean（临时可使用纯色 QA 底，不得提交为生产资产）
hair_back_00
body_base
arm_00
head_00
hair_front_00
eye_open
```

### 预览检查

- [ ] 头颈接缝自然。
- [ ] 肩部接缝自然。
- [ ] 前后发层次正确。
- [ ] 眼睛未被错误遮挡。
- [ ] 人物整体比例与母图一致。
- [ ] 无重复人物部件。
- [ ] 无透明边污染。

临时 QA 底图只能用于检查，不能复制为 `background_clean.png`，也不能让资产验证器误判为正式完成。

---

## Task I — 自动验证

运行：

```bash
cd projects/liv_jimeng_dream_journey
node scripts/verify_frame_assets.js --write-report=qa/first-batch-validation-v8.json
```

由于完整资产尚未提供，报告总体状态仍会是 `BLOCKED_BY_ASSET_PRODUCTION`。本批次只检查这 6 个文件自身不得出现：

- PNG 格式错误；
- 尺寸错误；
- 完全透明占位；
- 缺失 Alpha；
- 透明像素隐藏 RGB 污染过高。

---

## Task J — 人工审核记录

填写：

```text
assets/frame-v8/production/first-batch-review-v8.json
```

只有以下项目全部通过，才允许继续生产 `head_01~08` 和 `arm_01~08`：

- [ ] reference identity match
- [ ] body proportion approved
- [ ] head-neck seam approved
- [ ] shoulder seam approved
- [ ] hands and fingers natural
- [ ] eyes aligned and clean
- [ ] no glow noise
- [ ] no chain/restraint pattern
- [ ] no alpha halo
- [ ] layer isolation correct

---

## 本批次完成定义

第一批只有在以下条件同时满足时才可标记为完成：

1. 6 个真实 PNG 文件均存在；
2. 自动文件检查无针对这 6 个文件的致命错误；
3. 首次组合预览通过；
4. 人工审核状态为 `APPROVED`；
5. 未使用假透明占位、整图裁切或复制层；
6. `PROJECT_STATUS_V8.yaml` 更新为 `MOTHER_FRAME_BATCH_APPROVED`。

否则状态保持：

```text
BLOCKED_BY_ASSET_PRODUCTION
```
