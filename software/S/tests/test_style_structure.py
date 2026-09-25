"""Structural integrity of the stylesheet.

``test_page_assets.py`` pins CSS *content* -- which selectors and declarations exist --
but nothing pinned the *shape* of the sheet. Brace balance is exactly the damage a merge
does silently: two branches each append a block at the end of the file, the closing brace
they happen to share gets emitted once instead of twice, and from then on every rule
appended after the mistake is nested inside whichever block was left open. The rules still
exist, so every content assertion still passes while the styles themselves stop applying.

That is not hypothetical. Integrating the seed read-back branch and then the
purpose-preflight branch left ``@media (max-width:900px)`` open -- which scoped the seed
badges and notes to narrow viewports only -- and left ``.seed-note.warn`` open, which
swallowed ``.help.purpose-unsupported`` so the "this workflow cannot do what you asked"
box never rendered at all. Both were caught by review, not by the suite, because the
suite had no assertion that could fail. Hence this file.
"""

from __future__ import annotations

import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import css_source  # noqa: E402

#: Rules that must be declared at the top level, exactly once. Each one is the only styling
#: for a verdict the page shows the user, so nesting it inside another block turns a
#: designed signal into an unstyled paragraph. They are named here rather than derived so
#: that a future merge cannot quietly move them and still pass.
TOP_LEVEL_RULES = (
    ".badge.seed-unchecked",
    ".seed-note",
    ".seed-note.warn",
    ".help.purpose-unsupported",
)


def scan_blocks(text: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]], int, list[int]]:
    """Walk the sheet once.

    Returns ``(top_level, all_blocks, depth_left_open, lines_with_stray_close)``. A block is
    recorded as ``(line_of_selector, selector_text)``; ``line`` is where the selector's
    first non-blank character sits, and ``selector_text`` has its internal whitespace
    collapsed so a multi-line selector still compares equal to its written form.
    ``all_blocks`` includes blocks nested inside others, so a rule that exists both at the
    top level and inside a media query is visible as two entries.

    Comments and quoted strings are skipped, because a brace inside either is not a brace.
    """
    top_level: list[tuple[int, str]] = []
    all_blocks: list[tuple[int, str]] = []
    stray_close: list[int] = []
    depth = 0
    line = 1
    selector_line = 1
    pending: list[str] = []
    in_comment = False
    quote: str | None = None
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\n":
            line += 1
        if in_comment:
            if text.startswith("*/", i):
                in_comment = False
                i += 1
            i += 1
            continue
        if quote is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if text.startswith("/*", i):
            in_comment = True
            i += 2
            continue
        if ch in "\"'":
            quote = ch
            i += 1
            continue
        if ch == "{":
            label = " ".join("".join(pending).split())
            all_blocks.append((selector_line, label))
            if depth == 0:
                top_level.append((selector_line, label))
            depth += 1
            pending = []
            selector_line = line
        elif ch == "}":
            depth -= 1
            if depth < 0:
                stray_close.append(line)
                depth = 0
            pending = []
            selector_line = line
        elif ch == ";":
            pending = []
            selector_line = line
        else:
            # the selector starts at its first non-blank character, not at the newline or
            # brace that happens to precede it
            if ch not in " \t\r" and not "".join(pending).strip():
                selector_line = line
            pending.append(ch)
        i += 1
    return top_level, all_blocks, depth, stray_close


class StylesheetStructureTests(unittest.TestCase):
    def test_braces_balance_and_never_underflow(self):
        top_level, _, depth, stray_close = scan_blocks(css_source())
        self.assertEqual(
            [], stray_close,
            "样式表里出现了多余的右花括号：它会把后续所有规则挤到错误的层级",
        )
        self.assertEqual(
            0, depth,
            "样式表在文件结束时仍有未闭合的块：其后追加的任何规则都会被吞进这个块，"
            "而且该块自身的规则在层叠中的位置也不再是作者写下的位置",
        )
        self.assertGreater(len(top_level), 50, "扫描器没有真正读到样式块")

    def test_verdict_rules_are_top_level_and_declared_once(self):
        """A rule nested inside ``@media`` is not top level; a second copy is not allowed.

        Both failure shapes matter. ``@media`` left open by a merge scoped these rules to one
        viewport; a duplicate left behind by a resolution that kept both sides would let the
        later copy win. The "declared once" half counts *every* occurrence at every depth, so
        a nested duplicate is caught too.
        """
        top_level, all_blocks, _, _ = scan_blocks(css_source())
        top_selectors = [selector for _, selector in top_level]
        every_selector = [selector for _, selector in all_blocks]
        for wanted in TOP_LEVEL_RULES:
            at_top = [s for s in top_selectors if s == wanted]
            anywhere = [s for s in every_selector if s == wanted]
            self.assertEqual(
                1, len(at_top),
                f"{wanted} 必须在样式表顶层且只声明一次，顶层实际找到 {len(at_top)} 次",
            )
            self.assertEqual(
                1, len(anywhere),
                f"{wanted} 在整份样式表里出现了 {len(anywhere)} 次（含嵌套）："
                "集成留下的重复声明会让后一份生效，而作者只写了其中一份",
            )
            self.assertEqual(
                1, len(re.findall(re.escape(wanted) + r"\s*[,{]", css_source())),
                f"{wanted} 的声明次数与文本出现次数不一致",
            )


if __name__ == "__main__":
    unittest.main()
