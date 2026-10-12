"""Static integrity checks for the single-page UI.

The UI is written as a handful of very long lines, so a stray brace is easy to
introduce and invisible in review -- and it silently kills the *entire* script
block, leaving a page that renders but does nothing. That is exactly what
happened while wiring the review board, so it is pinned here.

``scan_delimiters`` is a small JS-aware scanner rather than a naive
``str.count``: it tracks string, template, comment and regex-literal state so
delimiters inside literals are not miscounted. A naive count happens to work on
this file, but it would silently pass a file that used a brace inside a string.
"""

from __future__ import annotations

import pathlib
import re
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fakes import css_source, html_source, js_source, page_source  # noqa: E402


def script_blocks() -> list[str]:
    """The page's behaviour, as one block.

    It used to be two inline blocks whose split followed no concern at all --
    the workbench, live sync and deleted-preset code ended up in the first while
    the review board sat in the second. It is now a single ``app.js``, and the
    delimiter check below runs over it.
    """
    return [js_source()]


#: A '/' begins a regex literal when the previous significant character cannot
#: end an expression. This mirrors how the JS lexer disambiguates.
_REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%~^<>") | {"\n"}


def scan_delimiters(code: str) -> dict[str, int]:
    """Return delimiter counts for ``code``, ignoring anything inside a literal.

    Handles the three cases a naive count gets wrong in this file:

    * delimiters inside strings, templates and comments;
    * a ``}`` that closes a nested object inside ``${...}`` must not be mistaken
      for the brace that ends the interpolation, so braces are tracked on a
      stack that also records interpolation markers;
    * a ``/`` inside a regex character class is literal, so ``[abc/]`` does not
      end the regex early.
    """
    counts = {"{": 0, "}": 0, "(": 0, ")": 0, "[": 0, "]": 0}
    index = 0
    length = len(code)
    # Stack entries: "code" for a plain block brace, "interp" for a `${`.
    braces: list[str] = []
    in_template = False
    previous = ""

    while index < length:
        char = code[index]
        pair = code[index:index + 2]

        if not in_template:
            if pair == "//":
                while index < length and code[index] != "\n":
                    index += 1
                continue
            if pair == "/*":
                end = code.find("*/", index + 2)
                index = length if end < 0 else end + 2
                continue
            if char == "'" or char == '"':
                quote = char
                index += 1
                while index < length and code[index] != quote:
                    index += 2 if code[index] == "\\" else 1
                index += 1
                previous = quote
                continue
            if char == "/" and previous in _REGEX_PRECEDERS:
                index += 1
                in_class = False
                while index < length:
                    current = code[index]
                    if current == "\\":
                        index += 2
                        continue
                    if current == "\n":
                        break
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
                in_template = True
                index += 1
                continue
            if pair == "${":
                counts["{"] += 1
                braces.append("interp")
                index += 2
                continue
            if char == "{":
                counts["{"] += 1
                braces.append("code")
                index += 1
                previous = char
                continue
            if char == "}":
                counts["}"] += 1
                kind = braces.pop() if braces else None
                if kind == "interp":
                    in_template = True
                    index += 1
                    continue
                index += 1
                previous = char
                continue
            if char in counts:
                counts[char] += 1
            if not char.isspace():
                previous = char
            index += 1
            continue

        # Inside a template literal: only the closing backtick and `${` matter.
        if char == "\\":
            index += 2
            continue
        if char == "`":
            in_template = False
            index += 1
            continue
        if char == "$" and code[index + 1:index + 2] == "{":
            in_template = False
            continue
        index += 1

    return counts


def strip_comments(code: str) -> str:
    """Return ``code`` with ``//`` and ``/* */`` comments removed.

    An assertion about *what the code calls* must not be tripped by a comment
    that quotes the very thing it is explaining away. The atlas probe fixes
    have exactly that shape: the prose says "never use
    ``fetch(..., {cache:'no-store'})``" right next to the code that no longer
    does, so a plain ``assertNotIn`` over the raw file can never pass.

    Strings, template literals and regex literals are copied through
    untouched; the ``/`` case reuses :data:`_REGEX_PRECEDERS` so a regex is
    not mistaken for the start of a comment.

    ``${...}`` inside a template needs the same care as in
    :func:`scan_delimiters`: a brace stack marks the interpolation boundary, so
    the ``}`` that ends it flips the scanner *back* into the template rather
    than being read as an ordinary closing brace. Without that bookkeeping the
    state drifts exactly one level after the first interpolation and every
    later comment in the file silently survives. Templates here nest (a
    ``${`` whose expression contains another backtick), which is why this is
    not optional.
    """
    out: list[str] = []
    index = 0
    length = len(code)
    in_template = False
    previous = ""
    braces: list[str] = []

    while index < length:
        char = code[index]
        pair = code[index:index + 2]

        if not in_template:
            if pair == "//":
                while index < length and code[index] != "\n":
                    index += 1
                continue
            if pair == "/*":
                end = code.find("*/", index + 2)
                index = length if end < 0 else end + 2
                continue
            if char == "'" or char == '"':
                quote = char
                out.append(char)
                index += 1
                while index < length and code[index] != quote:
                    if code[index] == "\\":
                        out.append(code[index:index + 2])
                        index += 2
                        continue
                    out.append(code[index])
                    index += 1
                out.append(quote)
                index += 1
                previous = quote
                continue
            if char == "/" and previous in _REGEX_PRECEDERS:
                start = index
                index += 1
                in_class = False
                while index < length:
                    current = code[index]
                    if current == "\\":
                        index += 2
                        continue
                    if current == "\n":
                        break
                    if current == "[":
                        in_class = True
                    elif current == "]":
                        in_class = False
                    elif current == "/" and not in_class:
                        index += 1
                        break
                    index += 1
                out.append(code[start:index])
                previous = "/"
                continue
            if pair == "${":
                # 模板分支只翻转状态，把 ${ 交给这里登记，两边才有一致的账本。
                braces.append("interp")
                out.append(pair)
                index += 2
                continue
            if char == "{":
                braces.append("code")
                out.append(char)
                previous = char
                index += 1
                continue
            if char == "}":
                kind = braces.pop() if braces else None
                out.append(char)
                index += 1
                if kind == "interp":
                    in_template = True
                else:
                    previous = char
                continue
            out.append(char)
            if char == "`":
                in_template = True
            if not char.isspace():
                previous = char
            index += 1
            continue

        if char == "\\":
            out.append(code[index:index + 2])
            index += 2
            continue
        if char == "`":
            out.append(char)
            in_template = False
            index += 1
            continue
        if char == "$" and code[index + 1:index + 2] == "{":
            # 不写出、不推进：交给代码分支登记这个 ${ 并把它写出来，
            # 否则 $ 会被这里和那里各写一次。
            in_template = False
            continue
        out.append(char)
        index += 1

    return "".join(out)


class ScriptIntegrityTests(unittest.TestCase):
    def test_the_page_behaviour_is_one_file(self):
        """One script file, so there is one place behaviour can live."""
        self.assertEqual(1, len(script_blocks()))
        self.assertIn("function renderReview", js_source())

    def test_the_page_has_no_inline_style_or_script(self):
        """Structure only: no <style> and no inline <script> in index.html.

        样式与脚本都以外链引入，允许带 ?v= 缓存戳（发版时递增，
        避免浏览器拿旧缓存导致「改了没生效」）。
        """
        html = html_source()
        self.assertNotIn("<style>", html)
        self.assertNotIn("<script>", html)
        self.assertRegex(html, r'<link rel="stylesheet" href="/app\.css(\?v=\d+)?">')
        self.assertRegex(html, r'<script src="/app\.js(\?v=\d+)?"></script>')

    def test_each_script_block_has_balanced_delimiters(self):
        for index, block in enumerate(script_blocks(), 1):
            counts = scan_delimiters(block)
            with self.subTest(block=index):
                self.assertEqual(counts["{"], counts["}"],
                                 f"脚本块 {index} 花括号不平衡，整块脚本会无法执行")
                self.assertEqual(counts["("], counts[")"], f"脚本块 {index} 小括号不平衡")
                self.assertEqual(counts["["], counts["]"], f"脚本块 {index} 方括号不平衡")

    def test_scanner_rejects_a_real_imbalance(self):
        """The scanner must actually detect the defect it guards against."""
        broken = "function poll(){try{f()}catch(e){g()}}}"
        counts = scan_delimiters(broken)
        self.assertEqual(counts["{"], counts["}"] - 1)

    def test_scanner_ignores_delimiters_inside_literals(self):
        sample = "const a={b:'{'} ;const c=`x${ {d:1} }y`;const re=/[{}]/;"
        counts = scan_delimiters(sample)
        self.assertEqual(counts["{"], counts["}"])

    def test_no_stray_backtick_reverses_a_template_literal(self):
        for index, block in enumerate(script_blocks(), 1):
            with self.subTest(block=index):
                self.assertEqual(0, block.count("`") % 2, f"脚本块 {index} 反引号数量为奇数")


