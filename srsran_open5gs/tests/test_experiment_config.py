import json
import pathlib
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

from experiment_framework.config import ConfigError  # noqa: E402
from experiment_framework.config import apply_propagation  # noqa: E402
from experiment_framework.config import load_and_resolve_study  # noqa: E402
from experiment_framework.modes import study_plan  # noqa: E402


PILOT = REPO_ROOT / "experiments/studies/channel-evaluation-pilot.json"
EXPECTED_RESULT_ROOT = str(REPO_ROOT.parent / "results" / "evaluation")


class ExperimentConfigTests(unittest.TestCase):
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

    def test_noise_sweep_requires_profile(self):
        self._assert_condition_rejected(
            lambda c: c.update({"noise": {"enabled": True}}),
            "noise.profile",
        )

    def test_propagation_effects_default_to_false(self):
        scene = {"solver": {"specular_reflection": True, "los": True, "max_depth": 3}}
        merged = apply_propagation(scene, {"diffraction": True})
        # The condition controls enabled effects
        self.assertTrue(merged["solver"]["diffraction"])
        # Omitted effects resolve to False
        self.assertFalse(merged["solver"]["specular_reflection"])
        self.assertFalse(merged["solver"]["los"])
        # Solver tuning is preserved
        self.assertEqual(merged["solver"]["max_depth"], 3)
        # Original scene is not mutated
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

    def test_amf_safety_policy_is_enforced(self):
        resolved = load_and_resolve_study(PILOT)
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

    def test_channel_stream_defaults_are_resolved(self):
        resolved = load_and_resolve_study(PILOT)
        channel = resolved["parameters"]["channel"]
        self.assertEqual(channel["control_endpoint"], "tcp://127.0.0.1:5555")
        self.assertEqual(channel["stream_endpoint"], "tcp://127.0.0.1:5556")
        self.assertEqual(channel["port_forward"], "5555:5555")
        self.assertEqual(channel["port_forward_stream"], "5556:5556")
        for condition in resolved["conditions"]:
            self.assertEqual(condition["port_forward"], "5555:5555")
            self.assertEqual(condition["port_forward_stream"], "5556:5556")
            self.assertEqual(condition["stream_endpoint"], "tcp://127.0.0.1:5556")
        self.assertIn("5555:5555, 5556:5556", json.dumps(study_plan(resolved)))

    def test_plan_has_no_per_trial_complete_baseline(self):
        resolved = load_and_resolve_study(PILOT)
        text = json.dumps(study_plan(resolved))
        self.assertIn("restore deployment and validate only", text)
        self.assertNotIn("complete baseline after every trial", text)


if __name__ == "__main__":
    unittest.main()
