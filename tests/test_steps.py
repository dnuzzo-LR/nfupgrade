import unittest
from nfupgrader import steps


class TestSteps(unittest.TestCase):
    def test_stage_has_six_states_in_order(self):
        self.assertEqual(
            steps.states_for("stage"),
            ["swsstart", "swspatchinstall", "swswebinstall",
             "swscopycnc", "swsretropass1", "swscomplete"],
        )

    def test_upgrade_has_nine_states_in_order(self):
        self.assertEqual(
            steps.states_for("upgrade"),
            ["swscomplete", "swistart", "swicopycnc", "swipackages",
             "swipreboot", "swiretropass2", "swibootinc", "postinstall", "swidone"],
        )

    def test_total(self):
        self.assertEqual(steps.total("stage"), 6)
        self.assertEqual(steps.total("upgrade"), 9)

    def test_index_of_known_state(self):
        self.assertEqual(steps.index_of("stage", "swscopycnc"), 3)

    def test_index_of_unknown_state_is_minus_one(self):
        self.assertEqual(steps.index_of("stage", "bogus"), -1)

    def test_label_is_human_readable(self):
        self.assertIn("CORE", steps.label("swscoreupgrade") + steps.label("swsstart") + steps.label("swscopycnc"))

    def test_unknown_phase_raises(self):
        with self.assertRaises(KeyError):
            steps.states_for("nope")


if __name__ == "__main__":
    unittest.main()
