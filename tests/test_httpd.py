import unittest
from nfupgrader import httpd


class TestHelpers(unittest.TestCase):
    def test_parse_cookies(self):
        self.assertEqual(httpd.parse_cookies("nfu_sid=abc; other=1")["nfu_sid"], "abc")

    def test_parse_cookies_empty(self):
        self.assertEqual(httpd.parse_cookies(""), {})

    def test_client_ip_prefers_socket(self):
        self.assertEqual(httpd.client_ip_from(("10.1.1.9", 5555)), "10.1.1.9")


class TestUrlHost(unittest.TestCase):
    def test_override_wins(self):
        self.assertEqual(httpd.resolve_url_host("0.0.0.0", "r9dev19", "h"), "r9dev19")

    def test_wildcard_bind_uses_hostname(self):
        self.assertEqual(httpd.resolve_url_host("0.0.0.0", None, "r9dev19"), "r9dev19")

    def test_empty_bind_uses_hostname(self):
        self.assertEqual(httpd.resolve_url_host("", None, "r9dev19"), "r9dev19")

    def test_specific_bind_used_verbatim(self):
        self.assertEqual(httpd.resolve_url_host("127.0.0.1", None, "h"), "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
