import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_ROOT = REPO_ROOT / "gr-sionna-channel"
OVERLAY_ROOT = REPO_ROOT / "configs" / "ues" / "srsue-sparse"


class SparseChannelSourceTests(unittest.TestCase):
    def test_stage1_and_stage2_sources_are_inherited(self):
        kustomization = (
            OVERLAY_ROOT / "kustomization.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("../srsue-fixed", kustomization)
        self.assertNotIn("../srsue\n", kustomization)

    def test_overlay_uses_local_image_without_pulling(self):
        kustomization = (
            OVERLAY_ROOT / "kustomization.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn("newName: localhost/srsue-sparse", kustomization)
        self.assertIn("newTag: stage3-gr38-v1", kustomization)
        self.assertIn("value: Never", kustomization)

    def test_block_uses_gnuradio_38_pointer_api(self):
        public_header = (
            MODULE_ROOT
            / "include/gnuradio/sionna_channel/sparse_channel_cc.h"
        ).read_text(encoding="utf-8")
        implementation = (
            MODULE_ROOT / "lib/sparse_channel_cc_impl.cc"
        ).read_text(encoding="utf-8")
        self.assertIn("boost::shared_ptr", public_header)
        self.assertIn("gnuradio::get_initial_sptr", implementation)
        self.assertNotIn("std::shared_ptr", public_header)
        self.assertNotIn("make_block_sptr", implementation)

    def test_block_has_exact_limits_and_no_slot_assumption(self):
        header = (
            MODULE_ROOT / "lib/sparse_channel_cc_impl.h"
        ).read_text(encoding="utf-8")
        implementation = (
            MODULE_ROOT / "lib/sparse_channel_cc_impl.cc"
        ).read_text(encoding="utf-8")
        source = header + implementation
        self.assertIn("kMaxTaps = 48", source)
        self.assertIn("kMaxDelay = 255", source)
        self.assertNotIn("SamplesPerSlot", source)
        self.assertNotIn("sample_rate", source)
        self.assertNotIn("symbol_index", source)

    def test_history_is_persistent_and_power_of_two_wrapped(self):
        header = (
            MODULE_ROOT / "lib/sparse_channel_cc_impl.h"
        ).read_text(encoding="utf-8")
        implementation = (
            MODULE_ROOT / "lib/sparse_channel_cc_impl.cc"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "std::array<gr_complex, kHistorySize> d_history{}",
            header,
        )
        self.assertIn("& kHistoryMask", implementation)
        self.assertNotIn("d_history.clear", implementation)

    def test_python_binding_is_swig_not_pybind11(self):
        binding = (
            MODULE_ROOT / "python/bindings/sionna_channel_swig.i"
        ).read_text(encoding="utf-8")
        cmake = (
            MODULE_ROOT / "python/CMakeLists.txt"
        ).read_text(encoding="utf-8")
        self.assertIn('include(UseSWIG)', cmake)
        self.assertIn('GR_SWIG_BLOCK_MAGIC2', binding)
        self.assertNotIn("pybind11", binding + cmake)

    def test_launcher_uses_tested_sample_rate_parser(self):
        launcher = (
            OVERLAY_ROOT / "config/start_gnu_sparse_channel.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("fixed_channel.py sample-rate", launcher)
        self.assertNotIn("23.04", launcher)

    def test_flowgraph_is_static_and_noninteractive(self):
        flowgraph = (
            OVERLAY_ROOT / "config/multi_ue_sparse_channel.py"
        ).read_text(encoding="utf-8")
        self.assertIn("sparse_channel_cc", flowgraph)
        self.assertIn("stop_event.wait()", flowgraph)
        self.assertNotIn("input(", flowgraph)
        self.assertNotIn("sionna", flowgraph.lower().replace(
            "sionna_channel", ""
        ))
        self.assertNotIn("channel_control", flowgraph)


if __name__ == "__main__":
    unittest.main()