class BootOrderTests(unittest.TestCase):
    """启动序列必须排在所有顶层声明之后，否则整份脚本会当场中断。

    真实故障（用户看到的是「风格图鉴长时间加载」；核对过 V2.28 打包进 exe 的
    app.js，结构完全相同，所以不是某一版新引入的）：

    ``app.js`` 顶层第 1410 行就执行 ``goModule(localStorage.getItem(...))``。
    当 localStorage 里存的模块是 ``atlas`` 时，它转到 ``ensureAtlas()``，而
    ``ensureAtlas()`` 第一步 ``_atlasStopWaiting()`` 读的是顶层
    ``let _atlasRetryTimer`` —— 那个声明在一千行之后，求值还没执行到，
    于是读到的是**暂时性死区（TDZ）**：

        ReferenceError: Cannot access '_atlasRetryTimer' before initialization

    要命的是这是**顶层语句**：异常会中断整份脚本的求值，后面的一切都不再执行。
    于是故障现象不是"慢"，而是：

    * 门控停在 index.html 里的初始文案「正在连接图鉴服务…」永远不动；
    * 真正的探测请求一个都没发出（服务端日志里连 ``GET /api/version`` 都没有）；
    * 轮询、资源扫描、模块标签、审图按钮全部没绑上，页面看着渲染出来了其实是死的；
    * 每次重载都一样，因为 localStorage 里那格还是 ``atlas``。

    修法是把启动序列整体移到文件末尾（声明都已就绪）。这条规则不适合靠人记，
    所以在这里钉住：启动语句之后不允许再出现顶层声明。
    """

    #: 启动序列的入口语句。它一执行就会调到读顶层声明的函数。
    BOOT = "goModule(localStorage.getItem('comfybatch-module')||'gen')"

    #: 顶层声明的写法（本文件里顶层声明都顶格写）。
    _DECL = re.compile(r"^(?:let|const|var|class)\s")

    def _stripped_lines(self):
        """剥掉注释后按行切开。

        必须先剥注释：注释里出现 ``let x = 1`` 这种示例文字会很自然地骗过
        位置判断。剥注释不改变相对顺序，正好够用。
        """
        return strip_comments(js_source()).splitlines()

    def test_boot_statement_appears_exactly_once(self):
        lines = self._stripped_lines()
        found = [i + 1 for i, line in enumerate(lines) if self.BOOT in line]
        self.assertEqual(1, len(found), f"启动语句应当恰好一次，实际在 {found}")

    def test_no_top_level_declaration_after_the_boot_sequence(self):
        lines = self._stripped_lines()
        boot = next(i for i, line in enumerate(lines) if self.BOOT in line)
        late = [line.strip()[:70]
                for i, line in enumerate(lines) if i > boot and self._DECL.match(line)]
        self.assertEqual(
            [], late,
            "启动语句之后还有顶层声明 —— 启动路径读到它就是 TDZ，整份脚本会中断；"
            f"把启动序列挪到文件最后：{late}")

    def test_boot_sequence_is_the_tail_of_the_file(self):
        """再钉一道末尾边界：启动块之后不该再挂别的顶层调用。"""
        lines = self._stripped_lines()
        boot = next(i for i, line in enumerate(lines) if self.BOOT in line)
        tail = [line for line in lines[boot:] if line.strip()]
        self.assertLessEqual(
            len(tail), 8,
            f"启动块之后的顶层语句过多，说明又有代码挂到了它后面：{tail[8:]}")


class ReviewUiMarkerTests(unittest.TestCase):
    """The containers and functions the review flow depends on must exist."""

    def test_review_containers_exist(self):
        html = html_source()
        for marker in ('id="reviewBoard"', 'id="abortBanner"', 'id="reviewSummary"',
                       'id="drawer"', 'id="drawerBody"', 'id="viewer"', 'id="viewerImage"',
                       'class="review-toolbar"'):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)

    def test_review_functions_are_defined(self):
        html = page_source()
        for name in ("renderReview", "cardHtml", "issuesHtml", "openDrawer", "closeDrawer",
                     "confirmAllPending", "reloadSchema", "openViewer", "reviewConfirm",
                     "reviewReject", "reviewNote", "setActive"):
            with self.subTest(function=name):
                self.assertRegex(html, rf"function {name}\s*\(")

    def test_review_endpoints_are_referenced(self):
        """The endpoints the page itself drives.

        ``/api/results`` is deliberately absent: the page reads review state from
        ``/api/status`` (which embeds it) and posts to the review endpoints.
        ``/api/results`` exists for external callers and tests.
        """
        html = page_source()
        for endpoint in ("/api/review/confirm", "/api/review/redo", "/api/status",
                         "/api/inspect", "/api/reload-schema"):
            with self.subTest(endpoint=endpoint):
                self.assertIn(endpoint, html)

    def test_keyboard_triage_bindings_exist(self):
        html = page_source()
        self.assertIn("keydown", html)
        for key in ("'y'", "'n'", "'j'", "'k'"):
            with self.subTest(key=key):
                self.assertIn(key, html)

    def test_batch_abort_reason_is_surfaced(self):
        """The banner is how a user learns the batch stopped early."""
        html = page_source()
        self.assertIn("aborted_reason", html)
        self.assertIn("批次已提前停止", html)

    def test_each_result_card_shows_the_required_fields(self):
        """The plan lists the fields each result must display."""
        html = page_source()
        for field in ("review_status", "generation", "quality", "copied_to", "attempts"):
            with self.subTest(field=field):
                self.assertIn(field, html)
        # Upscale factor / dimensions and the actual branch.
        self.assertIn("dimensions", html)
        self.assertIn("workflow_variant", html)

    def test_version_marker_is_bumped(self):
        html = html_source()
        match = re.search(r"ComfyBatch V(\d+\.\d+)", html)
        self.assertIsNotNone(match, "页面必须带版本标识")
        self.assertNotIn("ComfyBatch V2.13", html, "旧版本标识必须清掉")


class ReviewUsabilityTests(unittest.TestCase):
    """打回重做 / 记录问题 必须用界面控件，不能再让用户手打枚举值。

    原来的实现弹两次 ``prompt()``：先输原因，再让人把 ``same_seed`` 这类英文
    枚举值原样敲进去，敲错只回一句"不支持的重做方式"。这里钉住新的做法。
    """

    def test_reject_and_note_use_real_dialogs(self):
        html = html_source()
        for marker in ('id="rejectDialog"', 'id="rejectMode"', 'id="rejectNote"',
                       'id="noteDialog"', 'id="noteText"'):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)

    def test_redo_mode_is_a_select_not_free_text(self):
        html = html_source()
        block = html.split('id="rejectDialog"', 1)[1].split("</dialog>", 1)[0]
        self.assertIn("<select", block, "重做方式必须是下拉选择")
        self.assertIn("<textarea", block)

    def test_review_never_calls_the_blocking_prompt(self):
        """``prompt()`` blocks the page and cannot offer choices."""
        js = js_source()
        self.assertNotIn("prompt('打回原因", js)
        self.assertNotIn("prompt('重做方式", js)
        self.assertNotIn("prompt('问题记录", js)

    def test_every_redo_mode_has_a_plain_language_label(self):
        """Every server-side mode must be explained, or the dropdown shows raw codes."""
        from comfybatch_v2_core import REDO_MODES

        js = js_source()
        for mode in REDO_MODES:
            with self.subTest(mode=mode):
                self.assertIn(f"{mode}:", js.replace(" ", ""),
                              f"重做方式 {mode} 缺少中文说明")

    def test_dialog_functions_are_defined(self):
        js = js_source()
        for name in ("openRejectDialog", "submitReject", "openNoteDialog",
                     "submitNote", "updateRejectHint", "redoModeLabel"):
            with self.subTest(function=name):
                self.assertRegex(js, rf"function {name}\s*\(")

    def test_grid_uses_the_preview_endpoint_not_the_full_image(self):
        """The board must not ship full-resolution PNGs for every thumbnail."""
        html = page_source()
        self.assertIn("/api/preview", html)
        # ``/api/image`` stays for the viewer, but no grid may point at it.
        for marker in ('id="gallery"',):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)


