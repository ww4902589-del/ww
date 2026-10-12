"""Split the single-page UI into index.html + app.css + app.js.

Step 1 of the frontend re-plan is deliberately a *mechanical* move: the content
of each block is carried over byte for byte, so the page looks and behaves
exactly as before. If anything breaks after this step, the split is the cause;
only later steps change layout. Mixing the two would make a failure impossible to
attribute.

The two inline script blocks are concatenated in their original order. They
already shared one global lexical scope, so joining them into a single file
preserves semantics -- and it also removes the arbitrary split, which had put
three unrelated additions (workbench, live sync, deleted presets) in the first
block while the review board sat in the second.

The pre-split page now lives under ``tools/legacy_page/`` rather than ``src/``:
it was the input to this one-time migration, nothing serves it any more, and a
stale copy of the UI sitting beside the real one is how a future edit ends up in
the wrong file.
"""

from __future__ import annotations

import pathlib
import re
import sys

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src"
LEGACY = pathlib.Path(__file__).resolve().parent / "legacy_page" / "comfybatch_v2.html"


def main() -> int:
    if not LEGACY.exists():
        print(f"nothing to do: {LEGACY} is already gone")
        return 0

    text = LEGACY.read_text(encoding="utf-8")

    styles = re.findall(r"<style>(.*?)</style>", text, re.S)
    scripts = re.findall(r"<script>(.*?)</script>", text, re.S)
    assert len(styles) == 2, f"expected 2 style blocks, found {len(styles)}"
    assert len(scripts) == 2, f"expected 2 script blocks, found {len(scripts)}"

    # Body markup: between <body><main> and the first <script>.
    body_start = text.index("<body>") + len("<body>")
    body_end = text.index("<script>")
    body = text[body_start:body_end].rstrip()

    title = re.search(r"<title>(.*?)</title>", text, re.S)
    head_meta = re.search(r'<meta charset="utf-8">.*?<title>.*?</title>', text, re.S)
    assert head_meta, "head metadata not found"

    index = (
        "<!doctype html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        + head_meta.group(0)
        + "\n"
        '<link rel="stylesheet" href="/app.css">\n'
        "</head>\n"
        "<body>\n"
        + body
        + "\n"
        '<script src="/app.js"></script>\n'
        "</body>\n"
        "</html>\n"
    )

    css = (
        "/* ComfyBatch 样式表。按区块分段：基础 / 卡片 / 画廊 / 风格预览 /\n"
        "   对话框 / 工作台导航 / 粘性操作栏 / 提示条 / 生效摘要\n"
        "   / 审图看板 / 参数工作台 / 已删除预设 / 抽屉 / 原图查看器。 */\n\n"
        + styles[0].strip()
        + "\n\n\n/* ---- 审图看板、参数工作台、抽屉、原图查看器 ---- */\n"
        + styles[1].strip()
        + "\n"
    )

    js = (
        "/* ComfyBatch 前端脚本。\n\n"
        "   原先分散在两个内联脚本块里，两块共享同一个全局词法作用域，\n"
        "   合并为一个文件不改变语义，只是去掉了那个与职责无关的切分。\n"
        "   按模块分段：状态与工具 / 本地环境 / 导入与任务 / 工作流 /\n"
        "   风格与 LoRA / 参数工作台 / 检查 / 运行 / 审图与确认 / 抽屉 /\n"
        "   租约与事件流。 */\n\n"
        + scripts[0].strip()
        + "\n\n\n/* ---- 审图看板、键盘审图、原图查看器 ---- */\n"
        + scripts[1].strip()
        + "\n"
    )

    (SOURCE / "index.html").write_text(index, encoding="utf-8")
    (SOURCE / "app.css").write_text(css, encoding="utf-8")
    (SOURCE / "app.js").write_text(js, encoding="utf-8")

    # Self-check: everything the tests assert on must survive the move.
    combined = index + css + js
    missing = [
        marker for marker in (
            'id="reviewBoard"', 'id="paramWorkbench"', 'id="deletedPresets"',
            'id="mappingDialog"', 'id="globalNegative"', 'id="presetPicker"',
            "function renderReview", "function loadWorkbench", "function connectEvents",
            "function refreshDeletedPresets", "/api/params/apply", "/api/review/confirm",
            "ComfyBatch V2.16", "批次已提前停止",
        )
        if marker not in combined
    ]
    if missing:
        print("FAILED self-check, these did not survive the move:")
        for marker in missing:
            print(f"   {marker}")
        return 1

    print(f"index.html  {len(index.splitlines()):5d} lines")
    print(f"app.css     {len(css.splitlines()):5d} lines")
    print(f"app.js      {len(js.splitlines()):5d} lines")
    print("self-check: all asserted markers present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
