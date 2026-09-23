import os
import re
import unittest

WEB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "nfupgrader", "web")

REQUIRED_IDS = [
    "host", "app-badge", "phase", "status", "bar-fill", "counter", "current",
    "about-grid", "links", "cfg-load-depots", "cfg-depot", "cfg-form",
    "cfg-preview", "cfg-save", "cfg-msg", "cfg-rendered", "cfg-issues",
    "mode", "btn-check", "btn-launch", "launch-msg", "preflight", "override-box",
    "override-msg", "reason", "postflight", "site-table", "min-sev", "log",
    "btn-reports-run", "reports-job", "reports-list", "cmp-a", "cmp-b",
    "cmp-style", "btn-compare", "cmp-table",
]


class TestWebAssets(unittest.TestCase):
    def setUp(self):
        self.html = open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
        self.css = open(os.path.join(WEB, "style.css"), encoding="utf-8").read()
        self.js = open(os.path.join(WEB, "app.js"), encoding="utf-8").read()

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

    def test_es5_only(self):
        # ES5-only per project constraints: no arrow functions, let/const, or
        # template literals in the shipped JS.
        self.assertNotRegex(self.js, r"=>")
        self.assertNotRegex(self.js, r"\blet\b")
        self.assertNotRegex(self.js, r"\bconst\b")
        self.assertNotIn("`", self.js)


if __name__ == "__main__":
    unittest.main()
