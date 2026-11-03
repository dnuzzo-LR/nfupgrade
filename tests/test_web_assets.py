import os
import re
import unittest

WEB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "nfupgrader", "web")
README = os.path.join(os.path.dirname(os.path.dirname(__file__)), "README.md")

REQUIRED_IDS = [
    "host", "app-badge", "phase", "status", "bar-fill", "counter", "current",
    "about-grid", "links", "cfg-load-depots", "cfg-depot", "cfg-depot-msg", "cfg-form",
    "cfg-preview", "cfg-save", "cfg-msg", "cfg-rendered", "cfg-issues",
    "mode", "btn-check", "btn-launch", "launch-msg", "preflight", "fix-msg", "override-box",
    "override-msg", "reason", "postflight", "site-table", "min-sev", "log",
    "btn-reports-run", "reports-job", "reports-list", "cmp-a", "cmp-b",
    "cmp-style", "btn-compare", "cmp-table", "local-detail",
    "log-file", "modal", "modal-title", "modal-body", "modal-ok", "modal-cancel",
    "install-cfg", "cfg-boxtype", "cfg-mate", "cfg-hosts", "cfg-master-me",
    "cfg-master-mate", "cfg-webgui", "cfg-license", "cfg-cnc-preview", "cfg-title",
    "not-installed", "install-failed", "install-fatal", "install-trace",
    "btn-install-resume", "btn-install-reset", "install-done", "btn-install-continue",
    "tab-btn-upgrade",
    "tab-btn-bootmenu", "tab-bootmenu", "bootmenu-status", "btn-bootmenu-open",
    "btn-bootmenu-end", "bootmenu-other", "btn-bootmenu-end-leftover", "bootmenu-term",
    "bootmenu-note", "bootmenu-loaderr",
]


