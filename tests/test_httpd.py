import unittest
from nfupgrader import httpd


class TestHelpers(unittest.TestCase):
    def test_parse_cookies(self):
        self.assertEqual(httpd.parse_cookies("nfu_sid=abc; other=1")["nfu_sid"], "abc")

    def test_parse_cookies_empty(self):
        self.assertEqual(httpd.parse_cookies(""), {})

    def test_client_ip_prefers_socket(self):
        self.assertEqual(httpd.client_ip_from(("10.1.1.9", 5555)), "10.1.1.9")


if __name__ == "__main__":
    unittest.main()
