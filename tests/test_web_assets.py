import os
import re
import unittest

WEB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "nfupgrader", "web")

REQUIRED_IDS = [
    "host", "app-badge", "phase", "status", "bar-fill", "counter", "current",
    "about-grid", "links", "cfg-load-depots", "cfg-depot", "cfg-depot-msg", "cfg-form",
    "cfg-preview", "cfg-save", "cfg-msg", "cfg-rendered", "cfg-issues",
    "mode", "btn-check", "btn-launch", "launch-msg", "preflight", "fix-msg", "override-box",
    "override-msg", "reason", "postflight", "site-table", "min-sev", "log",
    "btn-reports-run", "reports-job", "reports-list", "cmp-a", "cmp-b",
    "cmp-style", "btn-compare", "cmp-table", "local-detail",
    "log-file", "modal", "modal-title", "modal-body", "modal-ok", "modal-cancel",
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
        startup = self.js.split("buildForm();", 1)[1]
        self.assertIn("loadDepots();", startup)
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

    def test_es5_only(self):
        # ES5-only per project constraints: no arrow functions, let/const, or
        # template literals in the shipped JS.
        self.assertNotRegex(self.js, r"=>")
        self.assertNotRegex(self.js, r"\blet\b")
        self.assertNotRegex(self.js, r"\bconst\b")
        self.assertNotIn("`", self.js)


if __name__ == "__main__":
    unittest.main()