class ParameterWorkbenchUiTests(unittest.TestCase):
    """The workbench is generated from the server registry, not hard-coded."""

    def test_workbench_container_exists(self):
        html = html_source()
        for marker in ('id="paramWorkbench"', 'id="paramGroups"', 'id="paramScope"',
                       'id="applyParams"', 'id="paramFeedback"', 'id="paramScopeHint"'):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)

    def test_workbench_functions_are_defined(self):
        html = page_source()
        for name in ("loadWorkbench", "renderWorkbench", "fieldHtml", "collectWorkbench",
                     "applyWorkbench", "clearWorkbench", "allParamSpecs"):
            with self.subTest(function=name):
                self.assertRegex(html, rf"function {name}\s*\(")

    def test_all_four_scopes_are_offered(self):
        html = page_source()
        for scope in ("batch", "workflow", "task"):
            with self.subTest(scope=scope):
                self.assertIn(f'value="{scope}"', html)

    def test_workbench_talks_to_the_registry_endpoint(self):
        html = page_source()
        self.assertIn("/api/params", html)
        self.assertIn("/api/params/apply", html)

    def test_workbench_loads_when_its_own_stage_is_opened(self):
        """The complete generation configuration lives inside stage 2.

        Workflow, model, image specification, style, LoRA and parameters are
        one configuration surface; lazy loading therefore keys off stage 2.
        """
        html = page_source()
        self.assertIn("currentStep===2&&!workbench", html)
        self.assertIn("const LAST_STEP=4;", html, "阶段数必须与实际阶段一致")
        # The merge must not drop any pinned id from the two former stages.
        for marker in ('id="paramWorkbench"', 'id="precheck"', 'id="workflowFacts"',
                       'id="workflowFingerprint"', 'id="chainSummary"', 'id="ioSummary"',
                       'id="model"', 'id="globalImagePreset"', 'id="styleLibrary"',
                       'id="styleName"', 'id="loraPick"', 'id="paramScope"', 'id="paramGroups"',
                       'id="applyParams"', 'id="paramFeedback"'):
            self.assertIn(marker, html, f"合并面板丢失了 {marker}")

    def test_generation_configuration_is_one_stage_two_surface(self):
        """Model/style/LoRA/spec controls may not drift back to separate pages."""
        html = html_source()
        stage_two_start = html.index('data-step="2"')
        stage_three_start = html.index('data-step="3"')
        stage_two = html[stage_two_start:stage_three_start]

        for marker in ('id="model"', 'id="styleLibrary"', 'id="styleName"',
                       'id="loraPick"', 'id="styles"', 'id="loras"',
                       'id="globalImagePreset"', 'id="aspect"'):
            with self.subTest(marker=marker):
                self.assertEqual(html.count(marker), 1, f"{marker} 必须全局唯一")
                self.assertIn(marker, stage_two, f"{marker} 必须位于第二页")

        self.assertEqual(html.count('class="step-tab'), 4)
        self.assertNotIn("模型与风格</button>", html)

    def test_three_top_level_modules_are_peers(self):
        """生图工作台 / 风格图鉴 / 风格对比 是平级的顶层模块（v2.22 起）。

        模块条提供三个入口；图鉴与对比各自以全视口 iframe 挂 8793 服务；
        图鉴带 embed=1 隐藏大标题，看图区域最大化。图鉴不再是第四阶段之后
        的第五步，左栏只保留四个工作阶段。
        """
        html = html_source()
        self.assertEqual(html.count('class="mod-tab'), 3)
        for marker in ('id="module-atlas"', 'id="module-compare"',
                       'id="atlasFrame"', 'id="compareFrame"',
                       'id="modulebar"'):
            self.assertIn(marker, html)
        self.assertNotIn('data-step="5"', html, "图鉴不得再作为第五阶段出现")
        js = js_source()
        self.assertIn("function goModule(", js)
        self.assertIn("8793/?embed=1", js)
        self.assertIn("8793/compare", js)
        css = css_source()
        self.assertIn(".modulebar{", css)
        self.assertIn(".module-panel{", css)
        self.assertNotIn(".atlas-frame{", css, "旧的卡片内嵌样式应被模块全视口样式取代")
        # 面板可见性必须由 body.mod-* 类驱动；用 hidden 属性而 CSS 不摘除，
        # 就会出现「点击后生图区隐藏、模块面板仍 hidden」的一片空白。
        import re as _re
        for sec in ("module-atlas", "module-compare"):
            m = _re.search(r'<section id="%s"[^>]*>' % sec, html)
            self.assertIsNotNone(m, f"缺少模块面板 {sec}")
            self.assertNotIn("hidden", m.group(0), f"{sec} 不得用 hidden 属性")
        self.assertIn("body.mod-atlas #module-atlas", css)
        self.assertIn("body.mod-compare #module-compare", css)
        self.assertIn(".module-panel{\n  display:none;", css)

    def test_global_image_specification_is_a_first_class_module(self):
        html = page_source()
        for marker in ('class="image-spec-module"', 'id="imageSpecSummary"',
                       'id="imageSpecMeta"', 'function renderImageSpecSummary'):
            self.assertIn(marker, html, f"全局图像规格模块缺少 {marker}")

    def test_sidebar_is_vertical_and_owns_the_effective_summary(self):
        html = html_source()
        sidebar = html[html.index('<aside class="step-nav">'):html.index('</aside>')]
        self.assertIn('id="truthRail"', sidebar, "生效摘要必须位于左侧导航下方")
        self.assertLess(sidebar.index('data-step-target="4"'), sidebar.index('id="truthRail"'))
        css = css_source()
        self.assertIn('.workspace > .step-nav', css)
        self.assertIn('display:block;', css)

    def test_large_configuration_surfaces_are_collapsible(self):
        html = html_source()
        self.assertIn('<details class="image-spec-module"', html)
        self.assertIn('<details class="parameter-console"', html)
        self.assertIn('id="paramCompactSummary"', html)
        self.assertIn('function renderParamCompactSummary', page_source())

    def test_run_controls_follow_advanced_protection(self):
        html = html_source()
        stage_three = html[html.index('data-step="3"'):html.index('data-step="4"')]
        advanced = stage_three.index('高级生成保护与可选 LLM 精修')
        pause = stage_three.index("control('pause'")
        resume = stage_three.index("control('resume'")
        cancel = stage_three.index("control('cancel'")
        self.assertLess(advanced, pause)
        self.assertLess(pause, resume)
        self.assertLess(resume, cancel)

    def test_import_runs_the_preflight_banner(self):
        """导入即预检：三条导入路径都要触发同一次预检，横幅只此一份。"""
        html = page_source()
        self.assertIn('id="precheck"', html)
        self.assertEqual(html.count('function precheckAfterImport'), 1)
        self.assertEqual(html.count("precheckAfterImport()}catch"), 3,
                         "提示词文件、图片文件与链接图片三条导入路径都应调用 precheckAfterImport")
        self.assertIn("/api/extract-images", html)

    def test_truth_rail_carries_the_capability_facts(self):
        """B1：采样链路与输入输出常驻真相栏，任何阶段可见。"""
        html = page_source()
        for marker in ('id="railSamplers"', 'id="railIO"'):
            self.assertIn(marker, html)

    def test_remember_this_params_switch_is_wired(self):
        """方案A(docs/15 §2)：批次应用可勾选「记住这次的参数」，等于显式 remember()。"""
        html = page_source()
        for marker in ('id="rememberWrap"', 'id="rememberParams"', '记住这次的参数'):
            self.assertIn(marker, html, f"缺少记住参数开关标记 {marker}")
        # The switch only shows for batch scope and only batch+remember persists.
        self.assertIn("rememberWrap.hidden=scope!=='batch'", html)
        self.assertIn("rememberNow=scope==='batch'&&$('rememberParams')&&$('rememberParams').checked", html)
        self.assertIn("if(rememberNow)body.remember=true", html)


