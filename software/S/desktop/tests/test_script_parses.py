"""The page script must actually parse, and must keep its attribute values intact.

``test_page_assets`` checks delimiter balance, and that is not enough. While
reformatting the script, a newline was inserted inside the string literal
``'{}'``:

    method:'POST',body:'{
    }
    '}

Braces stayed balanced, so every test passed and the page rendered -- but the
entire script block died with *Invalid or unexpected token*, leaving a UI that
looked fine and did nothing. Only loading it in a browser revealed it.

The second defect of that pass was quieter. ``<select id="${sel}">`` became
``id="<newline>  sel<newline>"``, and an HTML attribute value keeps whitespace
verbatim, so ``getElementById("sel")`` stopped matching. The whitespace-only
comparison the formatter used could not see it: strip all whitespace and
``'{}'`` and ``'{<newline>}'`` are the same string.

Both checks here are views over ``tools/js_lexer.py`` -- the single lexer in this
project. Two earlier attempts at a private scanner each reported zero offenders
while real ones remained, because both mishandled one of the cases below:
template interpolations, nested templates, regex literals, or quotes inside a
comment. There is one lexer now, and these tests are what keep it honest.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

from fakes import js_source  # noqa: E402
from js_lexer import (  # noqa: E402
    EXPR,
    STRING,
    TEMPLATE,
    attribute_newlines,
    classify,
)


def strings_spanning_lines(code: str) -> list[tuple[int, str]]:
    """Return ``(line, context)`` for every quoted string broken by a newline.

    A single- or double-quoted JavaScript string cannot contain a raw newline, so
    one is a direct signal that a reformatter split inside a literal. Template
    literals *can* span lines, which is why this asks the lexer rather than
    counting quotes per line.
    """
    kinds = classify(code)
    offenders: list[tuple[int, str]] = []
    line = 1
    for index in range(len(code)):
        if code[index] == "\n":
            if kinds[index] == STRING:
                context = code[max(0, index - 40):index].replace("\n", " ")[-50:]
                offenders.append((line, context))
            line += 1
    return offenders


class StringLiteralTests(unittest.TestCase):
    def test_no_string_literal_spans_a_line(self):
        offenders = strings_spanning_lines(js_source())
        self.assertEqual([], offenders[:5], f"{len(offenders)} 处字符串跨行，脚本无法解析")

    def test_the_check_detects_the_real_break(self):
        """The guard must fail on the defect it exists for."""
        broken = "const a=api(1,{\n  body:'{\n  }\n  '}\n);"
        self.assertTrue(strings_spanning_lines(broken))

    def test_the_check_accepts_templates_spanning_lines(self):
        """Template literals legitimately span lines, so they are not offenders."""
        fine = "const t = `line one\nline two`;"
        self.assertEqual([], strings_spanning_lines(fine))

    def test_the_check_accepts_nested_templates(self):
        nested = "const t = `a${ok ? `b\nc` : ''}d`;\n"
        self.assertEqual([], strings_spanning_lines(nested))

    def test_the_check_ignores_apostrophes_in_comments(self):
        """An English comment with an apostrophe is not an unterminated string."""
        commented = "// the server's id\n// another page's value\nconst a=1;\n"
        self.assertEqual([], strings_spanning_lines(commented))

    def test_the_check_ignores_quotes_inside_a_regex(self):
        regex = "const esc=s=>s.replace(/[&<>\"']/g, c=>c);\n"
        self.assertEqual([], strings_spanning_lines(regex))

    def test_a_multiline_comment_with_an_apostrophe_is_not_an_offender(self):
        commented = "/*\n   the page's value\n*/\nconst a=1;\n"
        self.assertEqual([], strings_spanning_lines(commented))


class AttributeValueTests(unittest.TestCase):
    """A newline in an attribute value silently changes what the page matches."""

    def test_no_newline_inside_an_html_attribute_value(self):
        offenders = attribute_newlines(js_source())
        self.assertEqual([], [item[1] for item in offenders][:5],
                         f"{len(offenders)} 处换行落进 HTML 属性值")

    def test_the_check_detects_the_real_defect(self):
        broken = 'const t = `<select id="${\n  sel}\n">`;'
        self.assertTrue(attribute_newlines(broken))

    def test_the_check_accepts_markup_broken_between_tags(self):
        """Breaking between ``>`` and ``<`` is free: HTML collapses the text node."""
        fine = "const t = `<div>a</div>\n  <div>b</div>`;"
        self.assertEqual([], attribute_newlines(fine))

    def test_the_check_accepts_a_newline_inside_an_interpolation(self):
        """Whitespace inside ``${...}`` is JavaScript whitespace, not text."""
        fine = 'const t = `<select id="${\n  sel\n}">`;'
        self.assertEqual([], attribute_newlines(fine))

    def test_a_string_nested_in_an_interpolation_is_not_template_text(self):
        """The case that made the between-tags rule split a real string."""
        source = """const t = `a${ok ? '<b>x</b>' : '<i>y</i>'}b`;"""
        kinds = classify(source)
        self.assertEqual(EXPR, kinds[source.index("'<b>")])
        self.assertEqual(TEMPLATE, kinds[source.index("`a")])

    def test_a_nested_template_keeps_its_own_text_literal(self):
        """A template inside an interpolation still renders, so its text counts."""
        source = "const t = `${ok ? `<img src=\"x\">` : ''}`;"
        kinds = classify(source)
        self.assertEqual(TEMPLATE, kinds[source.index("<img")])


class ScriptStructureTests(unittest.TestCase):
    """Balance checks pass on code that cannot parse; these assert structure."""

    def test_every_function_declaration_closes(self):
        code = js_source()
        depth = 0
        starts = 0
        for number, line in enumerate(code.split("\n"), 1):
            if line.lstrip().startswith(("function ", "async function ")) and depth == 0:
                starts += 1
            depth += line.count("{") - line.count("}")
            if depth < 0:
                self.fail(f"第 {number} 行出现多余的右花括号")
        self.assertEqual(0, depth, "文件结束时花括号未归零")
        self.assertGreater(starts, 50, f"只找到 {starts} 个函数声明，可能被合并了")

    def test_no_statement_was_split_after_return(self):
        """A bare ``return`` on its own line silently changes behaviour (ASI)."""
        lines = js_source().split("\n")
        offenders = [
            (number + 1, lines[number].strip()[:50])
            for number in range(len(lines) - 1)
            if lines[number].strip() == "return"
        ]
        self.assertEqual([], offenders[:5], f"{len(offenders)} 处 return 被换行截断")


if __name__ == "__main__":
    unittest.main()