class TestWebAssets(unittest.TestCase):
    def setUp(self):
        self.html = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
        self.css = open(os.path.join(WEB, "style.css"), encoding="utf-8").read()
        self.js = open(os.path.join(WEB, "app.js"), encoding="utf-8").read()

    def test_site_tab_merged_into_overview(self):
        self.assertNotIn('data-tab="site"', self.html)
        self.assertNotIn('id="tab-site"', self.html)
        overview = self.html.split('id="tab-overview"', 1)[1].split("</section>", 1)[0]
        for i in ("site-table", "local-detail", "about-grid", "links"):
            self.assertIn('id="%s"' % i, overview)

    def test_local_detail_toggle_persists_and_is_moved(self):
        self.assertIn("nfu_local_open", self.js)
        self.assertIn("function toggleLocal(", self.js)
        self.assertIn("tb.appendChild(detail)", self.js)

    def test_saved_tab_falls_back_to_overview(self):
        self.assertIn('names.indexOf(saved) >= 0 ? saved : "overview"', self.js)

    def test_depots_listed_automatically(self):
        self.assertIn(">Refresh depots</button>", self.html)
        self.assertNotIn("List depots", self.html)
        self.assertIn("function loadDepots(", self.js)
        # The top-level startup sequence (after the single initTabs() call),
        # not some function body that happens to call buildForm().
        startup = self.js.split("\ninitTabs();", 1)[1]
        self.assertIn("\nloadDepots();", startup)
        self.assertIn("esc(x.path)", self.js)

    def test_log_follows_trace_file_changes(self):
        self.assertIn('"&file=" + encodeURIComponent(logFile)', self.js)
        self.assertIn("if (d.reset)", self.js)

    def test_overview_shows_upgrade_logdir(self):
        self.assertIn('row("Upgrade logs"', self.js)

    def test_mode_switches_to_upgrade_after_stage_completes(self):
        self.assertIn("function suggestUpgradeMode(", self.js)
        fn = self.js.split("function suggestUpgradeMode(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('p.phase === "stage" && p.status === "done"', fn)
        self.assertIn("modeTouched", fn)
        self.assertIn("resetPreflight();", fn)
        self.assertIn("suggestUpgradeMode(p);", self.js.split("function refreshProgress(", 1)[1])
        self.assertIn("modeTouched = true", self.js)

    def test_counter_shows_one_based_step(self):
        fn = self.js.split("function refreshProgress(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('"Step " + (p.step ? p.step : "–") + " / " + p.total', fn)

    def test_progress_strip_is_large(self):
        strip = self.css.split(".progress-strip {", 1)[1].split("}", 1)[0]
        self.assertIn("font-size: 17px", strip)
        bar = self.css.split(".progress-strip .bar {", 1)[1].split("}", 1)[0]
        self.assertIn("height: 22px", bar)

    def test_confirmations_use_in_page_modal(self):
        self.assertNotIn("window.confirm", self.js)
        self.assertNotIn("alert(", self.js)
        self.assertIn("function showConfirm(", self.js)
        launch = self.js.split("function launch(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("showConfirm(", launch)
        fix = self.js.split("function runFix(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("showConfirm(", fix)
        show = self.js.split("function showConfirm(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('getElementById("modal-cancel").focus()', show)   # Enter doesn't launch
        self.assertIn('"Escape"', self.js)
        self.assertIn('role="dialog"', self.html)
        self.assertIn('aria-modal="true"', self.html)
        self.assertNotIn("inset:", self.css)   # older Firefox on RHEL 7

    def test_load_errors_are_visible_and_tabs_wired_first(self):
        self.assertIn("window.onerror = function", self.js.split("\nfunction ", 1)[0])
        first_binding = self.js.index('document.getElementById("cfg-load-depots").onclick')
        self.assertLess(self.js.index("\ninitTabs();"), first_binding)
        self.assertEqual(self.js.count("\ninitTabs();"), 1)
        self.assertIn('rel="icon"', self.html)

    def test_elements_used_by_app_js_come_before_the_script_tag(self):
        # app.js runs as soon as the parser reaches its <script> tag (no defer), so
        # every element it looks up at startup must already be in the document.
        before = self.html.split('<script src="/app.js">', 1)[0]
        used = set(re.findall(r'getElementById\("([^"]+)"\)', self.js))
        created = set(re.findall(r"id='([^']+)'", self.js)) | {"load-error"}
        late = sorted(i for i in used - created if ('id="%s"' % i) not in before)
        self.assertEqual(late, [], "defined after the app.js <script> tag: %s" % late)

    def test_log_tab_shows_followed_file(self):
        fn = self.js.split("function refreshLog(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('getElementById("log-file").textContent', fn)
        self.assertIn("d.path", fn)
        self.assertIn("(not created yet)", fn)

    def test_failed_check_output_is_toggleable(self):
        fn = self.js.split("function renderChecks(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("out-toggle", fn)
        self.assertIn("esc(r.output)", fn)
        self.assertIn('closest("button.fix")', fn)   # handler still dispatches Fix

    def test_checks_table_has_category_column(self):
        fn = self.js.split("function renderChecks(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("<th>Category</th>", fn)
        self.assertIn("esc(r.category", fn)

    def test_launch_label_while_install_runs(self):
        # A running install is "unfinished" too: it must read as running (and
        # Launch stay disabled), not offer "Resume install".
        fn = self.js.split("function renderInstall(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('"Install running\\u2026"', fn)   # the JS \u2026 escape
        self.assertIn("s.running", fn.split('"Install running', 1)[0].rsplit("setTextIfChanged", 1)[1])
        upd = self.js.split("function updateLaunchState(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("installRunning()", upd)

    def test_log_tab_shows_live_step_output(self):
        self.assertIn('id="log-live"', self.html)
        self.assertIn('"/api/log/live"', self.js)
        fn = self.js.split("function refreshLive(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("log-live", fn)
        self.assertIn("setInterval(refreshLive", self.js)

    def test_preview_banner_and_picker(self):
        self.assertIn('id="preview-banner"', self.html)
        for v in ("before", "running", "failed", "done"):
            self.assertIn('<option value="%s">' % v, self.html)
        fn = self.js.split("function renderPreview(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("s.preview", fn)
        self.assertIn('"/api/preview"', self.js)
        self.assertIn("renderPreview(s);", self.js.split("function refreshInstall(", 1)[1])

    def test_all_required_ids_present(self):
        missing = [i for i in REQUIRED_IDS if ('id="%s"' % i) not in self.html]
        self.assertEqual(missing, [], "index.html missing IDs: %s" % missing)

    def test_no_external_resources(self):
        for name, text in (("html", self.html), ("css", self.css)):
            self.assertNotIn("http://", text, name)
            self.assertNotIn("https://", text, name)
        self.assertNotIn("@import", self.css)

    def test_tabs_and_panels_match(self):
        tabs = set(re.findall(r'data-tab="([a-z]+)"', self.html))
        panels = set(re.findall(r'id="tab-([a-z]+)"', self.html))
        self.assertEqual(tabs, panels, "tab buttons and panels differ")

    def test_launch_posts_per_check_overrides(self):
        self.assertIn("overrides:", self.js)
        self.assertNotIn('id="override"', self.html)

    def test_reports_output_is_escaped(self):
        self.assertIn("function esc(", self.js)
        self.assertIn("esc(r.left)", self.js)
        self.assertIn("esc(r.right)", self.js)

    def test_raw_diff_links_are_escaped(self):
        self.assertIn('esc("/api/reports/raw?id="', self.js)

    def test_cmp_status_and_op_classes_are_escaped(self):
        self.assertIn("cmp-\" + esc(r.status)", self.js)
        self.assertIn("esc(r.op)", self.js)

    def test_compare_follows_newest_pair(self):
        self.assertIn("lastCompared", self.js)

    def test_style_change_rerenders_open_diffs_not_refetch(self):
        self.assertNotIn('"cmp-style").onchange = runCompare', self.js)
        self.assertIn('getElementById("cmp-style").onchange = function', self.js)

    def test_ab_change_clears_stale_diffs(self):
        self.assertIn("Selection changed", self.js)
        self.assertIn('lastCompared = ""', self.js)

    def test_launch_error_shown(self):
        self.assertIn("d.launch_error", self.js)
        self.assertIn("Launch failed: ", self.js)

    def test_busy_409_is_not_a_preflight_block(self):
        # Only a 409 carrying not_overridden is a pre-flight block; any other
        # 409 (snapshot busy, upgrade already running) must not disable Launch
        # or re-run pre-flight.
        self.assertIn("else if (status === 409 && body.not_overridden) {", self.js)
        self.assertIn('"Not launched: " + (body.error || status)', self.js)

    def test_default_pair_anchors_on_newest_before(self):
        # defaultPair() pairs the newest before with the after naming it (not
        # whichever after is newest, whose before may have been pruned).
        self.assertIn("snapshots[j].pair_of === before.id", self.js)
        self.assertNotIn("return [snapshots[i].pair_of, snapshots[i].id]", self.js)

    def test_interrupted_before_notice_shown(self):
        self.assertIn("else if (!reportsJob && d.notice) {", self.js)
        self.assertIn('getElementById("reports-job").textContent = d.notice', self.js)

    def test_interrupted_fix_notice_shown(self):
        rj = self._fn("refreshReportsJob")
        self.assertIn("d.interrupted_fix", rj)
        self.assertIn('getElementById("fix-msg")', rj)
        self.assertIn("was interrupted (service restarted)", rj)
        self.assertIn("re-run pre-flight to see its state.", rj)

    def test_preflight_fix_buttons(self):
        self.assertIn("esc(r.fix)", self.js)
        self.assertIn("function runFix(", self.js)
        self.assertIn('postJSON("/api/checks/fix"', self.js)
        self.assertIn("Run fix as root on ", self.js)
        fix_fn = self.js.split("function applyFix(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("runPreflight();", fix_fn)

    def _fn(self, name):
        return self.js.split("function %s(" % name, 1)[1].split("\nfunction ", 1)[0]

    def test_fix_disables_launch_and_check_until_done(self):
        fix_fn = self._fn("applyFix")
        self.assertIn('getElementById("btn-launch").disabled = true', fix_fn)
        self.assertIn('getElementById("btn-check").disabled = true', fix_fn)
        # re-enabled when the fix answers, whatever the answer
        done = fix_fn.split('postJSON("/api/checks/fix"', 1)[1]
        self.assertIn('getElementById("btn-check").disabled = false', done)
        self.assertIn("updateLaunchState();", done)

    def test_fix_result_honest(self):
        fix_fn = self._fn("applyFix")
        self.assertIn("d.fix.ok && d.result && d.result.passed", fix_fn)
        self.assertIn(": fix applied but the check still fails \u2014 ", fix_fn)

    def test_preflight_rerun_failure_unsticks_ui(self):
        pf = self._fn("runPreflight")
        self.assertIn("could not re-run checks (HTTP ", pf)
        self.assertIn('getElementById("btn-check").disabled = false', pf)
        self.assertIn("#preflight button.fix", pf)
        self.assertIn('getElementById("fix-msg")', pf)

    def test_post_json_survives_non_json_body(self):
        post = self._fn("postJSON")
        self.assertIn("try {", post)
        self.assertIn("JSON.parse(x.responseText)", post)
        self.assertIn('{ error: "HTTP " + x.status }', post)

    def test_mate_is_type_or_pick(self):
        # Mate host is a single type-or-pick box: a text input bound to a
        # datalist, not a locked-down <select>.
        self.assertIn('list="cfg-hosts"', self.html)
        self.assertIn('<datalist id="cfg-hosts">', self.html)

    def test_license_stub_disabled(self):
        m = re.search(r'<input[^>]*id="cfg-license"[^>]*>', self.html)
        self.assertIsNotNone(m, "no #cfg-license element in index.html")
        el = m.group(0)
        self.assertIn("disabled", el)
        self.assertIn("coming later", el)

    def test_install_form_wiring(self):
        for s in ("function applyHostMode(", "/api/install/status", "/api/install/hosts",
                  "mode: hostMode", "webguiTouched"):
            self.assertIn(s, self.js, "missing %r" % s)
        self.assertIn("setInterval(refreshInstall", self.js)

    def test_hard_checks_not_overridable(self):
        self.assertIn("r.hard", self._fn("renderChecks"))
        self.assertIn("hardFailures()", self._fn("updateLaunchState"))
        self.assertIn("r.hard && !r.passed", self._fn("hardFailures"))

    def test_reset_requires_reason_in_modal(self):
        self.assertIn("id='reset-reason'", self.js)
        self.assertIn('"/api/install/reset"', self.js)

    def test_done_banner_waits_for_continue(self):
        self.assertIn("installSeen", self.js)
        self.assertIn('"btn-install-continue"', self.js)
        self.assertIn("installSeen", self._fn("refreshInstall"))

    def test_resume_label(self):
        self.assertIn('"Resume install"', self.js)

    def test_install_poll_keeps_selection(self):
        # F1: the 2 s poll must not rewrite unchanged text (it would clear the
        # operator's selection of the trace path).
        fn = self._fn("renderInstall")
        for el in ("install-failed-step", "install-fatal", "install-trace"):
            self.assertIn('setTextIfChanged("{}"'.format(el), fn)
        self.assertNotIn(".textContent =", fn)
        helper = self._fn("setTextIfChanged")
        self.assertIn("!==", helper)

    def test_config_issues_escaped(self):
        # M1: issue text includes operator-typed values (MATEHOST).
        fn = self._fn("renderIssues")
        for part in ("esc(i.level)", "esc(i.field)", "esc(i.message)"):
            self.assertIn(part, fn)

    def test_bootmenu_tab_and_vendored_xterm(self):
        self.assertIn('<button class="tab" data-tab="bootmenu" id="tab-btn-bootmenu">'
                      'Boot menu</button>', self.html)
        # after the Log tab
        self.assertLess(self.html.index('data-tab="log"'), self.html.index('data-tab="bootmenu"'))
        for name in ("xterm.js", "xterm.css", "addon-fit.js", "LICENSE"):
            self.assertTrue(os.path.isfile(os.path.join(WEB, "xterm", name)), name)
        lic = open(os.path.join(WEB, "xterm", "LICENSE"), encoding="utf-8").read()
        self.assertTrue("MIT" in lic or "Permission is hereby granted" in lic)
        self.assertIn('<link rel="stylesheet" href="/xterm/xterm.css">', self.html)
        app = self.html.index('<script src="/app.js">')
        self.assertLess(self.html.index('<script src="/xterm/xterm.js"></script>'), app)
        self.assertLess(self.html.index('<script src="/xterm/addon-fit.js"></script>'), app)
        panel = self.html.split('id="tab-bootmenu"', 1)[1].split("</section>", 1)[0]
        self.assertIn(">Open boot menu</button>", panel)
        self.assertIn(">End session</button>", panel)
        self.assertIn(">End it</button>", panel)
        self.assertIn("Restarting this tool ends the boot menu session.", panel)
        self.assertIn("The terminal could not load in this browser "
                      "(Firefox 115 or newer is needed).", panel)
        self.assertNotIn("<iframe", self.html)

    def test_bootmenu_glue(self):
        for s in ("function openBootMenu(", '"/ws/bootmenu"', '"/api/bootmenu/status"',
                  'typeof Terminal === "undefined"', '"/api/bootmenu/end"',
                  '"/api/bootmenu/end-leftover"', "new FitAddon.FitAddon()",
                  "setInterval(refreshBootMenu"):
            self.assertIn(s, self.js, "missing %r" % s)
        refresh = self._fn("refreshBootMenu")
        self.assertIn("greyed", refresh)
        self.assertIn('d.other === "leftover"', self._fn("renderBootMenu"))
        end = self._fn("endBootMenu")
        self.assertIn("showConfirm(", end)
        self.assertIn("A boot or shutdown appears to be in progress. Ending it now can leave "
                      "netFLEX half up or half down.", self.js)
        # a replayed scrollback starts from a clean terminal
        self.assertIn("term.reset()", self.js)
        # detached: no automatic reconnect, say why
        self.assertIn('"Opened in another tab"', self.js)

    def test_bootmenu_ended_scrollback_viewable(self):
        # Showing the tab attaches to an ended session too (replay + "Session
        # ended"), without starting a new one; Open then starts it.
        self.assertIn("bm.showAttach = true", self._fn("bootMenuShown"))
        refresh = self._fn("refreshBootMenu")
        self.assertIn('bm.state === "ended" && bm.showAttach', refresh)
        self.assertIn("bm.wantStart = false", refresh)
        readme = " ".join(open(README, encoding="utf-8").read().split())
        self.assertIn("Showing the tab again reconnects", readme)
        self.assertIn("output of an ended session, which stays viewable", readme)

    def test_bootmenu_detached_cleared_when_not_running(self):
        self.assertIn('bm.detached && bm.state !== "running"', self._fn("refreshBootMenu"))

    def test_bootmenu_end_wording_is_exact(self):
        # The server kills only the menu's own processes; what the menu started
        # is not killed. Nothing may claim otherwise.
        readme = " ".join(open(README, encoding="utf-8").read().split())
        for text in (self.js, readme):
            self.assertNotIn("anything it is running", text)
            self.assertNotIn("anything the menu is running", text)
            self.assertNotIn("whatever it is running", text)
        self.assertIn("is not killed and may keep running", self.js)
        self.assertIn("is not killed and may keep running", readme)

    def test_bootmenu_end_status_error_shown(self):
        end = self._fn("endBootMenu")
        self.assertIn("}, function (status) {", end)
        self.assertIn("not ended", end)

    def test_bootmenu_loaderr_shown_without_a_click(self):
        render = self._fn("renderBootMenu")
        self.assertIn("xtermMissing()", render)
        self.assertIn('"bootmenu-loaderr"', render)
        self.assertIn('typeof Terminal === "undefined"', self._fn("xtermMissing"))

    def test_es5_only(self):
        # ES5-only per project constraints: no arrow functions, let/const, or
        # template literals in the shipped JS.
        self.assertNotRegex(self.js, r"=>")
        self.assertNotRegex(self.js, r"\blet\b")
        self.assertNotRegex(self.js, r"\bconst\b")
        self.assertNotIn("`", self.js)


if __name__ == "__main__":
    unittest.main()