class ReadableSourceTests(unittest.TestCase):
    """The sources must stay patchable.

    One extra brace in a 4000-character line once killed the whole script block,
    and it made scripted edits mis-target repeatedly. Both problems come from the
    minified form, so its absence is pinned here.
    """

    #: A line that touches a template literal cannot be split (the split would land
    #: inside the string), and the splitter is deliberately conservative elsewhere:
    #: where it cannot be certain a character is code, it leaves the line alone.
    #: 800 is therefore the practical ceiling -- far below the 2793-character lines
    #: the minified file had, and small enough that a text match has an anchor.
    MAX_LINE = 800

    def test_no_monster_lines_outside_templates(self):
        code = js_source()
        offenders = [
            (len(line), line[:60])
            for line in code.split("\n")
            if len(line) > self.MAX_LINE and "`" not in line
        ]
        self.assertEqual([], offenders[:3], f"{len(offenders)} 行超过 {self.MAX_LINE} 字符")

    def test_the_script_is_not_minified(self):
        """Line count is the cheap proxy: the minified form was 798 lines of monsters."""
        code = js_source()
        lines = code.split("\n")
        self.assertGreater(len(lines), 900, "脚本行数过少，可能又变成压缩形态")
        longest = max(len(line) for line in lines)
        self.assertLess(longest, 2800, "出现过原压缩形态那种超长行")

    def test_css_is_one_declaration_per_line(self):
        css = css_source()
        dense = [line for line in css.split("\n") if line.count(";") >= 3]
        self.assertEqual([], dense[:3], f"{len(dense)} 行挤了多条样式声明")


class HiddenToggleTests(unittest.TestCase):
    """The ``hidden`` attribute only hides through a user-agent rule.

    ``[hidden] { display: none }`` comes from the user-agent sheet, so any author
    declaration of ``display`` on the same element wins. ``.drawer`` set
    ``display: flex`` and the attribute the page toggles therefore did nothing:
    the drawer stayed fixed over the truth rail at ``z-index: 60`` with pointer
    events on, and the rail's 阻断问题 button could not be clicked at all.
    """

    @staticmethod
    def _display_setters_by_class(css: str) -> dict[str, str]:
        """Classes whose rule sets ``display`` on *that* element.

        Only the subject compound counts: ``.deleted-presets .row`` styles the
        row, not the element carrying ``deleted-presets``.
        """
        setters: dict[str, str] = {}
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            value = re.search(r"display\s*:\s*([^;}]+)", body)
            if not value or value.group(1).strip() == "none":
                continue
            if "[hidden]" in selector or ":hover" in selector:
                continue
            subject = re.split(r"[ >+~]+", selector.strip())[-1]
            for name in re.findall(r"\.([A-Za-z][\w-]*)", subject):
                setters.setdefault(name, selector.strip()[:60])
        return setters

    @staticmethod
    def _hidden_toggles(html: str) -> list[str]:
        """Classes of elements carrying the ``hidden`` *attribute*.

        The attribute is matched outside the class value, so ``class="fields
        hidden"`` -- which toggles the ``.hidden`` class instead -- is not one.
        """
        names: list[str] = []
        for tag in re.findall(r"<[^>]*>", html):
            without_class = re.sub(r'class="[^"]*"', "", tag)
            if not re.search(r"\shidden(?=[\s/>])", without_class):
                continue
            classes = re.search(r'class="([^"]*)"', tag)
            if classes:
                names.extend(classes.group(1).split())
        return names

    @classmethod
    def _offenders(cls, html: str, css: str) -> list[tuple[str, str]]:
        setters = cls._display_setters_by_class(css)
        return [
            (name, setters[name])
            for name in cls._hidden_toggles(html)
            if name in setters and f".{name}[hidden]" not in css
        ]

    def test_every_hidden_toggle_that_sets_display_also_overrides_hidden(self):
        offenders = self._offenders(html_source(), css_source())
        self.assertEqual([], offenders[:3],
                         f"{len(offenders)} 个用 hidden 切换的元素被 author display 覆盖")

    def test_the_check_detects_the_real_defect(self):
        """The guard must fail on exactly the rule that shipped."""
        broken_html = '<aside id="drawer" class="drawer" hidden></aside>'
        broken_css = ".drawer {\n  position:fixed;\n  display:flex\n}"
        self.assertEqual([("drawer", ".drawer")], self._offenders(broken_html, broken_css))

        fixed_css = broken_css + "\n.drawer[hidden] {\n  display:none\n}"
        self.assertEqual([], self._offenders(broken_html, fixed_css))

    def test_a_hidden_toggle_with_no_display_rule_is_left_alone(self):
        """``#abortBanner``/``#deletedPresets`` rely on the user-agent rule."""
        html = '<div id="abortBanner" class="abort-banner" hidden></div>'
        css = ".abort-banner {\n  margin-top:10px\n}"
        self.assertEqual([], self._offenders(html, css))

    def test_a_descendant_rule_is_not_attributed_to_the_ancestor(self):
        """``.deleted-presets .row`` styles the row, not the hidden panel."""
        html = '<div id="deletedPresets" class="deleted-presets" hidden></div>'
        css = ".deleted-presets .row {\n  display:flex\n}"
        self.assertEqual([], self._offenders(html, css))

    def test_a_class_named_hidden_is_not_the_attribute(self):
        """``class="fields hidden"`` toggles a class, so the attribute is absent."""
        html = '<div id="llmFields" class="fields hidden"></div>'
        css = ".fields {\n  display:grid\n}"
        self.assertEqual([], self._offenders(html, css))


class AppModuleWiringTests(unittest.TestCase):
    def test_app_source_uses_the_gateway_backed_client_and_schema(self):
        from fakes import app_source

        source = app_source()
        # The app talks to ComfyUI through ComfyClient, which is the facade over
        # ProductionGateway; the gateway itself is wired in the core module.
        self.assertIn("ComfyClient", source)
        self.assertIn("refresh_schema", source)
        self.assertIn("ResultReviewStore", source)
        # The old filesystem-only inventory path must be routed through the
        # client-aware helper so /models listings include extra_model_paths.
        self.assertIn("self._inventory()", source)

    def test_core_source_defines_the_new_modules(self):
        from fakes import SOURCE_ROOT

        core = (SOURCE_ROOT / "comfybatch_v2_core.py").read_text(encoding="utf-8")
        self.assertIn("from comfybatch_gateway import ProductionGateway", core)
        self.assertIn("convert_node", core)
        self.assertIn("CompiledGraphAudit", core)

    def test_positional_widget_cursor_is_gone(self):
        """The defect must not survive anywhere in the compiler.

        The marker is a fragment of the old assignment statement rather than the
        bare name ``widget_index``, because the new module's docstring quotes the
        old code to explain what was wrong with it.
        """
        from fakes import SOURCE_ROOT

        marker = "or widget_index)]"
        for name in ("comfybatch_v2_core.py", "comfybatch_nodeschema.py"):
            source = (SOURCE_ROOT / name).read_text(encoding="utf-8")
            with self.subTest(module=name):
                self.assertNotIn(marker, source, "位置游标又回到了实际代码里")

    def test_server_and_page_versions_agree(self):
        """One version, stated in both places; a mismatch ships silently."""
        from fakes import app_source

        source = app_source()
        declared = re.search(r'APP_VERSION = "([\d.]+)"', source)
        self.assertIsNotNone(declared, "版本号必须写在一处")
        self.assertNotIn('"2.13"', source, "旧版本号必须清掉")
        # The server banner and the page must both read that one value, and no
        # route may hardcode its own copy (that is how the ping version drifted).
        self.assertIn("f\"ComfyBatchV2/{APP_VERSION}\"", source)
        self.assertNotIn('"version": "2.1', source, "接口里不得再写死版本号")
        page = re.search(r"ComfyBatch V([\d.]+)", html_source())
        self.assertIsNotNone(page)
        self.assertEqual(declared.group(1), page.group(1), "前后端版本号必须一致")


