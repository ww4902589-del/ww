# S / ComfyBatch V2.42

这是唯一维护源码目录。桌面目录 c 是 E 盘目录的映射，不是另一份源码。唯一正式程序是桌面的 S.exe。

运行代码位于 src/、tools/，验证代码位于 tests/。修改记录位于 changes/；历史工作副本和旧程序已压缩归档，本目录不保留散落 EXE。

完整验证：设置 ATLAS_NO_DESKTOP=1 和 NO_PROXY=127.0.0.1,localhost，执行 python -B run_tests.py。再运行 node tests/check_atlas_management_js.js 和 node tests/check_atlas_duplicates_js.js。已有回归结果为 636 项，635 通过、0 失败、1 跳过（外部 XLSX 环境）。

构建依赖见 requirements-build.txt；build.ps1 在临时目录构建，输出 ZIP 候选包。-Deploy 才会更新桌面程序；发布前应退出当前 S。

图鉴文件夹为虚拟组织，原图不移动；日期仅取可信生产记录，缺失为日期未知。同类侧区支持精确字段匹配、独立预览分页和累计批量标记。

收藏、配置和用户图片不属于源码；现用数据仍在 AppData 的 ComfyBatch-S。首次安装种子为空，升级读取现有记录。tools/_grades_system.json 是空的发布种子，原本机评级已归档且现用 AppData 记录保留。

GitHub 分为 software/S/code（本源码映射）和 software/S/changes（修改记录）。历史记录见 changes/history；代码通过本目录 Git 提交溯源，后续修改应提交并记录测试及 EXE 哈希。
