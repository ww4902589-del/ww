# S / ComfyBatch V2.21 交接

## 本次交付

- 新增完全本机运行的图片提示词反推，优先 EasyUse，缺失或失败时回退 ArtVenture BLIP，
  两条路径都由 H3ShowText 把文本写入 ComfyUI 历史并回填任务。
- 新增公开网页、图片直链和 B 站视频封面提取。
- 下载链路限制为 HTTP/HTTPS；连接固定到已校验的公网 IP，TLS 仍校验原始主机名，每次重定向都重新校验，避免 DNS 重绑定访问本机或私有网段。
- 单次提取最多检查 36 个候选、发出 16 次请求、下载 64 MiB、耗时 45 秒；失败候选也计入预算。
- 直链图片使用 12 MiB 单图上限，网页仍为 1 MiB；仅接受 PNG、JPEG 和 WebP，并按实际解码格式确定扩展名。
- 图片进入既有批处理导入流程，不覆盖原文件。
- 修复重复启动体验：已有实例响应后，按正常桌面启动方式打开其浏览器页面；`--no-browser` 保持无界面。
- 默认目录改为跨机器的用户目录，可用 `COMFYBATCH_COMFY_ROOT`、`COMFYBATCH_WORKFLOW_ROOT`、`COMFYBATCH_OUTPUT_ROOT` 覆盖。
- 发布夹具仅保留三份测试工作流所需且已录制的 38 类节点，并移除了本机图片名、本地模型/LoRA 名、作者笔记、绝对路径和无关文件枚举；公开 Hugging Face 元数据仅作为节点结构依据保留。

## 验证记录

- Python 编译：通过。
- 前端词法检查：通过，属性值内危险换行为 0。
- V2.21 全量测试：442 项；441 通过，1 项因未提供外部 XLSX 夹具而跳过。
- V2.21 隔离候选 `/api/ping`：版本 `2.21`。
- V2.21 页面及 `app.js`：HTTP 200，标题与资源版本一致，反推接口已打包。
- 本机节点缓存识别：首选 `easy imageInterrogator`，回退 `BLIPCaption`，依赖无缺失。
- ComfyUI 离线时：两种后端均被依次尝试，并在约 7 秒内返回明确的本机连接错误。
- 真实本机 ComfyUI：`easy imageInterrogator` 的 `fast` 模式约 42 秒成功，
  `H3ShowText` 历史文本由候选包读取并回填，响应标记 `local_only: true`。
- 非本机监听：以 `--host 0.0.0.0` 启动会立即拒绝并以退出码 2 结束。
- 图片直链：成功提取 1 张。
- 普通网页：GitHub CPython 页面和 Pillow 文档页均成功提取 Open Graph / 页面图片。
- B 站视频：成功提取 1 张 `bilibili-cover`。
- 重复启动：第二进程正常退出，主实例 `activated_at` 前移，并执行打开已有 URL 的分支。

## 构建物

- 构建配置：`src/S.spec`
- 候选文件：`build/dist-v221-release/S.exe`（不提交到公开仓库）
- 大小：17,091,100 字节
- SHA-256：`B49F42CB4FE2C24E4509146ACDF324CAE537702EB4566B9E35EDACC849F0852B`
- Authenticode：未签名。当前没有可信代码签名证书，不生成伪签名。

## 后续

- 跟踪 Issue：<https://github.com/ww4902589-del/ww/issues/2>
- V2.20 已合并并部署。V2.21 本机提示词反推完成后，依次开展用途/分段/定位、SQLite 作品库、重做队列、通用工作流映射、审图交互、风格推荐、绘画参考与 Agent 操作层。
