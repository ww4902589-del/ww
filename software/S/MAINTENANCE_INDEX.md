# S 桌面软件与后续维护索引

核对日期：2026-10-13（北京时间）。用户指定的维护对象为桌面的 **S.exe**。

## 当前基线与远端边界

- 桌面版本 **3.0.1**；文件大小 **18,753,116 字节**。
- 本次读取的 SHA-256：`261D325CF10B15730B2CC2E8F79EFF4263E7E36C2312BBCD01CD31ED0BC2D097`。
- 对应发布记录的构建源码提交：`636152b22549514e7b582e400a5fa7dc8a345e0c`。
- 本次索引前的本地维护提交：`030b4897151ad2c10415a7025e601851a426eae8`。本地保留一个展开的维护源码目录，目录历史名称不代表产品版本。
- GitHub main 目前仍为旧的 `software/S/src/` 布局。[PR #25](https://github.com/ww4902589-del/ww/pull/25) 提供 `code/` 与 `changes/` 分层，但尚未合并且代码为 V2.42。
- **V3.0.1 运行源码尚未上传本仓库。** 本次提交保存定位证据、当前本地文件目录/指纹及 V3.2 目标资料；不能把旧 main 或 PR #25 当作桌面现用源码。后续源码同步需从本地已确认基线进行，核对指纹并保留远端既有改动。

## 维护目录与模块

以下路径以当前本地维护源码根为基准。后续远端映射为 `software/S/code/<相对路径>`；本地 `changes/` 单独映射到 `software/S/changes/`，同步时避免重复放入 code/。

| 修改目的 | 相对源码位置 | 验证入口（tests/ 内） |
| --- | --- | --- |
| 主工作台、接口、配置 | src/comfybatch_v2_app.py、index.html、app.js、app.css | test_app_routes.py、test_page_assets.py |
| 生成、工作流、批次和重做 | src/comfybatch_v2_core.py、comfybatch_nodeschema.py、comfybatch_params.py、comfybatch_gateway.py | test_comfybatch_v2_core.py、test_compiled_graph.py、test_params.py |
| 分批与桌面生命周期 | src/comfybatch_launcher.py、comfybatch_lifecycle.py、atlas_host.py | test_launcher.py、test_ui_lifecycle.py、test_atlas_host.py |
| 内嵌图库与缓存 | tools/atlas_server.py、build_atlas.py、build_thumbs.py | test_atlas_cache.py、test_atlas_management.py |
| 图像身份与 SQLite | tools/atlas_resources.py | test_atlas_resources.py、test_atlas_sync.py |
| 导入、扫描与来源 | tools/atlas_library.py、atlas_sources.py | test_v3_library.py、test_v3_library_routes.py |
| 目录、日期、详情、同类 | tools/atlas_management.py、atlas_management.js | test_v3_production.py、check_v3_folder_js.js、check_v3_detail_js.js |
| 淘汰、审批与最终派发 | src/comfybatch_style_governance.py + app/core 调用点 | test_v3_governance.py、test_v3_guard_routes.py |
| 能力与构建 | src/capabilities.json、src/S.spec、build.ps1、requirements-build.txt | 能力、版本、发布哈希核对 |

`tools/` 包含正式运行代码，不能整体视为开发临时目录。配置、收藏、图片、数据库及本机绝对路径不属于公开源码。

本地 `INDEX.md` 提供完整目录和链接，`AGENTS.md` 提供后续维护入口，`tools/maintenance_index.py` 可重建和校验索引。公开的 [source-index.json](changes/maintenance/source-index.json) 保存当前本地 143 个维护文件的相对路径、SHA-256、大小和 Python 顶层符号。它是本地现场目录，不表示这些文件均已在本远端分支存在。

## 后续工作与本次验证

目标方案按用户提供文件原样保存：[图片风格工作台 V3.2](changes/plans/style-workbench-v3.2.md)，SHA-256 `5AB4661F0A62AC6864465DAAB462AE160CB68014740022CB37AEFDF68E299A82`。目标版本与现用软件版本分开记录。下一步先做源码—目标差距映射，保留已有图库与治理能力，再处理项目/实验/轮次、运行快照、评价和 AI 精确提案。

本次对当前本地源码执行 V3 Python 回归 **44 项全部通过**，两组目录/详情 JavaScript 行为检查通过；索引内容指纹与文档链接另行核对。没有重新打包或替换桌面程序，没有迁移真实用户数据，也没有执行真实 GPU 生图或重做全套 P0 备份恢复验收。GitHub 索引提交是维护资料，不是发布放行。

维护后更新本地索引、修改记录和运行验证；正式发布通过既有构建与回退流程，保存新的源代码提交和程序指纹。