class AtlasLinkageTests(unittest.TestCase):
    """v2.23 三方联动：工作台 ⇄ 图鉴 ⇄ 对比。

    四条链路各落一个锚点：出图→图鉴（批次完成通知重建）、
    图鉴→对比（selection 会话 + postMessage）、图鉴/对比→工作台
    （预设导入 API，名字可自定义）、工作台输出位置←图鉴（选目录对话框）。
    断言锚点字符串而不是行为 —— 行为由 jsdom 冒烟测试盯。
    """

    @classmethod
    def _tools_source(cls, name: str) -> str:
        from fakes import PACKAGE_ROOT

        return (PACKAGE_ROOT / "tools" / name).read_text(encoding="utf-8")

    def test_batch_completion_notifies_the_atlas(self):
        js = js_source()
        self.assertIn("function atlasRebuild(", js)
        self.assertIn("8793/api/rebuild", js)
        # 状态跃迁处必须真的调用它（只挂完成态；取消/中止不重建）
        self.assertIn(
            "['completed','completed_with_errors'].includes(s.status))atlasRebuild()",
            js)

    def test_atlas_page_can_push_selection_into_compare(self):
        atlas = self._tools_source("build_atlas.py")
        for marker in ('function openInCompare(', "'/api/selection'",
                       "atlas-open-compare", "window.open('/compare?sel='+"):
            self.assertIn(marker, atlas, f"图鉴页缺少对比联动锚点 {marker}")

    def test_atlas_and_compare_can_import_presets_into_s(self):
        # 图鉴大图按钮 + 对比页按钮都 POST 到 S 的预设 API，名字可自定义
        atlas = self._tools_source("build_atlas.py")
        self.assertIn('id="vimp"', atlas)
        self.assertIn("function importToS(", atlas)
        self.assertIn("/api/save-style-lora-preset", atlas)
        self.assertIn("prompt(", atlas, "导入必须允许自定义命名")
        live = self._tools_source("compare_live.html")
        self.assertIn("⇢ 导入工作台", live)
        self.assertIn("/api/save-style-lora-preset", live)

    def test_s_accepts_selection_messages_and_picks_atlas_dirs(self):
        js = js_source()
        html = html_source()
        css = css_source()
        self.assertIn("atlas-open-compare", js)
        self.assertIn("function openAtlasDirPicker(", js)
        self.assertIn("8793/api/data", js)
        self.assertIn('id="atlasDirDialog"', html)
        self.assertIn("从图鉴选位置…", html)
        self.assertIn(".atlas-dir-dialog{", css)

    def test_strip_comments_removes_prose_but_keeps_code(self):
        """The helper above has to actually strip, and to leave code alone."""
        sample = ("/* 不能这样写 fetch(u,{cache:'no-store'}) */\n"
                  "const a='no-store';// 也不带 cache:'no-store'\n"
                  "fetch('/api/version?_=' + Date.now());\n")
        stripped = strip_comments(sample)
        self.assertNotIn("不能这样写", stripped)
        self.assertNotIn("也不带", stripped)
        self.assertIn("const a='no-store';", stripped)
        self.assertIn("fetch('/api/version?_=' + Date.now());", stripped)

    def test_strip_comments_survives_nested_templates(self):
        """``${...}`` 里再嵌一层模板时账本不能错位。

        漏记插值边界的话，状态会在第一个 ``${}`` 之后整体偏一档：本该回到
        模板态的地方还留在代码态，于是**后面每一个注释都不再被剥掉** ——
        而本文件里就有 ``x=>`<option ...>` `` 这种嵌套，实测正是这么漏的。
        偏偏「漏剥」是往宽松方向失效，所以必须单独钉住。
        """
        sample = ("const a=`x${ f(y=>`z`) }w`;\n"
                  "// 该消失的注释\n"
                  "const b=1;\n")
        stripped = strip_comments(sample)
        self.assertNotIn("该消失的注释", stripped)
        self.assertIn("const b=1;", stripped)
        self.assertIn("const a=`x${ f(y=>`z`) }w`;", stripped)

    def test_cross_port_probes_stay_simple_and_can_time_out(self):
        """跨端口探测：保持「简单请求」，并且必须能超时。

        这里**刻意不**断言 ``cache:'no-store'``。曾经以为它会触发 CORS 预检、
        让门控永远停在「正在连接图鉴服务…」；用真浏览器（Chromium）实测推翻了：
        它确实会给请求补上 ``Cache-Control: no-cache`` / ``Pragma: no-cache``，
        但**不会**发 OPTIONS 预检 —— cache mode 自己加的头不算"作者自定义头"，
        那条 fetch 9ms 就正常 resolve 了。真因是启动序列踩了 TDZ，见 BootOrderTests。

        留下来的两条都有实测支撑：破缓存用时间戳参数（把请求保持在「简单请求」
        这一档，就不必依赖服务端答得出预检）；必须带 AbortController ——
        没有超时的 fetch 一旦挂住，提示语会永远停在「正在连接…」，
        用户分不清是慢还是卡死。
        """
        code = strip_comments(js_source())
        self.assertIn("?_='+Date.now()", code, "破缓存改用时间戳参数")
        self.assertIn("AbortController", code,
                      "探测必须能超时，否则挂住时提示语永远停在「正在连接…」")
        self.assertIn("AbortError", code, "超时要能说清是超时，而不是假装还在连")

    def test_compare_page_shows_mark_status(self):
        """对比页要能看出「这张图已标进哪个夹」。

        对比页是「用另一套版式再看一遍同一批图」，看不到标记就等于把图鉴里
        已经分好堆的结论丢掉一半 —— 用户的原话是「风格对比丧失了标记的能力」。
        """
        live = self._tools_source("compare_live.html")
        code = strip_comments(live)
        for marker in ("/api/marks",             # 从图鉴取标记
                       "MK_UID", "MK_PATH",      # uid 精确 + path 兜底两条通道
                       "MCOLORS",                # 与图鉴同一套色号
                       "markChips(",             # 卡片上的标记签
                       "ONLY_MARKED",            # 「只看已标记」
                       "已标记"):
            self.assertIn(marker, code, f"对比页缺少标记锚点 {marker}")
        # 色号必须和图鉴一致，否则同一张图在两个页面是两种颜色，等于白标
        atlas = self._tools_source("build_atlas.py")
        palette = re.search(r"const COLORS=\[([^\]]+)\]", atlas)
        self.assertIsNotNone(palette, "图鉴的色号表找不到，两页颜色对齐的断言就失效了")
        self.assertIn(palette.group(1), live,
                      "对比页的标记色号必须与图鉴同一套")

    def test_compare_page_marks_use_apply_not_whole_overwrite(self):
        """对比页**可以**标记，但只能走局部接口，不许整套覆盖。

        这条护栏在 V2.32 被有意改写。原来的版本钉的是「对比页完全只读」，
        理由是写协议是「整体覆盖 sets」（前端是权威）—— 对比页手里那份 sets
        是加载时的快照，覆盖回去会把图鉴期间新标的图全抹掉。用户随后明确
        要求「对比页要能直接标 淘汰/保留」，于是问题变成：怎样让对比页能写、
        又不会吃数据。答案是换协议，不是换口号：

        · 图鉴页 → POST /api/marks      整体覆盖（它有全量权威副本）
        · 对比页 → POST /api/marks/apply 局部读-改-写（只说这几张图 × 这个夹）

        apply 走服务端现读现改，两端并发互不影响。所以这里禁的是整体覆盖，
        不是禁写 —— 目标（对比页不能吃掉图鉴的数据）没变，手段变了。
        """
        code = strip_comments(self._tools_source("compare_live.html"))
        self.assertIn("fetch('/api/marks',{cache:'no-store'})", code,
                      "仍要能读标记")
        self.assertNotIn("/api/marks',{method:'POST'", code,
                         "对比页不许整体覆盖 sets —— 会把图鉴期间新标的图抹掉")
        self.assertIn("/api/marks/apply", code,
                      "对比页的写入必须走局部 apply 接口")

    def test_compare_verdict_buttons_match_atlas_size_and_place(self):
        """淘汰/保留按钮的尺寸与位置必须和图鉴的 .mk 一致。

        用户在对比页是整页几十张连续过的，靠肌肉记忆盲点。按钮大小或位置
        一变就会点空 —— 用户明确提了这一点（「注意标记的按钮的位置和大小」）。
        这里把两者钉成同一个数值：26px 圆、left 6px、top 6px。
        """
        live = self._tools_source("compare_live.html")
        atlas = self._tools_source("build_atlas.py")
        spec = r"width:26px;height:26px"
        self.assertIn(spec, atlas, "图鉴的 .mk 规格变了，这条断言的前提失效")
        # 对比页的 .mk 行内样式必须含同一组数值（可能被换行拆开，先压平）
        flat = re.sub(r"\s+", "", live)
        self.assertIn("top:6px;left:6px;z-index:3;width:26px;height:26px",
                      flat, "对比页标记按钮的尺寸/位置与图鉴不一致")
        for anchor in ("setVerdict(", "SET_DEL", "SET_KEEP"):
            self.assertIn(anchor, live, f"对比页缺少判定锚点 {anchor}")

    def test_compare_verdict_marks_are_scoped_per_batch(self):
        """淘汰/保留的夹必须按批次派生，不能全局共用一个夹。

        用户报的现象：「92 张的批次里选中了一些，不希望和之前的混在一起」。
        实测他那份 _marks.json 的「默认」夹 43 张跨了 14 个批次，找不回来 ——
        根因就是判定夹原先写死为 verdict-reject / verdict-keep 两个固定 id，
        所有批次的图都往里堆。

        所以钉住三件事：
        1) 从 uid 派生批次段（uid = 路径#批次#序号）
        2) 夹 id 带批次后缀，且判定用**前缀匹配**（老的无后缀夹也要认得出）
        3) 色位从前缀判，不再 VERDICT_SLOT[setId] 直接查表
           —— 带后缀的 id 查表会是 undefined，等于把颜色丢了
        """
        live = strip_comments(self._tools_source("compare_live.html"))
        self.assertIn("function batchOf(", live, "缺少批次派生函数")
        self.assertIn("function verdictSet(", live, "缺少按批次派生的判定夹构造")
        self.assertIn("function isVerdictSet(", live, "缺少判定夹的前缀识别")
        self.assertIn("function slotFor(", live, "缺少色位的前缀判定")
        # 派生夹的 id 形如 base+'@'+batch
        self.assertIn("base+'@'+b", live, "判定夹 id 没带批次后缀")
        # 不许再直接按 setId 查色位表（带 @ 后缀会取到 undefined）
        self.assertNotIn("VERDICT_SLOT[setId]", live,
                         "色位不能用 setId 直接查表：派生 id 查不到会丢颜色")
        # _UNNAMED 是占位不是真批次，必须归到统一的 x 夹
        self.assertIn("_UNNAMED", live, "缺少对占位批次 _UNNAMED 的处理")

    def test_compare_clear_verdicts_groups_by_batch(self):
        """「清空判定」必须按 目标夹×判定 分组，不能只发两条 op。

        判定夹按批次派生后，同一页可能同时涉及多个批次的夹。
        如果清空时只按 kind 合并成两条 op，就会把不同批次的图
        写进同一个夹里 —— 正是这次要修的问题。

        注：这里钉 targetSet（而不是更早的 verdictSet(kind,batchOf(it))）——
        targetSet 内含「继承同批次老夹」的逻辑，是更强的约束：
        既保证按批分夹，又保证不会把同批图分裂到老夹之外。
        """
        live = strip_comments(self._tools_source("compare_live.html"))
        pos = live.find("async function clearVerdicts(")
        self.assertGreater(pos, 0, "找不到 clearVerdicts")
        body = live[pos:pos + 2200]
        self.assertIn("targetSet(kind,it)", body,
                      "清空时必须按每张图自己的批次选夹（且走继承逻辑）")
        self.assertIn("groups", body, "缺少分组逻辑")

    def test_compare_clear_verdicts_recycles_empty_derived_sets(self):
        """清空判定后要回收自己派生的空夹，但**只回收派生夹**。

        判定夹按批次派生后，每标一批再清空就留一个空壳，
        几十批下来夹列表全是空的。所以派生夹空掉就顺手删 ——
        但图鉴页手动建的夹（用户自己命名、无 @<batch> 后缀）绝不能碰。
        """
        live = strip_comments(self._tools_source("compare_live.html"))
        pos = live.find("async function clearVerdicts(")
        self.assertGreater(pos, 0)
        body = live[pos:pos + 3000]
        self.assertIn("isVerdictSet(s.id,'del')||isVerdictSet(s.id,'keep')", body,
                      "回收空夹时必须校验它确实是派生夹")
        self.assertIn("drop:true", body, "缺少整夹删除标记")
        self.assertIn("drop", self._tools_source("atlas_server.py"),
                      "服务端不支持 drop，空夹回收会落空")

    def test_marks_apply_supports_drop_but_never_touches_files(self):
        """apply 的 drop 只能删**派生夹**，不能删图，也不能删老夹。"""
        server = self._tools_source("atlas_server.py")
        pos = server.index('if bool(op.get("drop"))')
        body = server[pos:pos + 1100]
        self.assertIn('s["id"] != sid', body, "drop 必须按 id 精确筛掉，不能整表替换")
        # 老夹（无 @ 后缀）是用户历史数据，服务端必须自己拦住，不靠前端自觉
        self.assertIn('"@" not in sid', body,
                      "服务端必须拒绝删除无后缀的老夹（用户数据）")
        # drop 只解引用；不该出现任何删文件的调用
        for danger in ("os.remove", "os.unlink", "shutil.rmtree", "send2trash"):
            self.assertNotIn(danger, body, f"drop 路径里不该出现 {danger}")

    def test_compare_inherits_legacy_verdict_set_same_batch(self):
        """升级不能让同一批图分裂成两个夹。

        用户实测：V2.32 标的 15 张「保留」全在无后缀 verdict-keep 里，
        且全部来自批次 926266f5。如果升级后无脑新建 verdict-keep@926266f5，
        同一批图就分成两个夹，用户会以为「之前标的没了」。
        所以必须：同批次有老夹 → 继续写老夹；别批次 → 照常新建派生夹。
        """
        live = strip_comments(self._tools_source("compare_live.html"))
        self.assertIn("function inheritSetId(", live, "缺少老夹继承逻辑")
        self.assertIn("function targetSet(", live, "缺少统一的夹选择入口")
        # setVerdict / clearVerdicts 都必须走 targetSet，不能各自拿 verdictSet
        for fn in ("async function setVerdict(", "async function clearVerdicts("):
            pos = live.find(fn)
            self.assertGreater(pos, 0, f"找不到 {fn}")
            body = live[pos:pos + 2500]
            self.assertIn("targetSet(", body, f"{fn} 必须走 targetSet（否则会分裂）")
        # 无后缀老夹绝不能被自动回收
        pos = live.find("const empty=[]")
        self.assertGreater(pos, 0)
        body = live[pos:pos + 700]
        self.assertIn("indexOf('@')>0", body,
                      "空夹回收必须排除无后缀老夹（那是用户历史数据）")

    def test_marks_apply_endpoint_is_partial_and_locked(self):
        """服务端的局部标记接口必须只动指定的图，并且加锁。"""
        server = self._tools_source("atlas_server.py")
        self.assertIn('u.path == "/api/marks/apply"', server,
                      "局部标记接口不存在，对比页的写入会落空")
        pos = server.index('u.path == "/api/marks/apply"')
        body = server[pos:pos + 3000]
        self.assertIn("_marks_lock", body,
                      "apply 必须持标记锁：读-改-写不加锁会在并发时丢标记")
        # 新保存协议合并本页增量；旧页面的整份覆盖已被拒绝。
        self.assertNotIn("def _apply_marks_overwrite", server)
        self.assertIn("'/api/marks/sync'", server)
        # 锁是**模块级**的：Handler 每请求实例化一次，挂在 self 上的锁形同虚设
        # （每次请求都是新对象，等于没锁）。这条断言防的就是这个 —— 写这段代码
        # 时第一版正是 self._marks_lock，真起服务打请求才炸出来。
        self.assertNotIn("self._marks_lock", server,
                         "锁必须挂在模块上；Handler 实例是每次请求新建的")
        self.assertIn("_marks_lock = threading.Lock()", server,
                      "锁本身要在模块级定义")
        self.assertIn("with _marks_lock:", server,
                      "锁必须真的用 with 拿")

    def test_selection_items_carry_uid(self):
        """对比页靠 uid 认标记，服务器就得把 uid 放进 selection 的每一条里。

        只给 path 的话，跨批次的同名图会互相认错 —— 图鉴那边正是因为这个才
        引入了 uid（path#批次#序号）。
        """
        server = self._tools_source("atlas_server.py")
        sel = server.index('u.path == "/api/selection"', server.index("def do_POST"))
        body = server[sel:sel + 2500]
        self.assertIn('"uid": im.get("uid") or ""', body,
                      "POST /api/selection 必须把 uid 带进 items")

    def test_compare_route_supports_embed_chrome_hiding(self):
        server = self._tools_source("atlas_server.py")
        # /compare 响应必须支持 embed=1 注入（S 的 iframe 内嵌藏大标题）
        compare_pos = server.index('u.path == "/compare"')
        embed_pos = server.index('if "embed" in qs', compare_pos)
        self.assertLess(compare_pos, embed_pos)


