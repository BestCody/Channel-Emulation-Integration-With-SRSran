import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from experiment_framework.runner import _port_forward_mappings  # noqa: E402


class ExperimentRunnerDesignTests(unittest.TestCase):
    def test_live_execution_requires_explicit_confirmation(self):
        source = (REPO_ROOT / "bin/evaluation-experiment.py").read_text()
        self.assertIn("--confirm-live", source)
        self.assertIn("run requires --confirm-live", source)

    def test_runner_has_failure_recovery_and_restore(self):
        source = (REPO_ROOT / "experiment_framework/runner.py").read_text()
        self.assertIn("failure-recovery", source)
        self.assertIn("without_safety_checks", source)
        self.assertIn("self.deployment_changed = True", source)

    def test_runner_uses_port_forward_and_no_nodeport(self):
        source = (REPO_ROOT / "experiment_framework/runner.py").read_text()
        self.assertIn('"port-forward"', source)
        self.assertIn("_port_forward_mappings(self.channel)", source)
        self.assertIn("*port_forward", source)
        self.assertNotIn('"NodePort"', source)

    def test_runner_forwards_control_and_stream_ports(self):
        self.assertEqual(
            _port_forward_mappings({
                "port_forward": "5555:5555",
                "port_forward_stream": "5556:5556",
            }),
            ("5555:5555", "5556:5556"),
        )
        self.assertEqual(
            _port_forward_mappings({
                "port_forward": ["5555:5555", "5556:5556"],
                "port_forward_stream": "5556:5556",
            }),
            ("5555:5555", "5556:5556"),
        )

    def test_generated_result_root_is_outside_repository(self):
        from experiment_framework.config import load_and_resolve_study

        resolved = load_and_resolve_study(
            REPO_ROOT / "experiments/studies/channel-evaluation-pilot.json"
        )
        result_root = pathlib.Path(resolved["result_root"]).resolve()
        self.assertNotEqual(result_root, REPO_ROOT)
        self.assertNotIn(REPO_ROOT, result_root.parents)


if __name__ == "__main__":
    unittest.main()
