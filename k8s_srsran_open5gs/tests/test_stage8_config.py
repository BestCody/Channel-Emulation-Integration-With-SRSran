import json
import pathlib
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

from experiment_framework.config import ConfigError  # noqa: E402
from experiment_framework.config import MODES  # noqa: E402
from experiment_framework.config import load_and_resolve_study  # noqa: E402
from experiment_framework.modes import study_plan  # noqa: E402


PILOT = REPO_ROOT / "experiments/studies/stage8-pilot.json"
EXPECTED_RESULT_ROOT = str(REPO_ROOT.parent / "results" / "stage8")


class Stage8ConfigTests(unittest.TestCase):
    def test_pilot_resolves_exactly_one_trial_per_mode(self):
        resolved = load_and_resolve_study(PILOT, resolved_at="2026-06-22T00:00:00+00:00")
        self.assertTrue(resolved["pilot"])
        self.assertEqual(resolved["trials_per_condition"], 1)
        self.assertEqual(resolved["trial_count"], 6)
        self.assertEqual({item["mode"] for item in resolved["conditions"]}, MODES)
        self.assertEqual(resolved["conditions"][0]["condition_id"], "baseline")
        self.assertEqual(
            resolved["result_root"],
            EXPECTED_RESULT_ROOT,
        )

    def test_corrected_baseline_and_amf_policy_is_enforced(self):
        resolved = load_and_resolve_study(PILOT)
        baseline = resolved["baseline_policy"]
        self.assertEqual(baseline["before_pilot"], "complete")
        self.assertEqual(baseline["after_successful_condition"], "restoration-validation-only")
        self.assertEqual(baseline["after_failed_condition"], "immediate-complete-baseline-and-stop")
        self.assertEqual(baseline["after_pilot"], "complete")
        amf = resolved["amf_safety"]
        self.assertTrue(amf["continuous"])
        self.assertEqual(amf["stop_at_limit_fraction"], 0.90)
        self.assertEqual(amf["stop_at_growth_bytes"], 128 * 1024 * 1024)

    def test_all_conditions_defer_throughput_and_resolve_checksums(self):
        resolved = load_and_resolve_study(PILOT)
        for condition in resolved["conditions"]:
            self.assertEqual(condition["throughput"]["status"], "deferred")
            self.assertIn("verified", condition["throughput"]["reason"])
            self.assertTrue(condition["configuration"]["sha256"])
            self.assertTrue(condition["measurement_profile_resolved"]["configuration"]["sha256"])

    def test_fixed_attenuation_is_not_claimed_as_an_snr_test(self):
        resolved = load_and_resolve_study(PILOT)
        fixed = next(item for item in resolved["conditions"] if item["mode"] == "fixed_attenuation")
        self.assertIn("without added noise", fixed["interpretation"])
        self.assertIn("receiver compensation", fixed["interpretation"])

    def test_invalid_result_root_is_rejected(self):
        study = json.loads(PILOT.read_text())
        study["result_root"] = str(REPO_ROOT / "results")
        study["conditions"] = [
            str((PILOT.parent / reference).resolve())
            for reference in study["conditions"]
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "study.json"
            path.write_text(json.dumps(study))
            with self.assertRaisesRegex(ConfigError, "outside the Git repository"):
                load_and_resolve_study(path)

    def test_pilot_single_trial_enforcement_is_parameterized(self):
        study = json.loads(PILOT.read_text())
        study["trials_per_condition"] = 2
        study["conditions"] = [
            str((PILOT.parent / reference).resolve())
            for reference in study["conditions"]
        ]
        # enforced pilots reject more than one trial
        study["parameters"] = {"study": {"enforce_pilot_single_trial": True}}
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "study.json"
            path.write_text(json.dumps(study))
            with self.assertRaisesRegex(ConfigError, "pilot trial count"):
                load_and_resolve_study(path)

    def test_multi_trial_study_is_allowed_when_enforcement_is_off(self):
        study = json.loads(PILOT.read_text())
        study["trials_per_condition"] = 5
        study["conditions"] = [
            str((PILOT.parent / reference).resolve())
            for reference in study["conditions"]
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "study.json"
            path.write_text(json.dumps(study))
            resolved = load_and_resolve_study(path)
            self.assertEqual(resolved["trials_per_condition"], 5)

    def test_plan_has_no_per_trial_complete_baseline(self):
        resolved = load_and_resolve_study(PILOT)
        text = json.dumps(study_plan(resolved))
        self.assertIn("restore deployment and validate only", text)
        self.assertNotIn("complete baseline after every trial", text)


if __name__ == "__main__":
    unittest.main()
