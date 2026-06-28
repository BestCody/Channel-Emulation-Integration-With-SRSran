import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class Stage8RunnerDesignTests(unittest.TestCase):
    def test_live_execution_requires_explicit_confirmation(self):
        source = (REPO_ROOT / "bin/stage8-experiment.py").read_text()
        self.assertIn("--confirm-live", source)
        self.assertIn("run requires --confirm-live", source)

    def test_runner_has_immediate_failed_condition_baseline_and_final_baseline(self):
        source = (REPO_ROOT / "experiment_framework/runner.py").read_text()
        self.assertIn("failure-recovery", source)
        self.assertIn("post-pilot-baseline", source)
        self.assertIn("pre-pilot-baseline", source)
        self.assertIn("without_safety_checks", source)
        self.assertIn("self.deployment_changed = True", source)

    def test_runner_uses_port_forward_and_no_nodeport(self):
        source = (REPO_ROOT / "experiment_framework/runner.py").read_text()
        self.assertIn('"port-forward"', source)
        self.assertNotIn('"NodePort"', source)

    def test_generated_result_root_is_outside_repository(self):
        study = (REPO_ROOT / "experiments/studies/stage8-pilot.json").read_text()
        self.assertIn('/home/h3lou/sionna-srsran/results/stage8', study)
        self.assertNotIn(str(REPO_ROOT / "results"), study)


if __name__ == "__main__":
    unittest.main()
