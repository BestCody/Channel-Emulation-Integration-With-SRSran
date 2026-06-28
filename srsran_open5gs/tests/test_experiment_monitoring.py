import math
import unittest

from experiment_framework.monitoring import AMFSafetyPolicy  # noqa: E402
from experiment_framework.monitoring import evaluate_amf_sample  # noqa: E402


def sample(memory=100, maximum=1000, restarts=2, pod="pod-a", container="container-a"):
    return {
        "memory_current": memory,
        "memory_max": maximum,
        "restart_count": restarts,
        "pod_uid": pod,
        "container_id": container,
    }


class ExperimentMonitoringTests(unittest.TestCase):
    def setUp(self):
        self.policy = AMFSafetyPolicy(
            stop_at_limit_fraction=0.90,
            stop_at_growth_bytes=128 * 1024 * 1024,
        )
        self.baseline = sample(memory=128 * 1024 * 1024, maximum=512 * 1024 * 1024)

    def test_safe_sample(self):
        result = evaluate_amf_sample(
            self.baseline,
            sample(memory=180 * 1024 * 1024, maximum=512 * 1024 * 1024),
            self.policy,
        )
        self.assertTrue(result["safe"])

    def test_restart_stops_study(self):
        result = evaluate_amf_sample(self.baseline, {**self.baseline, "restart_count": 3}, self.policy)
        self.assertIn("restart", " ".join(result["reasons"]))

    def test_identity_change_stops_study(self):
        result = evaluate_amf_sample(self.baseline, {**self.baseline, "pod_uid": "pod-b"}, self.policy)
        self.assertIn("UID", " ".join(result["reasons"]))

    def test_ninety_percent_stops_study(self):
        result = evaluate_amf_sample(
            self.baseline,
            sample(memory=math.ceil(0.90 * 512 * 1024 * 1024), maximum=512 * 1024 * 1024),
            self.policy,
        )
        self.assertIn("90%", " ".join(result["reasons"]))

    def test_128_mib_growth_stops_study(self):
        result = evaluate_amf_sample(
            self.baseline,
            sample(memory=self.baseline["memory_current"] + 128 * 1024 * 1024, maximum=512 * 1024 * 1024),
            self.policy,
        )
        self.assertIn("128 MiB", " ".join(result["reasons"]))


if __name__ == "__main__":
    unittest.main()
