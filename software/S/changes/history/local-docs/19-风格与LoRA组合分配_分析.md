# S / ComfyBatch V2.20 分析：风格与 LoRA 的组合、分配

分析日期 2026-09-30。目的：为后续「风格 + LoRA 的组合与分配」建立事实底稿。
全部数字来自本机实测，非文档转述；命令与脚本可复现。

---

## 一、S 是什么

| | |
|---|---|
| 身份 | S / ComfyBatch **V2.20**（目录名 `S-v2.14` 是历史遗留，程序版本看 `APP_VERSION`） |
| 形态 | Python 后端 + 单页前端，PyInstaller 打包成 `S.exe`（17 MB，未签名） |
| 入口 | `src/comfybatch_v2_app.py`（HTTP + 路由）· `src/comfybatch_v2_core.py`（核心，172 KB，最大） |
| 前端 | `src/index.html` + `app.css` + `app.js`，**五阶段**：文件与任务 / 工作流与参数 / **模型风格与 LoRA** / 检查与运行 / 成图预览与确认 |
| 数据目录 | `%LOCALAPPDATA%\ComfyBatch-S\` —— `settings.json`（预置与档案 34 KB）· `library.db`（SQLite 作品库 3.7 MB）· `reviews\`（98 个 json）· `object_info.cache.json`（4.5 MB 节点 schema） |
| 它做的事 | 批量把提示词合集送进 ComfyUI，编译工作流、预检资源、止损、出图、审图、打回重做 |

---

## 二、三层数据结构（这是组合分配的全部操作面）

### 2.1 风格目录 —— 只读，来自磁盘

`ResourceInventory._styles()` 扫描 `<comfy_root>/custom_nodes/**/styles/*.json`。

```python
{"library": <文件名 stem>, "library_cn": ..., "name", "name_cn", "display_name",
 "prompt": <模板>, "negative_prompt", "thumbnail"}
```

**实测家底：72 个库 / 3946 条词条。**
- 全部 3946 条都有中文名、都有 `prompt` 模板、都有缩略图（前端走 `/api/style-thumbnail`）
- **`negative_prompt` 有值的：0 条** —— 而 `PromptCompiler.compile_negative()` 里
  `style.get("negative_prompt")` 是取的，也就是说这条路径永远取到空。风格目录目前
  只提供正向模板，不提供负面。

### 2.2 LoRA 档案 —— 可写，`lora_profiles`

- LoRA 清单本身扫 `models/loras`，优先走 ComfyUI `/models/loras`（含 `extra_model_paths`）
- 触发词可从 safetensors 元数据**自动抽取**：`modelspec.tags` / `trigger_words` / `ss_trigger_words`
- 用户档案（键 = LoRA 名）：`{display_name, trigger_words[], trigger_status, use_triggers}`

**实测家底：13 个 LoRA 文件，14 条档案。**

### 2.3 组合方案 —— 可写，`style_lora_presets`（**这就是"组合"的载体**）

```json
{"id": "897fee5dfab6", "name": "手游+数字",
 "styles": [{"catalog": "krea2_anime_动漫_styles", "name": "Polished Mobile Gacha Style"}],
 "loras":  [{"name": "BArtstyle-KREA2-V2-1024.safetensors", "strength": 0.55, "use_triggers": true}]}
```

**硬约束（写在 `save_style_lora_preset()` 里）：**

| 约束 | 值 | 出处 |
|---|---|---|
| 风格条数上限 | **4** | `list(value.get("styles") or [])[:4]` —— 超出直接丢弃，不报错 |
| LoRA 条数 | 不限 | 全量保留 |
| 强度范围 | **-2.0 ~ 2.0** | `max(-2.0, min(2.0, …))` —— 超出静默夹紧 |
| 名称唯一 | 是 | 重名抛「已经存在同名预设」 |
| 最低要求 | 至少 1 个风格或 1 个 LoRA | 否则抛错 |

**数量：出厂 18 个**（`src/default_presets.json`）· **用户现有 17 个** · **墓碑 147 个**（已删除）。

---

## 三、从「组合」到「出图」的完整链路

```text
style_lora_presets                 ← 组合定义在这里
        │  assign_style_lora_preset(preset_id, indexes)   ← 分配在这里
        ▼
prompt item.metadata["style_lora_preset_id"]
        │  resolve_bundle_presets()      生成时解析
        ├─ _resolve_styles(styles, inventory)  用 (library,name) 查目录，补齐 prompt 模板
        └─ _resolve_loras(loras)               用 name 查 lora_profiles，补齐 trigger_words
        ▼
BatchConfig.styles / .loras / .style_application
        ▼
① 风格：两条路
   · style_application == "native" → 不写进提示词，改为工作流插桩
     STYLE_INJECTION_WHITELIST["easy stylesSelector"]：把风格选择器插进
     采样器 positive 与 CLIPTextEncode 之间。
     锚点契约：采样器 positive 必须**直连** CLIPTextEncode。
     锚点不满足 → 绝不猜插桩位置，回退为提示词编译，并在 style_injection 台账里说明。
   · 否则 → PromptCompiler.apply_style_templates()
     模板含 {prompt} 则占位替换，否则 `原文本, 模板` 追加。多条按列表顺序叠加。

② LoRA：**串行链**，不是集合
   KSampler 的 model/clip 起点之后，按 loras 列表顺序依次串
   LoraLoader（有 CheckpointLoader）或 LoraLoaderModelOnly（有 UNET）。
   strength_clip 缺省 = strength_model。
   ⇒ **顺序有语义，改变顺序就是改变结果。**

③ 触发词：只对 use_triggers=true 的 LoRA 收集 trigger_words，
   去重后追加一行「LoRA触发词：a, b。」到正向提示词末尾。
```

---

## 四、家底与可变空间

### 4.1 图鉴与软件口径**完全对齐**（这条决定了图鉴能不能当依据）

`C:\Users\wyj\Desktop\_atlas_D.json`（7018 张图）对本机清单：

| 维度 | 图鉴 | 软件 | 交集 | 结论 |
|---|---|---|---|---|
| 风格库 | 24 | 72 | **24** | 图鉴用过的库全在清单里，零漂移 |
| 风格词条 | 206 | 3945 | **204** | 2 条例外见下 |
| LoRA | 7 | 13 | **7** | 完全一致 |
| 方案名 | 158 | 17 | **17** | 当前方案全被图鉴覆盖 |

2 条例外：`Analog Glitch Nostalgia`、`Neon Cel Smear` 出现在生成记录里但**不在风格目录**——
是直接写进工作流的，不是目录词条。

**结论：风格库 / 风格词条 / LoRA 三个维度是干净的键，可以直接拿图鉴的 7018 张实测图做组合决策的依据。**

### 4.2 从未用过的空间

| | 已用 | 可用 | 未用 |
|---|---|---|---|
| 风格库 | 24 | 72 | **48** |
| 风格词条 | 204 | 3945 | **3741** |
| LoRA | 7 | 13 | **6** |

未用的 6 个 LoRA 里，**3 个根本不适用于出图**：

| LoRA | 性质 | 能否用于出图 |
|---|---|---|
| `MiniMaxH3/minimax_h3_fl2v_turbo_8step…` | 视频（首尾帧转视频） | ✗ |
| `minimax_h3_fl2v_lightx2v_turbo_4step…` | 视频 | ✗ |
| `minimax_h3_turbo_4步加速ema…` | 视频加速 | ✗ |
| `QuadView_krea2_v1` | 四视图/四面板 | ⚠ 与软件的 `SINGLE_SUBJECT_GUARD`（单主体守卫）语义冲突，且软件自带重复面板检测 |
| `Krea2_TextFusion_Refusal_Reduction` | 拒答抑制 | ⚠ 功能型，非风格型 |
| `Krea2-编辑identity_edit_v1_2` | 身份编辑 | ⚠ 编辑型，非生成型 |

**所以真正可用于「风格组合」的 LoRA 其实只有 7 个，且这 7 个已经全在用。**
想扩大组合空间，主要靠 48 个未用风格库 / 3741 条未用词条，不是靠 LoRA。

### 4.3 方案维度**不是干净的键**（重要提醒）

图鉴里有 158 个方案名，但它混了三种来源：

| 来源 | 个数 | 说明 |
|---|---|---|
| 当前方案 | 17 | 真方案 |
| **已删除方案** | **131** | 在墓碑里，样图仍在图鉴里 |
| 文件名反推的伪方案 | 10 | 如 `手游`、`水彩`、`手游_黑色漫画`——**从未作为方案存在过** |
| `(未命名)` | — | 3676 张（52%），无方案信息 |

**做分析要按风格/风格库/LoRA 分组，不要按方案名分组。**

---

## 五、现有的分配机制

`assign_style_lora_preset(preset_id, indexes)`

- **输入**：方案 id + **提示词编号列表**（1-based，对应导入的提示词合集 `bundle.items`）
- **效果**：给每个 `items[i-1].metadata["style_lora_preset_id"] = preset_id`
- **前端**：多选提示词（`selectedPromptIndexes`，支持全选/反选/清空）→ 点「分配」
- **优先级**：任务级预设 > 全局「风格与 LoRA」区（页面明写「任务级预设优先于这里的全局组合」）
- **可清空**：传空 preset_id 即恢复全局组合
- 另有 `assign_image_preset` 按同样方式分配**图像规格**（比例/像素）

也就是说：**「按编号把一套风格+LoRA 分配给指定的一批提示词」这条链路已经存在且可用。**

---

## 六、做组合分配前必须知道的 7 件事

### ① 质量反馈是空的 —— 最大的约束

| | |
|---|---|
| `library.db` → `works` 650 行 | `review_status`：**待确认 649 · 已通过 1** |
| `works.note` | **0 条**（没有任何问题描述） |
| `reviews/` 2119 条 | status 只有 `completed` 2105 / `submitted` 14，**无通过/打回结论** |

**没有任何累计的好坏信号。** 这意味着：组合分配目前**不可能做成"按实测效果排序"**，
只能做成**原理驱动 + 图鉴共现证据驱动**。如果想要数据驱动，得先开始审图打分——
这是后续要不要投入的一个决策点。

### ② LoRA 是串行链，顺序有语义

列表顺序 = 串联顺序。改顺序就是改结果，不是改排序。所以「组合」的定义里**必须包含顺序**，
不能当成集合。

### ③ 风格最多 4 条，且超出的静默丢弃

`[:4]` 是硬上限，超出不报错。写方案时要注意。

### ④ 强度静默夹紧到 ±2.0

超出范围不报错，直接夹。当前方案里实际用到的强度区间是 **0.25 ~ 0.75**，
没有任何一条用到负值或超过 0.8。

### ⑤ 风格目录不提供负面词

3946 条词条的 `negative_prompt` 全为空。负面只能来自：任务级负面 + 全局负面词。

### ⑥ `style_application` 默认值不一致

`BatchConfig` 的 dataclass 默认是 `"prompt"`，但 `from_dict()` 缺省给 `"native"`。
走 UI 的路径用 `native`；直接构造对象的路径可能拿到 `prompt`。两条路的语义不同
（一个插桩、一个写提示词），排查时要知道这一点。

### ⑦ 图鉴里同一方案名下混着不同修订版的图

例：`漫画` 方案声明 2 个 LoRA，实测只有 96/199 张命中它们，另外 103 张用了
**未声明**的 `krea2-masterpieces-v51`。说明方案被改过，图鉴是历史累积。
**按方案统计时要按时间（批次 mtime）切片。**

---

## 七、触发词现状（组合里最容易白配的一块）

当前 17 个方案里，标了 `use_triggers=true` 的 LoRA 引用共 22 处：

| LoRA | 引用 | 触发词 | 状态 |
|---|---|---|---|
| `Krea2 & Anima] Yoneyama Mai米山舞 - Artist Style` | 11 | `Yoneyama Mai Style` | manual |
| `BArtstyle-KREA2-V2-1024` | 6 | `azure youth anime art style` | manual |
| `houTuIllustrationLoLV1.0_Krea2RawV1.0` | 3 | `htl7q9vx` | confirmed（元数据自带） |
| `Detailer-KREA2` | **2** | **无** | 标了 true 却无词 → 白标 |

**只有 3 个 LoRA 真正会注入触发词。** 其中 2 个是手工填的、1 个是元数据自带的。
其余 LoRA（masterpieces / REDZ15_DetailDaemonZ / Krea2Mivt4tStyle）都设了
`use_triggers=false` —— 这个处理是对的，因为它们确实没有触发词。

---

## 八、下一步的可行方向

### 我这边可以直接做的（不需要你再提供东西）

1. **按风格库盘点未用词条**：48 个未用库里，哪些与现有 7 个 LoRA 的家族/训练方向匹配，
   可以低成本试。这需要有 LoRA 的元数据（已读，13 个里 6 个有 `base_model: krea2`）。
2. **用图鉴做共现分析**：206 条已用词条的实测共现，找出「经常一起出现」和「从未组合过
   但技法互补」的配对，作为下一轮组合的候选。
3. **补触发词**：把 11 个无词的 LoRA 逐个查 safetensors 元数据，能补的补上，
   不能补的明确标 `unknown` 而不是留 `use_triggers=true` 的白标。
4. **出一份组合矩阵草案**：以「风格库 × LoRA 家族 × 强度区间」为轴，给出建议组合与
   理由，标清哪些有实测依据、哪些是外推。

### 需要你先定的两件事

1. **要不要开始审图打分？** 现在 649 条全挂「待确认」。哪怕只把最近 2 个批次
   （112 + 若干）过一遍，就能形成第一批可比样本。不做的话，分配只能是原理驱动。
2. **这一轮的目标是"扩风格"还是"稳复现"？**
   - 扩风格 → 主力投在 48 个未用风格库 / 3741 条未用词条
   - 稳复现 → 主力投在现有 7 个 LoRA 的强度-顺序矩阵，把已验证的组合固化

---

## 附：本次分析用到的工具与命令

| 用途 | 入口 |
|---|---|
| 资源清单（软件自带） | `ResourceInventory(comfy_root, []).snapshot()` → styles 3946 / loras 13 |
| 方案声明 vs 实测核对 | `tools/核对方案实测.py`（本次新增，只读） |
| 图鉴数据 | `C:\Users\wyj\Desktop\_atlas_D.json`（7026→7018 条，由 `tools/build_atlas.py` 生成） |
| 作品库 | `%LOCALAPPDATA%\ComfyBatch-S\library.db` → `works` / `runs` |
| 审图记录 | `%LOCALAPPDATA%\ComfyBatch-S\reviews\*.json` |
