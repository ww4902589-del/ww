"""Reformat app.js and app.css so statements sit on their own lines.

The minified single-line form is what produced a real shipped defect: one extra
brace while wiring the review board killed the entire script block, leaving a page
that rendered but did nothing. It also made my patch scripts mis-target several
times, because a 4000-character line offers no reliable anchor for a text match.

Rules that keep this safe:

* a single scanner classifies every character as code, string, template, comment
  or regex, so braces inside literals never affect splitting or indentation;
* a whole template literal -- text *and* its ``${...}`` interpolations -- is
  classified as template, so no newline is ever placed inside a string the user
  can read, and the braces of ``${}`` never count toward indentation;
* splitting happens at ``{``, ``}`` and ``;`` at **any** brace depth as long as
  parentheses are closed, so function bodies get one statement per line too --
  only ``for(;;)``-style headers stay intact;
* the result is only accepted if it is **whitespace-identical** to the input, so
  this can never change behaviour -- an earlier attempt at CSS reformatting was
  caught exactly this way;
* a second guard refuses output that puts a newline inside an HTML attribute
  value, because an attribute keeps whitespace verbatim (``id="${sel}"`` gaining
  a newline makes ``getElementById`` miss). Whitespace-only comparison cannot see
  that class, which is how it once shipped.

Indentation is computed as the brace balance of the preceding lines (minus one
when the line opens with ``}``). That is exact, unlike the incremental counter a
first attempt used, which mishandled ``} else {`` and indented the file further on
every run.
"""

from __future__ import annotations

import pathlib
import re
import sys

from js_lexer import CODE, TEMPLATE, attribute_newlines, classify, tag_state

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "src"


def split_statements(code: str, kinds: list[str]) -> str:
    """Break lines after every code ``{``, ``}`` and ``;`` with closed parens.

    Paren depth is what keeps a ``for(let i=0;i<n;i++){`` header in one piece:
    the ``;`` inside it are not break points.

    Declarator commas are deliberately not break points. Splitting on them needs
    to know whether the statement started with ``var``/``let``/``const``, and
    that question is answered from the line the comma currently sits on -- which
    the previous pass changed, so the formatter was not idempotent and refused
    itself. Break points that depend only on structure stay stable.
    """
    out: list[str] = []
    parens = 0
    for index, char in enumerate(code):
        kind = kinds[index]
        if kind != CODE:
            # Template, string, comment and regex characters are never split on:
            # a newline there lands inside what the user sees.
            out.append(char)
            continue
        if char == "(":
            parens += 1
            out.append(char)
            continue
        if char == ")":
            parens = max(0, parens - 1)
            out.append(char)
            continue
        if char not in "{};":
            out.append(char)
            continue

        out.append(char)
        if parens:
            continue
        # Only break when the next code character does not already start a line.
        rest = index + 1
        while rest < len(code) and code[rest] in " \t\r":
            rest += 1
        if rest < len(code) and code[rest] == "\n":
            continue
        out.append("\n")
    return "".join(out)


def split_template_markup(code: str) -> str:
    """Break template markup between tags, and only there.

    A template literal that builds an HTML card is one very long line, and the
    splitter above will not touch it -- correctly, because a newline anywhere
    inside can land in an attribute value or in the text of a string. There is
    exactly one position inside such a literal where a newline is free: between a
    closing ``>`` and the next opening ``<``. No text node exists there, so HTML
    renders the same, and it cannot be inside an attribute.

    The position must not already hold a newline, or a second pass would add
    another one and the formatter would refuse itself as unstable.
    """
    kinds = classify(code)
    flags = tag_state(code, kinds)
    out: list[str] = []
    index = 0
    length = len(code)
    while index < length:
        char = code[index]
        out.append(char)
        if kinds[index] == TEMPLATE and char == ">" and not flags[index]:
            rest = index + 1
            newline_seen = False
            while rest < length and code[rest] in " \t\r\n":
                newline_seen = newline_seen or code[rest] == "\n"
                rest += 1
            if not newline_seen and rest < length and code[rest] == "<" and kinds[rest] == TEMPLATE:
                out.append("\n")
        index += 1
    return "".join(out)


def brace_balance(line: str, kinds_line: list[str]) -> int:
    """Net code-brace count for one line.

    ``line`` and ``kinds_line`` must be the same length: an earlier version
    filtered the kind list before zipping, which shifted every position and made
    the balance come out as zero -- the formatting ran but indented nothing.
    """
    total = 0
    for char, kind in zip(line, kinds_line):
        if kind == CODE and char in "{}":
            total += 1 if char == "{" else -1
    return total


