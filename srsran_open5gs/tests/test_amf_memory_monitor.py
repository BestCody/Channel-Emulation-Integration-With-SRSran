import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "channel_emulation"))

from amf_memory_monitor import MIB  # noqa: E402
from amf_memory_monitor import evaluate_sample  # noqa: E402


THRESHOLDS = dict(
    stop_growth_bytes=128 * MIB,
    warn_growth_bytes=64 * MIB,
    stop_limit_fraction=0.90,
    warn_limit_fraction=0.75,
)


def sample(memory, restart=1, uid="uid", container="container"):
    return {
        "memory_current": memory,
        "memory_max": 512 * MIB,
        "restart_count": restart,
        "pod_uid": uid,
        "container_id": container,
    }


class AmfMemoryMonitorTests(unittest.TestCase):
    def test_normal_growth_is_allowed(self):
        reasons, warnings = evaluate_sample(
            sample(30 * MIB),
            sample(20 * MIB),
            **THRESHOLDS,
        )
        self.assertEqual(reasons, [])
        self.assertEqual(warnings, [])

    def test_restart_is_unsafe(self):
        reasons, _warnings = evaluate_sample(
            sample(20 * MIB, restart=2),
            sample(20 * MIB, restart=1),
            **THRESHOLDS,
        )
        self.assertIn("AMF restart count changed", reasons)

    def test_growth_thresholds(self):
        _reasons, warnings = evaluate_sample(
            sample(85 * MIB),
            sample(20 * MIB),
            **THRESHOLDS,
        )
        self.assertIn(
            f"AMF memory grew by at least {THRESHOLDS['warn_growth_bytes']} bytes",
            warnings,
        )
        reasons, _warnings = evaluate_sample(
            sample(150 * MIB),
            sample(20 * MIB),
            **THRESHOLDS,
        )
        self.assertIn(
            f"AMF memory grew by at least {THRESHOLDS['stop_growth_bytes']} bytes",
            reasons,
        )

    def test_limit_threshold_is_unsafe(self):
        reasons, _warnings = evaluate_sample(
            sample(int(512 * MIB * 0.91)),
            sample(20 * MIB),
            **THRESHOLDS,
        )
        self.assertIn("AMF memory reached 90% of its limit", reasons)


if __name__ == "__main__":
    unittest.main()
