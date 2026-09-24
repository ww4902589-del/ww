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


class ScriptIntegrityTests(unittest.TestCase):
    def test_the_page_behaviour_is_one_file(self):
        """One script file, so there is one place behaviour can live."""
        self.assertEqual(1, len(script_blocks()))
        self.assertIn("function renderReview", js_source())

    def test_the_page_has_no_inline_style_or_script(self):
        """Structure only: no <style> and no inline <script> in index.html."""
        html = html_source()
        self.assertNotIn("<style>", html)
        self.assertNotIn("<script>", html)
        self.assertIn('<link rel="stylesheet" href="/app.css">', html)
        self.assertIn('<script src="/app.js"></script>', html)

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
        self.assertIn("const LAST_STEP=5;", html, "阶段数必须与实际阶段一致")
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

        self.assertEqual(html.count('class="step-tab'), 5)
        self.assertNotIn("模型与风格</button>", html)

    def test_the_library_stage_is_the_fifth_and_only_the_fifth_surface(self):
        """作品库是独立阶段：它跨批次，不属于「成图与确认」这一批的结果面。"""
        html = html_source()
        stage_five = html[html.index('data-step="5"'):]
        for marker in ('id="paramWorkbench"', 'id="reviewBoard"', 'id="libBoard"'):
            with self.subTest(marker=marker):
                self.assertEqual(html.count(marker), 1, f"{marker} 必须全局唯一")
        for marker in ('id="libQuery"', 'id="libStatus"', 'id="libRun"', 'id="libSort"',
                       'id="libTagStrip"', 'id="libSummary"', 'id="libBoard"',
                       'id="libPageInfo"', 'id="libFavorite"', 'id="libDeleted"'):
            with self.subTest(marker=marker):
                self.assertIn(marker, stage_five, f"{marker} 必须位于作品库阶段")
        # 侧栏标签与阶段一一对应，多的那个不能是"看不见的第五页"。
        self.assertIn('data-step-target="5"', html)
        self.assertIn("currentStep===5&&!libraryState.loaded", js_source())

    def test_the_library_only_addresses_images_by_record_id(self):
        """作品库不许把磁盘路径交给客户端：预览与原图都只能用 work_id 换。"""
        js = page_source()
        self.assertIn('/api/library/preview?work_id=', js)
        self.assertIn('/api/library/image?work_id=', js)
        self.assertNotIn('/api/library/preview?path=', js)
        self.assertNotIn('/api/library/image?path=', js)
        # 危险动作要二次确认，且移除文案必须说清"文件不会被删"。
        self.assertIn('从作品库移除这条记录？', js)
        self.assertIn('图片仍在原处', js)

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


if __name__ == "__main__":
    unittest.main()

