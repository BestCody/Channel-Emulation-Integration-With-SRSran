import importlib.util
import os
import pathlib
import unittest
from contextlib import contextmanager


ROOT = pathlib.Path(__file__).resolve().parents[1]
UE_CONFIG = ROOT / "configs/ues/srsue/config"
GNB_CONFIG = ROOT / "configs/srsRAN/srsran-gnb/config"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


radio_endpoints = load_module(
    "radio_endpoints_under_test",
    UE_CONFIG / "radio_endpoints.py",
)
render_gnb_config = load_module(
    "render_gnb_config_under_test",
    GNB_CONFIG / "render_gnb_config.py",
)


ENV_KEYS = {
    "SRSRAN_AMF_N3_ADDR",
    "SRSRAN_GNB_N3_BIND_ADDR",
    "SRSRAN_GNB_ZMQ_ADDR",
    "SRSRAN_UE_ZMQ_ADDR",
    "SRSRAN_ZMQ_GNB_DOWNLINK_ENDPOINT",
    "SRSRAN_ZMQ_GNB_DOWNLINK_PORT",
    "SRSRAN_ZMQ_GNB_UPLINK_ENDPOINT",
    "SRSRAN_ZMQ_GNB_UPLINK_PORT",
    "SRSRAN_ZMQ_INTERFACE",
    "SRSRAN_ZMQ_UE1_DOWNLINK_ENDPOINT",
    "SRSRAN_ZMQ_UE1_UPLINK_ENDPOINT",
    "SRSRAN_ZMQ_UE_DOWNLINK_BASE_PORT",
    "SRSRAN_ZMQ_UE_UPLINK_BASE_PORT",
}


@contextmanager
def patched_env(values):
    original = {key: os.environ.get(key) for key in ENV_KEYS}
    try:
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(values)
        yield
    finally:
        for key in ENV_KEYS:
            os.environ.pop(key, None)
            if original[key] is not None:
                os.environ[key] = original[key]


class RadioEndpointConfigTests(unittest.TestCase):
    def test_default_endpoints_match_existing_topology(self):
        with patched_env({"SRSRAN_ZMQ_INTERFACE": "missing-test0"}):
            self.assertEqual(
                radio_endpoints.gnb_downlink_endpoint(),
                "tcp://10.10.3.231:2000",
            )
            self.assertEqual(
                radio_endpoints.gnb_uplink_endpoint(),
                "tcp://10.10.3.232:2001",
            )
            self.assertEqual(
                radio_endpoints.ue_uplink_endpoint(1),
                "tcp://10.10.3.232:2101",
            )
            self.assertEqual(
                radio_endpoints.ue_downlink_endpoint(1),
                "tcp://10.10.3.232:2201",
            )

    def test_endpoint_addresses_and_ports_are_configurable(self):
        with patched_env({
            "SRSRAN_GNB_ZMQ_ADDR": "192.0.2.31",
            "SRSRAN_UE_ZMQ_ADDR": "192.0.2.32",
            "SRSRAN_ZMQ_GNB_DOWNLINK_PORT": "3000",
            "SRSRAN_ZMQ_GNB_UPLINK_PORT": "3001",
            "SRSRAN_ZMQ_UE_UPLINK_BASE_PORT": "3100",
            "SRSRAN_ZMQ_UE_DOWNLINK_BASE_PORT": "3200",
        }):
            self.assertEqual(
                radio_endpoints.gnb_downlink_endpoint(),
                "tcp://192.0.2.31:3000",
            )
            self.assertEqual(
                radio_endpoints.gnb_uplink_endpoint(),
                "tcp://192.0.2.32:3001",
            )
            self.assertEqual(
                radio_endpoints.ue_uplink_endpoint(2),
                "tcp://192.0.2.32:3102",
            )
            self.assertEqual(
                radio_endpoints.ue_downlink_endpoint(2),
                "tcp://192.0.2.32:3202",
            )

    def test_full_endpoint_overrides_take_precedence(self):
        with patched_env({
            "SRSRAN_ZMQ_UE1_UPLINK_ENDPOINT": "tcp://203.0.113.10:7001",
            "SRSRAN_ZMQ_UE1_DOWNLINK_ENDPOINT": "tcp://203.0.113.10:7002",
        }):
            self.assertEqual(
                radio_endpoints.ue_uplink_endpoint(1),
                "tcp://203.0.113.10:7001",
            )
            self.assertEqual(
                radio_endpoints.ue_downlink_endpoint(1),
                "tcp://203.0.113.10:7002",
            )

    def test_gnb_template_renders_defaults_and_overrides(self):
        template = (GNB_CONFIG / "srsran-gnb.yaml").read_text()
        with patched_env({"SRSRAN_ZMQ_INTERFACE": "missing-test0"}):
            rendered = render_gnb_config.render_text(template)
            self.assertIn("addr: 10.10.3.200", rendered)
            self.assertIn("bind_addr: 10.10.3.231", rendered)
            self.assertIn("tx_port=tcp://10.10.3.231:2000", rendered)
            self.assertIn("rx_port=tcp://10.10.3.232:2001", rendered)
            self.assertNotIn("${SRSRAN_", rendered)

        with patched_env({
            "SRSRAN_AMF_N3_ADDR": "192.0.2.200",
            "SRSRAN_GNB_N3_BIND_ADDR": "192.0.2.31",
            "SRSRAN_ZMQ_GNB_DOWNLINK_ENDPOINT": "tcp://192.0.2.31:3000",
            "SRSRAN_ZMQ_GNB_UPLINK_ENDPOINT": "tcp://192.0.2.32:3001",
        }):
            rendered = render_gnb_config.render_text(template)
            self.assertIn("addr: 192.0.2.200", rendered)
            self.assertIn("bind_addr: 192.0.2.31", rendered)
            self.assertIn("tx_port=tcp://192.0.2.31:3000", rendered)
            self.assertIn("rx_port=tcp://192.0.2.32:3001", rendered)

    def test_flowgraphs_and_templates_do_not_embed_zmq_ips(self):
        paths = [
            UE_CONFIG / "multi_ue_scenario.py",
            UE_CONFIG / "generate_ue_conf.py",
            UE_CONFIG / "ue0.conf",
            ROOT / "configs/ues/srsue-live/config/multi_ue_live_channel.py",
            ROOT / "configs/ues/srsue-noise/config/multi_ue_noise_channel.py",
            GNB_CONFIG / "srsran-gnb.yaml",
        ]
        for path in paths:
            text = path.read_text()
            self.assertNotIn("10.10.3.231", text, str(path))
            self.assertNotIn("10.10.3.232", text, str(path))


if __name__ == "__main__":
    unittest.main()