class EmbeddedAtlasBuildTests(unittest.TestCase):
    """内嵌图鉴的构建契约：打出来的 exe 里必须真的有图鉴。

    这条测试防的是「组合」被悄悄拆掉。改动之前的 S.spec 里 datas 只有四个
    前端文件，exe 里没有图鉴代码，于是「启动一个就够」退回成「还得手动双击
    桌面启动器」—— 界面看起来一切正常，没有任何测试会失败。把契约写在这里，
    下次谁改 spec 忘了带上 tools/，测试先说话。
    """

    @classmethod
    def setUpClass(cls):
        from fakes import PACKAGE_ROOT

        cls.spec = (PACKAGE_ROOT / "src" / "S.spec").read_text(encoding="utf-8")

    def test_spec_ships_every_atlas_module(self):
        for mod in ("atlas_server", "style_atlas", "build_atlas",
                    "build_thumbs", "atlas_host"):
            self.assertIn(mod, self.spec, f"S.spec 的 hiddenimports 少了 {mod}")

    def test_spec_resolves_tools_from_the_spec_itself(self):
        # 用 SPECPATH 推绝对路径，别写 '../tools'：spec 里的相对路径
        # 会跟着调用 PyInstaller 时的工作目录走，换台机器就指错。
        self.assertIn("SPECPATH", self.spec)
        self.assertIn("TOOLS", self.spec)

    def test_spec_ships_atlas_assets_and_user_data(self):
        for name in ("compare_live.html", "atlas_initial_data.json", "_grades_system.json"):
            self.assertIn(name, self.spec, f"S.spec 的 datas 少了 {name}")

    def test_page_no_longer_tells_users_to_start_it_by_hand(self):
        """界面里不该再出现「自己去开图鉴」的引导 —— 那是两个程序时代的说法。"""
        for text in (js_source(), html_source()):
            self.assertNotIn("风格图鉴-打开.cmd", text)