def indent_lines(code: str) -> str:
    """Indent by the brace balance of preceding lines -- exact, and idempotent."""
    kinds = classify(code)
    lines = code.split("\n")
    kinds_lines: list[list[str]] = []
    offset = 0
    for line in lines:
        kinds_lines.append(kinds[offset:offset + len(line)])
        offset += len(line) + 1

    result: list[str] = []
    balance = 0
    for line, kinds_line in zip(lines, kinds_lines):
        stripped = line.strip()
        if not stripped:
            if result and result[-1] != "":
                result.append("")
            continue
        level = balance - (1 if stripped.startswith("}") else 0)
        result.append("  " * max(0, level) + stripped)
        balance += brace_balance(line, kinds_line)
    return "\n".join(result)


def format_js(source: str) -> str:
    kinds = classify(source)
    split = split_statements(source, kinds)
    # Classified again because the first split moved characters to new lines; the
    # kinds themselves are unaffected (the regex precedent ignores whitespace).
    return indent_lines(split_template_markup(split))


def format_css(source: str) -> str:
    """Selector then one declaration per line, with a two-space indent."""
    out: list[str] = []
    depth = 0
    buffer: list[str] = []
    index = 0
    length = len(source)

    def flush(terminator: str) -> None:
        text = "".join(buffer).strip()
        buffer.clear()
        if not text:
            return
        # Never invent a character: the last declaration before ``}`` may have no
        # trailing semicolon, and adding one would make the content check fail
        # (which is how this was caught).
        suffix = " {" if terminator == "{" else (";" if terminator == ";" else "")
        out.append("  " * depth + text + suffix)

    while index < length:
        char = source[index]
        if source[index:index + 2] == "/*":
            end = source.find("*/", index + 2)
            end = length if end < 0 else end + 2
            comment = source[index:end]
            flush(";")
            for piece in comment.split("\n"):
                out.append("  " * depth + piece.strip())
            index = end
            continue
        if char == "{":
            flush("{")
            depth += 1
        elif char == "}":
            # The final declaration before a closing brace may legitimately lack a
            # semicolon; pass an empty terminator so none is invented.
            flush("")
            depth = max(0, depth - 1)
            out.append("  " * depth + "}")
        elif char == ";":
            flush(";")
        else:
            buffer.append(char)
        index += 1
    flush(";")

    cleaned: list[str] = []
    for line in out:
        if not line.strip() and cleaned and not cleaned[-1].strip():
            continue
        cleaned.append(line.rstrip())
    return "\n".join(cleaned).strip("\n")


FORMATTERS = {"app.js": format_js, "app.css": format_css}


def main() -> int:
    for name, formatter in FORMATTERS.items():
        path = SOURCE / name
        original = path.read_text(encoding="utf-8")
        formatted = formatter(original)

        squashed_before = re.sub(r"\s+", "", original)
        squashed_after = re.sub(r"\s+", "", formatted)
        if squashed_before != squashed_after:
            print(f"{name}: REFUSED -- content changed beyond whitespace")
            for position, (before, after) in enumerate(zip(squashed_before, squashed_after)):
                if before != after:
                    print(f"   first difference at {position}: {before!r} -> {after!r}")
                    print(f"   context: ...{squashed_before[max(0, position - 70):position + 70]!r}")
                    break
            return 1

        # The whitespace-insensitive check above cannot see a newline placed
        # inside a string or an attribute value, so that class is checked on its
        # own terms -- it is the one that once killed the script block.
        before_attrs = len(attribute_newlines(original))
        after_attrs = attribute_newlines(formatted)
        if after_attrs:
            print(f"{name}: REFUSED -- {len(after_attrs)} 处换行落进 HTML 属性值"
                  f"（原本 {before_attrs} 处）")
            for _, context, _ in after_attrs[:3]:
                print(f"   {context}")
            return 1

        # Idempotence: a second pass must be a no-op, or indentation is drifting.
        if formatter(formatted) != formatted:
            print(f"{name}: REFUSED -- formatting is not stable, it would drift on every run")
            return 1

        # ``format_js`` can end its output with a newline already; appending
        # another left a blank last line, and the counts in the message below
        # then disagreed with the file on disk (1817 lines reported as 1816).
        body = formatted.rstrip("\n")
        before_lines = len(original.rstrip("\n").splitlines())
        path.write_text(body + "\n", encoding="utf-8")
        print(f"{name}: {before_lines} -> {len(body.splitlines())} lines "
              f"(whitespace only, stable)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
