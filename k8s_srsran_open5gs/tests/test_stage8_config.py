import json
import pathlib
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

from experiment_framework.config import ConfigError  # noqa: E402
from experiment_framework.config import apply_propagation  # noqa: E402
from experiment_framework.config import load_and_resolve_study  # noqa: E402
from experiment_framework.modes import study_plan  # noqa: E402


PILOT = REPO_ROOT / "experiments/studies/stage8-pilot.json"
EXPECTED_RESULT_ROOT = str(REPO_ROOT.parent / "results" / "stage8")


class Stage8ConfigTests(unittest.TestCase):
    def test_pilot_resolves_one_trial_per_condition(self):
        resolved = load_and_resolve_study(PILOT, resolved_at="2026-06-22T00:00:00+00:00")
        self.assertTrue(resolved["pilot"])
        self.assertEqual(resolved["trials_per_condition"], 1)
        self.assertEqual(resolved["trial_count"], len(resolved["conditions"]))
        for condition in resolved["conditions"]:
            self.assertTrue(condition["scene"])
            self.assertIn(condition["mobility"], {"static", "moving"})
        self.assertEqual(
            resolved["result_root"],
            EXPECTED_RESULT_ROOT,
        )

    def test_condition_requires_a_scene(self):
        self._assert_condition_rejected(lambda c: c.pop("scene"), "requires a scene")

    def test_moving_condition_requires_a_trajectory(self):
        def mutate(condition):
            condition["mobility"] = "moving"
            condition.pop("trajectory", None)
        self._assert_condition_rejected(mutate, "requires a trajectory", condition_id="static-reflections-on")

    def test_unknown_propagation_key_is_rejected(self):
        self._assert_condition_rejected(
            lambda c: c.setdefault("propagation", {}).update({"reflections": True}),
            "unknown propagation keys",
        )

    def test_noise_sweep_requires_profile_and_calibration(self):
        self._assert_condition_rejected(
            lambda c: c.update({"noise": {"enabled": True}}),
            "noise.profile and noise.calibration",
        )

    def test_propagation_overrides_scene_solver(self):
        scene = {"solver": {"specular_reflection": True, "los": True}}
        merged = apply_propagation(scene, {"specular_reflection": False})
        self.assertFalse(merged["solver"]["specular_reflection"])
        self.assertTrue(merged["solver"]["los"])
        # original scene is not mutated
        self.assertTrue(scene["solver"]["specular_reflection"])

    def _assert_condition_rejected(self, mutate, message, condition_id="static-reflections-on"):
        study = json.loads(PILOT.read_text())
        study["conditions"] = [
            str((PILOT.parent / reference).resolve())
            for reference in study["conditions"]
        ]
        target = next(path for path in study["conditions"] if condition_id in path)
        condition = json.loads(pathlib.Path(target).read_text())
        mutate(condition)
        with tempfile.TemporaryDirectory() as directory:
            override = pathlib.Path(directory) / "condition.json"
            override.write_text(json.dumps(condition))
            study["conditions"] = [str(override)]
            study_path = pathlib.Path(directory) / "study.json"
            study_path.write_text(json.dumps(study))
            with self.assertRaisesRegex(ConfigError, message):
                load_and_resolve_study(study_path)

    def test_restore_and_amf_policy_is_enforced(self):
        resolved = load_and_resolve_study(PILOT)
        baseline = resolved["baseline_policy"]
        self.assertEqual(baseline["after_successful_condition"], "restoration-validation-only")
        self.assertEqual(baseline["after_failed_condition"], "recovery-check-and-stop")
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