class CompareVerdictBehaviourTests(unittest.TestCase):
    """对比页「淘汰 / 保留」的**行为**验证（真起服务 + 真 HTTP）。

    上面 AtlasLinkageTests 里的断言只看字符串锚点，挡不住语义错误 ——
    写这段代码时就吃过两次亏，都是锚点全绿但实际是坏的：

    1. ``self._marks_lock``（实例属性）—— Handler 每请求新建，
       锁等于没有；真打请求直接 AttributeError。
    2. ``MCOLORS.indexOf(自定义色)`` 反查得到 ``-1``，``-1`` 是合法 int，
       被原样存进 ``_marks.json``，前端取模保护掩盖了它。

    所以这里不查字符串，起真服务跑一遍完整链路，断言**数据**。
    隔离在临时目录（必须同时改 DATA_DIR 与 Handler.out_dir ——
    marks_file 取自后者，只改前者会把测试数据写进用户真文件）。
    """

    @classmethod
    def setUpClass(cls):
        import tempfile
        import threading
        import time

        from fakes import PACKAGE_ROOT

        tools = PACKAGE_ROOT / "tools"
        sys.path.insert(0, str(tools))
        import atlas_server as S

        cls.S = S
        cls.tmp = tempfile.mkdtemp(prefix="cmp_verdict_test_")
        # 真图片文件：/api/marks 会过滤掉指向不存在文件的标记，
        # 用假路径的话读回来全是空集，看起来像接口坏了（也是踩过的坑）。
        cls.IMG = [str(pathlib.Path(cls.tmp) / ("shot%d.png" % i)) for i in range(1, 5)]
        for p in cls.IMG:
            pathlib.Path(p).write_bytes(b"\x89PNG\r\n\x1a\n")
        cls.UID = [p + "#runX#%d" % i for i, p in enumerate(cls.IMG)]

        cls._orig_dir = S.DATA_DIR
        cls._orig_out = S.Handler.out_dir
        S.DATA_DIR = cls.tmp
        S.Handler.out_dir = cls.tmp          # 关键：marks_file 取自这里
        cls._orig_state = S._state.get("data")
        S._state["data"] = {"images": [
            {"path": cls.IMG[i], "uid": cls.UID[i], "preset": "预设%d" % i,
             "styles": [], "loras": [], "index": i + 1, "run": "runX"}
            for i in range(4)]}

        from http.server import ThreadingHTTPServer

        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        import shutil

        cls.srv.shutdown()
        cls.S.DATA_DIR = cls._orig_dir
        cls.S.Handler.out_dir = cls._orig_out
        cls.S._state["data"] = cls._orig_state
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _req(self, method, path, obj=None):
        import http.client
        import json

        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        body = json.dumps(obj).encode("utf-8") if obj is not None else None
        c.request(method, path, body,
                  {"Content-Type": "application/json"} if body else {})
        r = c.getresponse()
        raw = r.read().decode("utf-8")
        c.close()
        return r.status, json.loads(raw)

    def _reset(self, sets):
        import json

        from atlas_resources import ResourceStore
        ResourceStore(self.tmp).save('_marks.json', {"sets": sets, "active": sets[0]["id"]})

    def _sets(self):
        _, m = self._req("GET", "/api/marks")
        return {s["id"]: set(s["paths"]) for s in m["sets"]}, m

    def test_isolation_writes_to_temp_dir(self):
        """先证明这条测试写的是临时文件 —— 否则它会污染用户数据。

        上一次就是只改了 DATA_DIR 没改 Handler.out_dir，把测试夹写进了
        用户真的 _marks.json。这条断言放在最前面，隔离失败就不必再看别的。
        """
        self.assertEqual(self.S.Handler.out_dir, self.tmp)
        self._reset([{"id": "sX", "name": "默认", "paths": [self.UID[0]],
                      "note": {}, "color": 0}])
        _, j = self._req("POST", "/api/marks/apply", {"ops": [
            {"set": "verdict-keep", "name": "保留", "uids": [self.UID[1]],
             "remove": False}]})
        self.assertTrue(j.get("ok"), j)
        self.assertIn(self.tmp.lower(), str(j.get("file", "")).lower(),
                      "写入必须落在临时目录，否则会污染用户数据")

    def test_apply_touches_only_target_set_and_uid(self):
        """局部写：只动目标夹的目标图，别的夹一张都不能少。"""
        self._reset([
            {"id": "sDef", "name": "默认", "paths": [self.UID[0], self.UID[1]],
             "note": {}, "color": 0},
            {"id": "sOther", "name": "别的夹", "paths": [self.UID[2]],
             "note": {}, "color": 1},
        ])
        st, j = self._req("POST", "/api/marks/apply", {"ops": [
            {"set": "verdict-reject", "name": "淘汰", "uids": [self.UID[3]],
             "remove": False, "color": 0}]})
        self.assertTrue(j.get("ok"), j)
        cur, _ = self._sets()
        self.assertEqual(cur["verdict-reject"], {self.UID[3]})
        self.assertEqual(cur["sDef"], {self.UID[0], self.UID[1]})
        self.assertEqual(cur["sOther"], {self.UID[2]})

    def test_apply_is_idempotent(self):
        """重复标同一张不会堆成两条 —— 否则计数虚高。"""
        self._reset([{"id": "sX", "name": "默认", "paths": [],
                      "note": {}, "color": 0}])
        for _ in range(3):
            self._req("POST", "/api/marks/apply", {"ops": [
                {"set": "verdict-keep", "name": "保留", "uids": [self.UID[0]],
                 "remove": False}]})
        cur, _ = self._sets()
        self.assertEqual(cur["verdict-keep"], {self.UID[0]})

    def test_apply_remove_empties_set_but_keeps_it(self):
        """取消判定 → 夹空了但夹本身还在（不能把用户的夹删掉）。"""
        self._reset([{"id": "sX", "name": "默认",
                      "paths": [self.UID[0]], "note": {}, "color": 0}])
        self._req("POST", "/api/marks/apply", {"ops": [
            {"set": "verdict-keep", "name": "保留", "uids": [self.UID[0]],
             "remove": False}]})
        self._req("POST", "/api/marks/apply", {"ops": [
            {"set": "verdict-keep", "name": "保留", "uids": [self.UID[0]],
             "remove": True}]})
        cur, _ = self._sets()
        self.assertEqual(cur.get("verdict-keep"), set())
        self.assertIn("verdict-keep", cur)

    def test_apply_dedupes_uid_drift_by_path(self):
        """uid 漂移兜底：同路径换 uid 后缀，不该在夹里留两条。"""
        self._reset([{"id": "sX", "name": "默认", "paths": [],
                      "note": {}, "color": 0}])
        for suffix in ("#runZ#77", "#runZ#99"):
            self._req("POST", "/api/marks/apply", {"ops": [
                {"set": "verdict-keep", "name": "保留",
                 "uids": [self.IMG[0] + suffix], "remove": False}]})
        cur, _ = self._sets()
        self.assertEqual(len(cur["verdict-keep"]), 1, cur["verdict-keep"])

    def test_apply_creates_missing_set_keeping_color(self):
        """夹不存在就现建一个，并带上前端给的色号（否则图鉴里串色）。"""
        self._reset([{"id": "sX", "name": "默认", "paths": [],
                      "note": {}, "color": 0}])
        self._req("POST", "/api/marks/apply", {"ops": [
            {"set": "verdict-reject", "name": "淘汰", "uids": [self.UID[0]],
             "remove": False, "color": 0}]})
        _, m = self._sets()
        vj = [s for s in m["sets"] if s["id"] == "verdict-reject"][0]
        self.assertEqual(vj["name"], "淘汰")
        self.assertEqual(vj.get("color"), 0)

    def test_old_page_overwrite_is_rejected_and_preserves_collections(self):
        self._reset([{"id": "sOld", "name": "旧夹", "paths": [self.UID[0]], "note": {}, "color": 3}])
        st, j = self._req("POST", "/api/marks", {"sets": [
            {"id": "new", "name": "新夹", "paths": [], "note": {}}], "active": "new"})
        self.assertEqual(st, 409)
        cur, _ = self._sets()
        self.assertEqual(cur, {"sOld": {self.UID[0]}})

    def test_selection_items_carry_uid_for_verdict_matching(self):
        """对比页靠 uid 认标记 → selection 的每条必须带 uid，否则按钮认不出状态。"""
        st, j = self._req("POST", "/api/selection",
                          {"label": "测试", "paths": self.IMG})
        self.assertTrue(j.get("ok"), j)
        _, k = self._req("GET", "/api/selection?id=%d" % j["id"])
        items = k["selection"]["items"]
        self.assertEqual(len(items), 4)
        for it in items:
            self.assertTrue(it.get("uid"), "每条都要有 uid：%r" % it)


