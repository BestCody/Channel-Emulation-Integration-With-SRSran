import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
OVERLAY = REPO_ROOT / "configs/ues/srsue-noise"


class Stage6OverlayTests(unittest.TestCase):
    def test_separate_overlay_inherits_stage4(self):
        text = (OVERLAY / "kustomization.yaml").read_text()
        self.assertIn("../srsue-live", text)
        self.assertNotIn("../srsue-fixed", text)

    def test_no_nodeport_service_or_amf_change(self):
        all_text = "\n".join(
            path.read_text()
            for path in OVERLAY.rglob("*")
            if path.is_file()
            and path.suffix in {".py", ".yaml", ".sh"}
        )
        self.assertNotIn("NodePort", all_text)
        self.assertNotIn("kind: Service", all_text)
        self.assertNotIn("open5gs-amf", all_text)
        self.assertNotIn("memory:", all_text)

    def test_one_control_endpoint(self):
        launcher = (
            OVERLAY / "config/start_gnu_noise_channel.sh"
        ).read_text()
        flowgraph = (
            OVERLAY / "config/multi_ue_noise_channel.py"
        ).read_text()
        control = (
            OVERLAY / "config/noise_channel_control.py"
        ).read_text()
        self.assertEqual(launcher.count("0.0.0.0:5555"), 1)
        self.assertNotIn("socket.bind", control)
        self.assertIn("NoiseChannelControlServer", flowgraph)

    def test_noise_is_after_sparse_channel(self):
        flowgraph = (
            OVERLAY / "config/multi_ue_noise_channel.py"
        ).read_text()
        self.assertIn("self.downlink_noise_adder", flowgraph)
        self.assertIn("self.uplink_noise_adder", flowgraph)
        self.assertIn(
            "(self.downlink_channel, 0),\n"
            "            (self.downlink_noise_adder, 0)",
            flowgraph,
        )
        self.assertIn(
            "(self.uplink_channel, 0),\n"
            "            (self.uplink_noise_adder, 0)",
            flowgraph,
        )

    def test_signal_probe_is_connected_before_noise_adder(self):
        flowgraph = (
            OVERLAY / "config/multi_ue_noise_channel.py"
        ).read_text()
        self.assertIn(
            "self.downlink_channel,\n"
            "            self.downlink_signal_probe",
            flowgraph,
        )
        self.assertIn(
            "self.uplink_channel,\n"
            "            self.uplink_signal_probe",
            flowgraph,
        )

    def test_launcher_detects_sample_rate(self):
        launcher = (
            OVERLAY / "config/start_gnu_noise_channel.sh"
        ).read_text()
        self.assertIn("fixed_channel.py sample-rate", launcher)
        self.assertNotIn("23.04", launcher)


if __name__ == "__main__":
    unittest.main()
