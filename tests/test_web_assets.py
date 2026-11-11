import json
import os
import re
import shutil
import subprocess
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
    "tab-license", "lic-status", "btn-lic-create", "btn-lic-migrate", "lic-main",
    "lic-expiry", "lic-trial", "lic-numne", "lic-numusers", "lic-types",
    "lic-type-filter", "lic-advanced", "lic-password", "btn-lic-unlock",
    "btn-lic-review", "btn-lic-save", "lic-review", "lic-result",
    "tab-btn-patches", "tab-patches", "patch-installed", "patch-banner",
    "patch-table", "btn-patch-refresh", "patch-msg", "patch-last",
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

    def test_overview_dbcheck_unknown_is_not_shown_as_blank(self):
        # /api/info reports dbcheck_errors null when dbcheck did not complete.
        self.assertIn('i.app_detail.dbcheck_errors === null ? "unknown (dbcheck did not complete)"',
                      self.js)

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

    def test_license_repair_box(self):
        for i in ("lic-repair", "lic-repair-expiry", "lic-repair-password", "btn-lic-repair",
                  "lic-repair-msg"):
            self.assertIn('id="%s"' % i, self.html)
        self.assertIn('id="lic-repair-password" type="password"', self.html)
        fn = self.js.split("function repairLicense(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('"/api/license/repair"', fn)
        self.assertIn("showConfirm(", fn)
        clear = self.js.split("function clearLicensePassword(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('"lic-repair-password"', clear)
        self.assertIn('d.state === "needs_repair"', self.js.split("function renderLicense(", 1)[1])

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

    def test_install_form_links_license_tab(self):
        # The old "coming later" stub is now a link that opens the License tab.
        self.assertNotIn("coming later", self.html)
        m = re.search(r'<button[^>]*id="cfg-license"[^>]*>([^<]*)</button>', self.html)
        self.assertIsNotNone(m, "#cfg-license is not a button")
        self.assertIn("Set up the license on the License tab", m.group(1))
        self.assertIn('getElementById("cfg-license").onclick', self.js)
        handler = self.js.split('getElementById("cfg-license").onclick', 1)[1].split("};", 1)[0]
        self.assertIn('showTab("license")', handler)

    def test_patches_tab_after_upgrade(self):
        m = re.search(r'data-tab="upgrade"[^>]*>[^<]*</button>\s*<button[^>]*data-tab="patches"',
                      self.html)
        self.assertIsNotNone(m, "Patches tab button must follow the Upgrade tab")
        self.assertIn('id="tab-btn-patches"', self.html)
        self.assertIn('name === "patches"', self._fn("initTabs"))

    def test_patches_wiring_and_copy(self):
        self.assertIn("function refreshPatches(", self.js)
        self.assertIn('"/api/patches/install"', self.js)
        self.assertIn("netFLEX stays up, but its processes will be restarted", self.js)
        self.assertIn("This patch requires netFLEX to be stopped — use the Boot menu tab", self.js)
        self.assertIn('showTab("log")', self._fn("patchInstalled"))
        self.assertIn("esc(", self._fn("renderPatches"))

    def test_license_tab_button_and_panel(self):
        self.assertIn('<button class="tab" data-tab="license">License</button>', self.html)
        panel = self.html.split('id="tab-license"', 1)[1].split("</section>", 1)[0]
        self.assertIn('<details id="lic-advanced"', panel)
        exp = re.search(r'<input[^>]*id="lic-expiry"[^>]*>', panel).group(0)
        self.assertIn('placeholder="MM/DD/YYYY"', exp)
        self.assertIn('type="checkbox"', re.search(r'<input[^>]*id="lic-trial"[^>]*>', panel).group(0))
        save = re.search(r'<button[^>]*id="btn-lic-save"[^>]*>', panel).group(0)
        self.assertIn("disabled", save)
        self.assertIn('<input', re.search(r'<input[^>]*id="lic-type-filter"[^>]*>', panel).group(0))

    def test_license_password_not_persisted(self):
        pw = re.search(r'<input[^>]*id="lic-password"[^>]*>', self.html)
        self.assertIsNotNone(pw)
        self.assertIn('type="password"', pw.group(0))
        self.assertIn('autocomplete="off"', pw.group(0))
        # No localStorage/sessionStorage near the password, and never in a URL.
        for line in self.js.splitlines():
            if "lic-password" in line or "licPassword" in line:
                self.assertNotIn("localStorage", line)
                self.assertNotIn("sessionStorage", line)
                self.assertNotIn("encodeURIComponent", line)
        self.assertNotIn("sessionStorage", self.js)
        lic = self.js.split("// ---- License tab", 1)[1].split("// ---- end License tab", 1)[0]
        self.assertNotIn("localStorage", lic)
        self.assertNotIn("getJSON(\"/api/license?", lic)
        # Cleared after Save completes and when the tab is left.
        self.assertIn("clearLicensePassword()", self._fn("licenseSaved"))
        self.assertIn("clearLicensePassword()", self._fn("initTabs"))
        self.assertIn('getElementById("lic-password").value = ""', self._fn("clearLicensePassword"))

    def test_license_repolled_while_not_editable(self):
        self.assertIn("setInterval(pollLicense, 5000)", self.js)
        fn = self._fn("pollLicense")
        self.assertIn('"tab-license"', fn)
        self.assertIn("lic.editable", fn)
        self.assertIn("licBusy", fn)
        self.assertIn("refreshLicense()", fn)

    def test_repair_checks_date_before_confirm(self):
        fn = self._fn("repairLicense")
        self.assertLess(fn.index("expiryError(exp)"), fn.index("showConfirm("))
        self.assertIn("is not a real date", self._fn("expiryError"))

    def test_license_flow_wiring(self):
        for s in ('"/api/license/unlock"', '"/api/license/review"', '"/api/license/save"',
                  '"/api/license/create"', '"/api/license/migrate"', 'getJSON("/api/license"'):
            self.assertIn(s, self.js, "missing %r" % s)
        save = self._fn("saveLicense")
        self.assertIn("showConfirm(", save)
        self.assertIn('postJSON("/api/license/save"', save)
        self.assertIn("refreshLicense()", self._fn("licenseSaved"))
        self.assertIn('name === "license"', self._fn("initTabs"))
        self.assertIn("refreshLicense()", self._fn("initTabs"))
        # Any edit invalidates the last review (Save goes back to disabled).
        inv = self._fn("invalidateLicenseReview")
        self.assertIn('getElementById("btn-lic-save").disabled = true', inv)
        # Server text is escaped.
        rev = self._fn("renderLicenseReview")
        for part in ("esc(c.label)", "esc(w)", "esc(e.message)"):
            self.assertIn(part, rev)
        # ne_on is sent only once Unlock has loaded the type list.
        req = self._fn("licenseRequest")
        self.assertIn("licTypes ? ", req)
        # Only fields changed from the value first shown are sent.
        self.assertIn("v === licInitial[f.key]) return;", req)

    def _node(self, src):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        # Script on stdin as UTF-8 bytes: argv cannot carry it under LC_ALL=C.
        r = subprocess.run([node], input=src.encode("utf-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        return json.loads(r.stdout.decode("utf-8"))

    def test_license_request_sends_only_edited_fields(self):
        # M7: an untouched odd value (here NumNE "abc", Text "") is not sent.
        fns = "".join("\nfunction %s(%s" % (n, self._fn(n)) for n in
                      ("licFieldEl", "licFieldValue", "licNumber", "licenseRequest"))
        src = """
var els = {"lic-numne": {value: "abc"}, "lic-numusers": {value: "7"},
           "lic-trial": {checked: false}, "lic-f-Text": {value: ""},
           "lic-f-NumCusers": {value: "3"},
           "lic-password": {value: "pw"}, "lic-expiry": {value: ""}};
var document = {getElementById: function (id) { return els[id] || null; }};
var LIC_MAIN_IDS = {NumNE: "lic-numne", NumUsers: "lic-numusers", Trial: "lic-trial"};
var licTypes = null;
var lic = {license: {NumNE: "abc", NumUsers: 7, Trial: false, Text: ""},
           fields: [{key: "NumNE", kind: "int"}, {key: "NumUsers", kind: "int"},
                    {key: "Trial", kind: "bool"}, {key: "Text", kind: "str"},
                    {key: "NumCusers", kind: "int"}]};
var licInitial = {NumNE: "abc", NumUsers: "7", Trial: false, Text: "", NumCusers: "3"};
%s
var out = [];
out.push(licenseRequest().values);
els["lic-numusers"].value = "9"; els["lic-trial"].checked = true;
out.push(licenseRequest().values);
process.stdout.write(JSON.stringify(out));
""" % fns
        out = self._node(src)
        self.assertEqual(out[0], {})
        self.assertEqual(out[1], {"NumUsers": 9, "Trial": True})

    def test_license_saved_heading(self):
        # F1: nothing applied -> "Nothing was saved", not "Not everything".
        src = """
var els = {"lic-password": {value: "x"}, "lic-repair-password": {value: "y"},
            "lic-msg": {}, "lic-result": {}};
var document = {getElementById: function (id) { return els[id]; }};
function setLicenseBusy() {}
function refreshLicense() {}
function esc(s) { return String(s); }
%s
var out = [];
licenseSaved(200, {ok: false, applied: [], not_applied: ["NumNE"], error: "e"});
out.push(els["lic-result"].innerHTML);
licenseSaved(200, {ok: false, applied: ["NumUsers"], not_applied: ["NumNE"], error: "e"});
out.push(els["lic-result"].innerHTML);
process.stdout.write(JSON.stringify(out));
""" % ("\nfunction clearLicensePassword(" + self._fn("clearLicensePassword") +
       "\nfunction licenseSaved(" + self._fn("licenseSaved"))
        out = self._node(src)
        self.assertIn("Nothing was saved", out[0])
        self.assertNotIn("Not everything", out[0])
        self.assertIn("Not everything was saved", out[1])

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

    def test_bootmenu_replay_chunks_and_drop_limit(self):
        # M3: the replay comes in several frames; only the first resets the
        # terminal. A socket that keeps dropping mid-replay stops reattaching
        # by itself after the third drop in a row (a click is required).
        frame = self._fn("bootMenuFrame")
        self.assertIn("if (m.replay_start)", frame)
        self.assertNotIn("if (m.replay) bm.term.reset()", frame)
        self.assertIn("bm.replayDrops = 0", frame)
        connect = self._fn("connectBootMenu")
        self.assertIn("bm.inReplay", connect)
        self.assertIn("bm.replayDrops > 2", connect)
        self.assertIn("bm.autoFailed = true", connect)
        self.assertIn("bm.replayDrops = 0", self._fn("openBootMenu"))

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