class CssVariableTests(unittest.TestCase):
    """每个被 var() 引用的自定义属性都必须真的定义过。

    真实故障：``button.danger{background:var(--danger)}`` 引用了 ``--danger``，
    但两份 ``:root`` 都没定义它。``var()`` 不写兜底值时整条声明在计算阶段失效，
    于是这个按钮回落到底层 ``button{color:#fff}`` —— **白字配透明底**，
    用户看到的就是「删除按钮全是白色」（实际整个按钮都看不见）。

    同一批新样式里的 ``--line-strong`` 也漏了。所以这里做一条通用检查：
    凡是文件里被 ``var(--x)`` 引用、又没有就地兜底值的变量，都必须在
    ``:root`` 里声明过。这样以后再漏，测试会先拦下来。
    """

    #: 常量值也在 :root 里给出，允许某些变量只在一处出现。
    _DEF_RE = re.compile(r"(--[a-z0-9-]+)\s*:")
    _USE_RE = re.compile(r"var\(\s*(--[a-z0-9-]+)\s*[,)]")

    def test_every_referenced_custom_property_is_defined(self):
        css = css_source()
        defined = set(self._DEF_RE.findall(css))
        used = set(self._USE_RE.findall(css))
        # 带就地兜底值的引用（var(--x, fallback)）不会静默失效，允许不定义。
        used_with_fallback = set(
            re.findall(r"var\(\s*(--[a-z0-9-]+)\s*,", css)
        )
        missing = sorted(used - defined - used_with_fallback)
        self.assertEqual(
            [], missing,
            "这些自定义属性被 var() 引用却没有定义（声明会整条失效）：%s" % missing,
        )

    def test_the_danger_button_colour_is_defined(self):
        """删除预设按钮必须真的有色，否则白字打在白底上等于消失。"""
        css = css_source()
        defined = set(self._DEF_RE.findall(css))
        self.assertIn("--danger", defined)
        self.assertIn("--line-strong", defined)
        # 取**最后**一条 button.danger：层叠里靠后的那条才是真正生效的
        # （文件里第 106 行还有一条早期的、用 --accent 的规则）。
        rules = re.findall(r"button\.danger\s*\{([^}]*)\}", css)
        self.assertTrue(rules, "button.danger 规则必须存在")
        effective = rules[-1]
        self.assertIn("background:var(--danger)", effective)

    def test_the_readonly_rule_can_actually_match(self):
        """只读态变淡的选择器必须能命中真实 DOM。

        ``.step-panel`` 元素自己就带 ``.card`` 类（index.html 里四个
        ``<section class="card wide step-panel">``），所以旧写法
        ``.step-panel .card button`` 要求「卡片里再套一张卡片」—— DOM 里不存在，
        规则从来没命中过，只读时按钮不会变淡，用户拿不到任何只读反馈。
        """
        css = css_source()
        # 只看选择器区域，忽略注释正文（注释里会引用这个坏写法作说明）。
        selectors = self._selectors_only(css)
        self.assertNotIn(".step-panel .card button", selectors,
                         "这个选择器永远命中不了：.step-panel 自己就带 .card")
        self.assertIn("body.readonly .step-panel button:not(.lease-ok)", selectors)

        # 与真实 DOM 对齐：面板元素上确实同时有 card 和 step-panel 两个类。
        html = html_source()
        panels = re.findall(r'<section[^>]*class="([^"]*step-panel[^"]*)"', html)
        self.assertTrue(panels, "必须找得到 step-panel 面板")
        for cls in panels:
            self.assertIn("card", cls.split(),
                          "step-panel 面板自身带 card，正是旧选择器失效的原因")

    @staticmethod
    def _selectors_only(css: str) -> str:
        """Strip /* ... */ comments so prose cannot satisfy or break a check."""
        return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


if __name__ == "__main__":
    unittest.main()
