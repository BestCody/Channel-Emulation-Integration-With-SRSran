import pathlib
import tempfile
import unittest

from experiment_framework.lifecycle import CommandExecutor  # noqa: E402
from experiment_framework.lifecycle import SafetyStop  # noqa: E402


class ExperimentLifecycleTests(unittest.TestCase):
    def test_capture_polls_safety_while_command_is_running(self):
        checks = 0

        def safety():
            nonlocal checks
            checks += 1
            if checks >= 3:
                raise SafetyStop("unsafe")

        executor = CommandExecutor(cwd="/tmp", safety_check=safety)
        with self.assertRaisesRegex(SafetyStop, "unsafe"):
            executor.capture(["bash", "-lc", "sleep 5"])
        self.assertGreaterEqual(checks, 3)

    def test_restoration_context_can_bypass_tripped_guard(self):
        def unsafe():
            raise SafetyStop("unsafe")

        executor = CommandExecutor(cwd="/tmp", safety_check=unsafe)
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "command.log"
            with executor.without_safety_checks():
                executor.run(["bash", "-lc", "printf restored"], output)
            self.assertEqual(output.read_text(), "restored")
        with self.assertRaises(SafetyStop):
            executor.capture(["bash", "-lc", "true"])


if __name__ == "__main__":
    unittest.main()
