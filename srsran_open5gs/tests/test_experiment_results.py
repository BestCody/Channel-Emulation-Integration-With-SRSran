import csv
import json
import pathlib
import tempfile
import unittest

from experiment_framework.results import ResultStore  # noqa: E402
from experiment_framework.results import expected_result_layout  # noqa: E402
from experiment_framework.results import write_json  # noqa: E402
from experiment_framework.summarize import summarize_run  # noqa: E402


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
EXTERNAL_RESULT_ROOT = str(REPO_ROOT.parent / "results" / "evaluation")


class ExperimentResultTests(unittest.TestCase):
    def test_result_tree_and_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResultStore(directory, "study", run_id="run-1")
            trial = store.trial("baseline", 1)
            self.assertTrue((trial / "condition/channel").is_dir())
            self.assertTrue((trial / "condition/traffic").is_dir())
            self.assertTrue((trial / "condition/monitoring").is_dir())
            self.assertTrue((trial / "restoration").is_dir())
            write_json(trial / "summary.json", {"condition_id": "baseline", "trial_number": 1})
            store.write_checksums()
            checksum = (store.root / "study-checksums.sha256").read_text()
            self.assertIn("trials/baseline/trial-001/summary.json", checksum)

    def test_existing_trial_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResultStore(directory, "study", run_id="run-1")
            store.trial("baseline", 1)
            with self.assertRaises(FileExistsError):
                store.trial("baseline", 1)

    def test_summary_keeps_individual_trials_and_omits_confidence_intervals(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResultStore(directory, "study", run_id="run-1")
            for number in range(1, 6):
                trial = store.trial("fixed", number)
                write_json(trial / "summary.json", {
                    "condition_id": "fixed",
                    "trial_number": number,
                    "status": "passed",
                    "attachment_success": True,
                    "ue_ip": f"10.41.0.{number}",
                    "ping": {
                        "packet_loss_percent": float(number - 1),
                        "rtt_ms": {"mean": 20.0 + number, "p95": 30.0 + number},
                    },
                    "amf": {
                        "restart_count_before": 1,
                        "restart_count_after": 1,
                        "memory_max_observed": 200000000,
                    },
                })
            summary = summarize_run(store.root)
            self.assertEqual(len(summary["trial_rows"]), 5)
            self.assertIsNone(summary["conditions"][0]["confidence_intervals"])
            with (store.root / "summary/trials.csv").open() as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(len(rows), 5)
            self.assertTrue((store.root / "summary/plots/packet-loss.svg").exists())
            self.assertTrue((store.root / "summary/channel-taps.csv").exists())
            self.assertTrue((store.root / "summary/channel-updates.csv").exists())
            self.assertTrue((store.root / "summary/sionna-timings.csv").exists())
            self.assertTrue((store.root / "summary/moving-positions.csv").exists())
            self.assertTrue((store.root / "summary/noise-levels.csv").exists())
            self.assertTrue((store.root / "summary/resource-samples.csv").exists())
            self.assertTrue((store.root / "summary/gpu-samples.csv").exists())
            self.assertTrue((store.root / "summary/amf-memory.csv").exists())
            self.assertIn("Throughput is deferred", (store.root / "summary/README.txt").read_text())

    def test_expected_layout_uses_external_result_root(self):
        layout = expected_result_layout(
            EXTERNAL_RESULT_ROOT,
            "channel-evaluation-pilot",
        )
        self.assertTrue(all(item.startswith(EXTERNAL_RESULT_ROOT + "/") for item in layout))
        self.assertTrue(any(item.endswith("summary/channel-updates.csv") for item in layout))


if __name__ == "__main__":
    unittest.main()
