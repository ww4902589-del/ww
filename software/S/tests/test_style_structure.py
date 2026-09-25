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
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import css_source  # noqa: E402

#: Rules that must be declared at the top level. Each one is the only styling for a
#: verdict the page shows the user, so nesting it inside another block turns a designed
#: signal into an unstyled paragraph. They are named here rather than derived so that a
#: future merge cannot quietly move them and still pass.
TOP_LEVEL_RULES = (
    ".badge.seed-unchecked",
    ".seed-note",
    ".seed-note.warn",
    ".help.purpose-unsupported",
)


def scan_blocks(text: str) -> tuple[list[tuple[int, str]], int, list[int]]:
    """Walk the sheet once.

    Returns every block that opens at depth 0 as ``(line, selector)`` (or ``@media ...``),
    the depth left open at end of file, and the lines where a closing brace had nothing to
    close. Comments and quoted strings are skipped, because a brace inside either is not a
    brace.
    """
    blocks: list[tuple[int, str]] = []
    negatives: list[int] = []
    depth = 0
    line = 1
    selector_start_line = 1
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
            if depth == 0:
                blocks.append((selector_start_line, label))
            depth += 1
            pending = []
            selector_start_line = line
        elif ch == "}":
            depth -= 1
            if depth < 0:
                negatives.append(line)
                depth = 0
            pending = []
            selector_start_line = line
        elif ch == ";":
            pending = []
            selector_start_line = line
        else:
            pending.append(ch)
        i += 1
    return blocks, depth, negatives


class StylesheetStructureTests(unittest.TestCase):
    def test_braces_balance_and_never_underflow(self):
        blocks, depth, negatives = scan_blocks(css_source())
        self.assertEqual(
            [], negatives,
            "样式表里出现了多余的右花括号：它会把后续所有规则挤到错误的层级",
        )
        self.assertEqual(
            0, depth,
            "样式表在文件结束时仍有未闭合的块：其后追加的任何规则都会被吞进这个块，"
            "而且该块自身的规则在层叠中的位置也不再是作者写下的位置",
        )
        self.assertGreater(len(blocks), 50, "扫描器没有真正读到样式块")

    def test_verdict_rules_are_top_level(self):
        """Nesting a verdict rule inside ``@media`` counts as not top level.

        The scanner only records blocks that open at depth 0, so a rule sitting inside a
        media query is simply absent from ``blocks`` and the assertion below fails -- which
        is the point: those four rules are the only styling for a verdict the page shows,
        and a media query that happens to be left open would scope them to one viewport.
        """
        blocks, _, _ = scan_blocks(css_source())
        selectors = [selector for _, selector in blocks]
        for wanted in TOP_LEVEL_RULES:
            matches = [s for s in selectors if s == wanted]
            self.assertEqual(
                1, len(matches),
                f"{wanted} 必须在样式表顶层且只声明一次，实际找到 {len(matches)} 次",
            )


if __name__ == "__main__":
    unittest.main()
