"""The one JavaScript lexer this project uses, plus the checks built on it.

Why this file exists
--------------------
``tools/reformat_frontend.py`` splits the frontend script after ``{``, ``}`` and
``;`` so statements sit on their own lines. That split is only safe if the
formatter knows which characters are code and which are string. The first
version of its scanner kept a single ``in_template`` boolean that flipped to
False at the first ``${`` and never came back, so every later quote was read as
code. Two real defects came out of that:

* ``body:'{}'`` became ``body:'{<newline>}'`` -- a newline inside a single-quoted
  string is a syntax error, and it killed the entire script block while the page
  still rendered;
* ``<select id="${sel}">`` became ``id="<newline>  sel<newline>"`` -- an
  attribute value keeps whitespace verbatim, so ``getElementById("sel")`` stopped
  matching.

The whitespace-insensitive guard the formatter used could not see either one:
removing all whitespace makes ``'{<newline>}'`` and ``'{}'`` identical. And a
second scanner, written to catch the attribute class, iterated the raw file, so a
``<`` or ``"`` in *code* leaked into its tag state and it reported zero offenders
while 42 remained.

So the lexer lives here once, is exercised by the tests, and is the only thing
that decides what a character is. Everything else -- scanning, formatting,
verification -- is a view over it.

What the kinds mean
-------------------
``CODE``     real JavaScript outside any literal.
``STRING``   inside ``'...'`` or ``"..."``.
``TEMPLATE`` the *literal text* of a template literal -- the part that becomes a
             string. A newline here lands in text the user reads or in an HTML
             attribute, so no pass may split it and the checks below look here.
``EXPR``     inside a ``${...}`` interpolation, including any nested strings and
             templates' interpolations. It is code, not text, so a newline here
             is plain JavaScript whitespace. Tracking it apart from TEMPLATE is
             what stops the "break between tags" rule from firing inside
             ``'<div class="x"></div>'`` -- a string nested in a template.
``COMMENT``  inside ``//`` or ``/* */``.
``REGEX``    inside a ``/.../ `` literal.

Regex literals are the subtle case. Without them the lexer desynchronises on the
first ``/`` that opens a pattern (a quote inside a pattern starts a bogus string),
and everything after it is misread -- which is exactly how the "0 offenders"
false negative happened.
"""

from __future__ import annotations

import pathlib
import sys

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src"

CODE, STRING, TEMPLATE, COMMENT, REGEX, EXPR = (
    "code", "string", "template", "comment", "regex", "expr"
)

#: A '/' opens a regex literal when the previous code character cannot end an
#: expression. Division after an identifier or ')' is not a regex.
_REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%~^<>") | {"\n"}


def classify(code: str) -> list[str]:
    """Return a per-character kind list, so no pass has to re-parse literals."""
    kinds = [CODE] * len(code)
    index = 0
    length = len(code)
    previous = ""
    #: One entry per open template literal, holding its interpolation depth. A
    #: backtick seen while an interpolation is open starts a *nested* template.
    templates: list[int] = []

    while index < length:
        char = code[index]
        pair = code[index:index + 2]

        if templates:
            # Inside an interpolation is *not* literal text: it is code, and it may
            # contain its own strings. Marking it separately is what keeps the
            # between-tags break from firing inside `'<div class="x"></div>'`,
            # which is a single-quoted string nested in a template.
            literal = templates[-1] == 0
            kind = TEMPLATE if literal else EXPR
            kinds[index] = kind
            if char == "\\":
                if index + 1 < length:
                    kinds[index + 1] = kind
                index += 2
                continue
            if pair == "${":
                kinds[index] = EXPR
                kinds[index + 1] = EXPR
                templates[-1] += 1
                index += 2
                continue
            if char == "`":
                if templates[-1] == 0:
                    templates.pop()
                else:
                    templates.append(0)
                index += 1
                continue
            if char == "}" and templates[-1] > 0:
                kinds[index] = EXPR
                templates[-1] -= 1
            index += 1
            continue

        if pair == "//":
            end = code.find("\n", index)
            end = length if end < 0 else end
            for position in range(index, end):
                kinds[position] = COMMENT
            index = end
            continue
        if pair == "/*":
            end = code.find("*/", index + 2)
            end = length if end < 0 else end + 2
            for position in range(index, min(end, length)):
                kinds[position] = COMMENT
            index = end
            continue
        if char in "'\"":
            kinds[index] = STRING
            index += 1
            while index < length and code[index] != char:
                kinds[index] = STRING
                if code[index] == "\\" and index + 1 < length:
                    kinds[index + 1] = STRING
                    index += 1
                index += 1
            if index < length:
                kinds[index] = STRING
            index += 1
            previous = char
            continue
        if char == "/" and previous in _REGEX_PRECEDERS:
            kinds[index] = REGEX
            index += 1
            in_class = False
            while index < length:
                current = code[index]
                if current == "\\":
                    kinds[index] = REGEX
                    if index + 1 < length:
                        kinds[index + 1] = REGEX
                    index += 2
                    continue
                if current == "\n":
                    break
                kinds[index] = REGEX
                if current == "[":
                    in_class = True
                elif current == "]":
                    in_class = False
                elif current == "/" and not in_class:
                    break
                index += 1
            index += 1
            previous = "/"
            continue
        if char == "`":
            kinds[index] = TEMPLATE
            templates.append(0)
            index += 1
            continue

        if not char.isspace():
            previous = char
        index += 1

    return kinds


