import os
import shutil
import tempfile
import unittest
from nfupgrader import bootmenu


def mkproc(root, pid, ppid, argv, name="x"):
    """Create a fake /proc/<pid> entry: stat (name may embed spaces/parens,
    ppid is field 4 after the LAST ')') and cmdline (NUL-separated)."""
    d = os.path.join(root, str(pid))
    os.makedirs(d)
    with open(os.path.join(d, "stat"), "w") as fh:
        fh.write("{} ({}) S {} 0 0 0 0 0\n".format(pid, name, ppid))
    with open(os.path.join(d, "cmdline"), "wb") as fh:
        fh.write("\0".join(argv).encode("ascii") + b"\0")


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        # 10: ksh .../boot_nms ; 11: vi boot_nms (not a menu) ; 12: /usr/cnc/bin/new_boot_nms
        mkproc(self.root, 10, 12, ["/usr/bin/ksh", "/usr/cnc/bin/boot_nms"])
        mkproc(self.root, 11, 1, ["vi", "boot_nms"])
        mkproc(self.root, 12, 1, ["/usr/cnc/bin/new_boot_nms"])

    def test_default_argv_never_a_shell(self):
        a = bootmenu.default_argv()
        self.assertEqual(a[:5], ["su", "cmm", "-s", "/bin/sh", "-c"])
        self.assertIn("exec /usr/cnc/bin/new_boot_nms", a[5])
        self.assertTrue(a[5].rstrip().endswith("exec /usr/cnc/bin/new_boot_nms"))

    def test_list_and_menu_detection(self):
        procs = bootmenu.list_processes(self.root)
        menus = [p["pid"] for p in procs if bootmenu.is_menu_argv(p["argv"])]
        self.assertEqual(sorted(menus), [10, 12])
        self.assertEqual(bootmenu.descendants(procs, 12), {10})  # 10's ppid is 12

    def test_list_processes_handles_name_with_parens_and_spaces(self):
        mkproc(self.root, 20, 1, ["weird"], name="my (odd) proc name")
        procs = bootmenu.list_processes(self.root)
        p20 = [p for p in procs if p["pid"] == 20][0]
        self.assertEqual(p20["ppid"], 1)

    def test_list_processes_skips_unreadable_or_vanished(self):
        # A pid directory with no stat/cmdline (simulating a vanished process).
        os.makedirs(os.path.join(self.root, "999"))
        procs = bootmenu.list_processes(self.root)
        self.assertNotIn(999, [p["pid"] for p in procs])
        # Non-numeric entries under /proc are ignored.
        os.makedirs(os.path.join(self.root, "not_a_pid"))
        procs = bootmenu.list_processes(self.root)
        self.assertEqual(sorted(p["pid"] for p in procs), [10, 11, 12])

    def test_classify(self):
        procs = bootmenu.list_processes(self.root)
        pf = os.path.join(self.root, "pidfile")
        with open(pf, "w") as fh:
            fh.write("999 12\n")

        self.assertEqual(
            bootmenu.classify_others(procs, {10, 12}, pf, lambda p: True),
            ("none", []))
        self.assertEqual(
            bootmenu.classify_others(procs, set(), pf, lambda p: False),
            ("leftover", [10, 12]))
        self.assertEqual(
            bootmenu.classify_others(procs, set(), pf, lambda p: True),
            ("foreign", [10, 12]))
        self.assertEqual(
            bootmenu.classify_others(procs, set(), "/nonexistent", lambda p: False),
            ("foreign", [10, 12]))


if __name__ == "__main__":
    unittest.main()
