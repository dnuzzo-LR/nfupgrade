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
]


class TestWebAssets(unittest.TestCase):
    def setUp(self):
        self.html = open(os.path.join(WEB, "index.html")).read()
        self.css = open(os.path.join(WEB, "style.css")).read()
        self.js = open(os.path.join(WEB, "app.js")).read()

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


if __name__ == "__main__":
    unittest.main()