def tag_state(source: str, kinds: list[str]) -> list[bool]:
    """Mark offsets that sit inside a quoted HTML attribute value.

    Tracked over ``TEMPLATE`` characters only, so a ``<`` or ``"`` in code cannot
    leak in -- the defect that made the previous attempt report zero offenders.
    The state is per template literal and is reset when the outermost one closes,
    so a template that ends mid-tag cannot poison the next one. It deliberately
    persists across a ``${...}`` interpolation, because ``value="${x}"`` is
    inside the attribute on both sides of the expression.
    """
    flags = [False] * len(source)
    in_tag = False
    quote = ""
    for index in range(len(source)):
        if kinds[index] != TEMPLATE:
            continue
        char = source[index]
        if in_tag:
            if quote:
                if char == quote:
                    quote = ""
            elif char in "\"'":
                quote = char
            elif char == ">":
                in_tag = False
        elif char == "<":
            in_tag = True
        flags[index] = in_tag and bool(quote)
    return flags


def scan(source: str) -> list[tuple[int, str, bool]]:
    """Return ``(offset, context, in_attribute)`` for template-text newlines."""
    kinds = classify(source)
    flags = tag_state(source, kinds)
    found: list[tuple[int, str, bool]] = []
    line = 1
    for index in range(len(source)):
        if source[index] == "\n":
            if kinds[index] == TEMPLATE:
                context = source[max(0, index - 30):index + 30].replace("\n", "\\n")
                found.append((index, f"line {line}: ...{context}...", flags[index]))
            line += 1
    return found


def attribute_newlines(source: str) -> list[tuple[int, str, bool]]:
    """The subset of template-text newlines that sit in an attribute value."""
    return [item for item in scan(source) if item[2]]


def fix(source: str, only=None) -> str:
    """Drop the newline+indent runs that sit inside template text.

    The reformatter replaced the single space that was there with a newline plus
    an indent, so collapsing the run to one space restores what the source said.
    A run directly against a delimiter is collapsed to nothing, because that is
    where there was no space to begin with (``}`` followed by a quote).
    ``only`` narrows the pass to one report function. Offsets are collected up
    front because editing shifts every later position.
    """
    offenders = {offset for offset, _, _ in (only or scan)(source)}
    if not offenders:
        return source
    out: list[str] = []
    index = 0
    length = len(source)
    while index < length:
        if index in offenders:
            run_end = index + 1
            while run_end < length and source[run_end] in " \t":
                run_end += 1
            before = out[-1] if out else ""
            after = source[run_end] if run_end < length else ""
            wordlike = lambda c: c.isalnum() or c in "_）】、" or ord(c) > 0x2000
            out.append(" " if (wordlike(before) and wordlike(after)) else "")
            index = run_end
            continue
        out.append(source[index])
        index += 1
    return "".join(out)


def main() -> int:
    apply_fix = "--fix" in sys.argv
    name = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "app.js"
    path = SOURCE / name
    text = path.read_text(encoding="utf-8")
    text_newlines = scan(text)
    harmful = [item for item in text_newlines if item[2]]
    print(f"{name}: 模板文本内换行 {len(text_newlines)} 处，其中属性值内 {len(harmful)} 处")
    for _, context, _ in harmful[:40]:
        print(f"   {context}")
    if apply_fix and harmful:
        fixed = fix(text, only=attribute_newlines)
        remaining = attribute_newlines(fixed)
        print(f"   修复后属性值内剩余 {len(remaining)} 处")
        if remaining:
            for _, context, _ in remaining[:5]:
                print(f"   !! {context}")
            return 1
        path.write_text(fixed, encoding="utf-8")
        print(f"   已写回 {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
