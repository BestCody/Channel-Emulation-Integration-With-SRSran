import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
OVERLAY = REPO_ROOT / "configs" / "ues" / "srsue-live"
BLOCK_HEADER = (
    REPO_ROOT
    / "gr-sionna-channel/include/gnuradio/sionna_channel/sparse_channel_cc.h"
)
BLOCK_IMPL = REPO_ROOT / "gr-sionna-channel/lib/sparse_channel_cc_impl.cc"


class Stage4OverlayTests(unittest.TestCase):
    def test_overlay_builds_on_base_and_sets_live_image(self):
        text = (OVERLAY / "kustomization.yaml").read_text()
        self.assertIn("../srsue", text)
        self.assertIn("localhost/srsue-live:stage4-gr38-v1", text)
        self.assertIn("value: Never", text)

    def test_no_nodeport_or_service(self):
        all_text = "\n".join(
            path.read_text()
            for path in OVERLAY.rglob("*")
            if path.is_file()
            and path.suffix in {".py", ".yaml", ".sh"}
        )
        self.assertNotIn("NodePort", all_text)
        self.assertNotIn("kind: Service", all_text)
        self.assertIn("containerPort: 5555", all_text)

    def test_live_mode_excludes_future_stages(self):
        flowgraph = (
            OVERLAY / "config/multi_ue_live_channel.py"
        ).read_text().lower()
        self.assertNotIn("sionna rt", flowgraph)
        self.assertNotIn("movement", flowgraph.replace(
            '"no noise, movement, sionna, or per-symbol channels"', ""
        ))
        self.assertNotIn("noise_source", flowgraph)
        self.assertNotIn("symbol_taps", flowgraph)

    def test_commit_both_is_cpp_transaction(self):
        header = BLOCK_HEADER.read_text()
        implementation = BLOCK_IMPL.read_text()
        self.assertIn("bool commit_both", header)
        self.assertIn("bool commit_both", implementation)
        self.assertIn("first->d_pending_mutex", implementation)
        self.assertIn("second->d_pending_mutex", implementation)
        first_publish = implementation.index(
            "downlink_impl->publish_prepared_locked"
        )
        second_lock = implementation.index(
            "second_lock(second->d_pending_mutex)"
        )
        self.assertGreater(first_publish, second_lock)

    def test_launcher_uses_detected_sample_rate(self):
        launcher = (
            OVERLAY / "config/start_gnu_live_channel.sh"
        ).read_text()
        self.assertIn("fixed_channel.py sample-rate", launcher)
        self.assertNotIn("23.04", launcher)


if __name__ == "__main__":
    unittest.main()
