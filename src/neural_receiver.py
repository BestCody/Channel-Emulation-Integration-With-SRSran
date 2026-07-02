import argparse
import copy
import json
import pathlib
import sys
from types import SimpleNamespace

import sionna.phy
import sionna.rt
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np

from sionna.phy import Block
from sionna.phy.channel import cir_to_ofdm_channel, subcarrier_frequencies
from sionna.phy.mimo import StreamManagement
from sionna.phy.ofdm import ResourceGrid, ResourceGridMapper, ResourceGridDemapper
from sionna.phy.utils import ebnodb2no
from sionna.phy.fec.ldpc import LDPC5GEncoder, LDPC5GDecoder
from sionna.phy.mapping import Mapper, BinarySource
from sionna.rt import load_scene, Transmitter, Receiver, PlanarArray, PathSolver
from sionna.phy.channel import ApplyOFDMChannel

# Reuse the evaluation system's parameter loaders
FRAMEWORK = pathlib.Path(__file__).resolve().parents[1] / "srsran_open5gs"
sys.path.insert(0, str(FRAMEWORK / "channel_emulation"))
from sionna_scene import (  # noqa: E402
    load_scene_config,
    sample_ue_positions,
    scene_bounding_box,
    antenna_array_dims,
    antenna_port_count,
    _solver_options,
)
from sionna_radio_config import load_radio_config  # noqa: E402

DEFAULT_SCENE_CONFIG = FRAMEWORK / "channel_emulation/scenes/default_scene.json"
DEFAULT_GNB_CONFIG = FRAMEWORK / "configs/srsRAN/srsran-gnb/config/srsran-gnb.yaml"
DEFAULT_UE_CONFIG = FRAMEWORK / "configs/ues/srsue/config/ue0.conf"

sionna.phy.config.seed = 42
device = sionna.phy.config.device

# Receiver PHY (model intrinsics, not eval parameters)
NUM_OFDM_SYMBOLS        = 14
FFT_SIZE                = 76
SUBCARRIER_SPACING      = 30e3
CYCLIC_PREFIX_LENGTH    = 6
PILOT_SYMBOL_INDICES    = [2, 11]
NUM_BITS_PER_SYMBOL     = 2
CODERATE                = 0.5
EBN0_DB_MIN             = -3.0
EBN0_DB_MAX             = 5.0
BATCH_SIZE              = 128
NUM_TRAINING_ITERATIONS = 10000


def build_phy(num_ut, num_ut_ant):
    # uplink layout: every UE stream lands on the one BS
    association = np.ones((1, num_ut), dtype=np.int32)
    stream_management = StreamManagement(association, num_ut_ant)
    resource_grid = ResourceGrid(
        num_ofdm_symbols          = NUM_OFDM_SYMBOLS,
        fft_size                  = FFT_SIZE,
        subcarrier_spacing        = SUBCARRIER_SPACING,
        num_tx                    = num_ut,
        num_streams_per_tx        = num_ut_ant,
        cyclic_prefix_length      = CYCLIC_PREFIX_LENGTH,
        pilot_pattern             = "kronecker",
        pilot_ofdm_symbol_indices = PILOT_SYMBOL_INDICES,
    )
    n = int(resource_grid.num_data_symbols * NUM_BITS_PER_SYMBOL)
    k = int(n * CODERATE)
    return SimpleNamespace(
        num_ut=int(num_ut),
        num_ut_ant=int(num_ut_ant),
        resource_grid=resource_grid,
        stream_management=stream_management,
        n=n,
        k=k,
    )


def channel_from_scene_configs(configs, carrier_hz, phy, num_samples=None):
    # one joint solve: shared gNB, one receiver per UE
    import drjit as dr
    from sionna.rt import scene as rt_scene

    base = configs[0]
    scene_name = base["scene"]
    scene_path = getattr(rt_scene, scene_name, None)
    if not isinstance(scene_path, str):
        raise ValueError(f"unknown bundled scene: {scene_name}")

    antenna = base["antenna"]
    bs_rows, bs_cols = antenna_array_dims(antenna, "bs_array")
    ue_rows, ue_cols = antenna_array_dims(antenna, "ue_array")

    scene = load_scene(scene_path)
    scene.frequency = float(carrier_hz)
    scene.tx_array = PlanarArray(
        num_rows=bs_rows, num_cols=bs_cols,
        pattern=antenna["pattern"],
        polarization=antenna["polarization"],
    )
    scene.rx_array = PlanarArray(
        num_rows=ue_rows, num_cols=ue_cols,
        pattern=antenna["pattern"],
        polarization=antenna["polarization"],
    )
    num_bs_ant = int(scene.tx_array.num_ant)
    if int(scene.rx_array.num_ant) != phy.num_ut_ant:
        raise ValueError("UE antenna count does not match the PHY grid")

    transmitter = Transmitter(
        name="gnb", position=base["transmitter"]["position"]
    )
    scene.add(transmitter)
    receivers = []
    for index, config in enumerate(configs):
        # UE velocity drives per-symbol Doppler below
        velocity = config["receiver"].get("velocity") or [0.0, 0.0, 0.0]
        velocity = [float(component) for component in velocity]
        if len(velocity) != 3:
            raise ValueError("receiver velocity must have three components")
        receiver = Receiver(
            name=f"ue{index + 1}",
            position=config["receiver"]["position"],
            velocity=velocity,
        )
        receiver.look_at(transmitter)
        scene.add(receiver)
        receivers.append(receiver)
    transmitter.look_at(receivers[0])

    options = _solver_options(base["solver"])
    if num_samples is not None:
        options["samples_per_src"] = int(num_samples)
    solver = PathSolver()
    paths = solver(scene, **options)
    dr.sync_thread()

    # one time step per OFDM symbol for real Doppler
    a, tau = paths.cir(
        sampling_frequency = 1.0 / phy.resource_grid.ofdm_symbol_duration,
        num_time_steps     = phy.resource_grid.num_ofdm_symbols,
        out_type           = "numpy",
    )
    # solve runs downlink; reciprocity flips it to uplink
    a = np.transpose(np.asarray(a), (2, 3, 0, 1, 4, 5))
    tau = np.asarray(tau)
    if tau.ndim == 3:
        tau = np.transpose(tau, (1, 0, 2))
    else:
        tau = np.transpose(tau, (2, 3, 0, 1, 4))
    a   = torch.tensor(a,   dtype=torch.complex64).to(device).unsqueeze(0)
    tau = torch.tensor(tau, dtype=torch.float32).to(device).unsqueeze(0)

    freqs = subcarrier_frequencies(
        phy.resource_grid.fft_size, phy.resource_grid.subcarrier_spacing
    ).to(device)
    h_freq = cir_to_ofdm_channel(freqs, a, tau, normalize=True)
    return h_freq, num_bs_ant


# Neural Receiver Architecture
class ResidualBlock(nn.Module):
    def __init__(self, num_conv_channels=128):
        super().__init__()
        self._layer_norm_1 = nn.LayerNorm([num_conv_channels, NUM_OFDM_SYMBOLS, FFT_SIZE])
        self._conv_1 = nn.Conv2d(num_conv_channels, num_conv_channels, kernel_size=3, padding=1)
        self._layer_norm_2 = nn.LayerNorm([num_conv_channels, NUM_OFDM_SYMBOLS, FFT_SIZE])
        self._conv_2 = nn.Conv2d(num_conv_channels, num_conv_channels, kernel_size=3, padding=1)

    def forward(self, inputs):
        z = self._layer_norm_1(inputs)
        z = F.relu(z)
        z = self._conv_1(z)
        z = self._layer_norm_2(z)
        z = F.relu(z)
        z = self._conv_2(z)
        return z + inputs

class NeuralReceiver(nn.Module):
    def __init__(self, num_bs_ant, num_ut, num_ut_ant, num_conv_channels=128):
        super().__init__()
        num_input_channels = 2 * num_bs_ant + 1
        # LLRs for every UE stream come out of one head
        num_output_channels = num_ut * num_ut_ant * NUM_BITS_PER_SYMBOL
        self._input_conv  = nn.Conv2d(num_input_channels, num_conv_channels, kernel_size=3, padding=1)
        self._res_block_1 = ResidualBlock(num_conv_channels)
        self._res_block_2 = ResidualBlock(num_conv_channels)
        self._res_block_3 = ResidualBlock(num_conv_channels)
        self._res_block_4 = ResidualBlock(num_conv_channels)
        self._output_conv = nn.Conv2d(num_conv_channels, num_output_channels, kernel_size=3, padding=1)

    def forward(self, y, noise):
        noise       = torch.log10(noise)
        y_real   = y.real
        y_imag   = y.imag
        batch_size = y.shape[0]
        noise = noise.view(-1, 1, 1, 1).expand(batch_size, 1, y.shape[2], y.shape[3])
        z = torch.cat([y_real, y_imag, noise], dim=1)
        z = self._input_conv(z)
        z = self._res_block_1(z)
        z = self._res_block_2(z)
        z = self._res_block_3(z)
        z = self._res_block_4(z)
        z = self._output_conv(z)
        return z.permute(0, 2, 3, 1)


class OFDMSystemNeuralReceiverRT(Block):
    def __init__(self, num_bs_ant, phy, training=True):
        super().__init__()
        self._training      = training
        self._phy           = phy
        self._k             = phy.k
        self._n             = phy.n
        self._binary_source = BinarySource()
        if not training:
            self._encoder = LDPC5GEncoder(phy.k, phy.n)
        self._mapper      = Mapper("qam", NUM_BITS_PER_SYMBOL)
        self._rg_mapper   = ResourceGridMapper(phy.resource_grid)

        self._neural_rx   = NeuralReceiver(
            num_bs_ant, phy.num_ut, phy.num_ut_ant
        ).to(device)
        self._rg_demapper = ResourceGridDemapper(
            phy.resource_grid, phy.stream_management
        )
        self._apply_channel = ApplyOFDMChannel(add_awgn=True)
        if not training:
            self._decoder = LDPC5GDecoder(self._encoder, hard_out=True)

    def forward(self, batch_size, ebno_db, h_freq):
        num_ut = self._phy.num_ut
        num_ut_ant = self._phy.num_ut_ant
        no = ebnodb2no(ebno_db, num_bits_per_symbol=NUM_BITS_PER_SYMBOL,
                       coderate=CODERATE, resource_grid=self._phy.resource_grid)
        if no.dim() == 0:
            no = no.expand(batch_size)

        if self._training:
            codewords = self._binary_source([batch_size, num_ut, num_ut_ant, self._n])
        else:
            bits      = self._binary_source([batch_size, num_ut, num_ut_ant, self._k])
            codewords = self._encoder(bits)

        x    = self._mapper(codewords)
        x_rg = self._rg_mapper(x)

        y = self._apply_channel(x_rg, h_freq, no)

        y   = y.squeeze(1)
        llr = self._neural_rx(y, no)
        # split the head into per-stream resource grids
        llr = llr.reshape(
            batch_size, NUM_OFDM_SYMBOLS, FFT_SIZE,
            num_ut * num_ut_ant, NUM_BITS_PER_SYMBOL,
        )
        llr = llr.permute(0, 3, 1, 2, 4).unsqueeze(1)
        llr = self._rg_demapper(llr)
        llr = llr.reshape(batch_size, num_ut, num_ut_ant, self._n)

        if self._training:
            return F.binary_cross_entropy_with_logits(llr, codewords.float())
        else:
            bits_hat = self._decoder(llr)
            return bits, bits_hat, llr


def train(num_bs_ant, phy, h_freq, iterations, batch_size, log_every=1000):
    model = OFDMSystemNeuralReceiverRT(num_bs_ant, phy, training=True)
    optimizer = torch.optim.Adam(model.parameters())
    for i in range(iterations):
        ebno_db = torch.empty(batch_size, device=device).uniform_(
            EBN0_DB_MIN, EBN0_DB_MAX
        )
        loss = model(batch_size, ebno_db, h_freq)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
        if log_every and i % log_every == 0:
            print(f"    {i}/{iterations}  Loss: {loss.item():.2E}", flush=True)
    return model


def evaluate_ber(weights, num_bs_ant, phy, h_freq, num_points, batch_size):
    # per-UE curves: list of [ebno_db, ber] rows per UE
    model = OFDMSystemNeuralReceiverRT(num_bs_ant, phy, training=False)
    model._neural_rx.load_state_dict(weights)
    model.eval()
    points = np.linspace(EBN0_DB_MIN, EBN0_DB_MAX, num_points)
    results = [[] for _ in range(phy.num_ut)]
    with torch.no_grad():
        for ebno_db in points:
            ebno = torch.tensor(float(ebno_db), device=device)
            bits, bits_hat, _ = model(batch_size, ebno, h_freq)
            errors = (bits != bits_hat).float()
            for ut in range(phy.num_ut):
                ber = errors[:, ut].mean().item()
                results[ut].append([float(ebno_db), max(ber, 1e-5)])
    return results


def evaluate_throughput(weights, num_bs_ant, phy, h_freq, num_points, batch_size):
    # per-UE curves: list of [ebno_db, bits/slot] rows per UE
    model = OFDMSystemNeuralReceiverRT(num_bs_ant, phy, training=False)
    model._neural_rx.load_state_dict(weights)
    model.eval()
    points = np.linspace(EBN0_DB_MIN, 2.0, num_points)
    results = [[] for _ in range(phy.num_ut)]
    with torch.no_grad():
        for ebno_db in points:
            ebno = torch.tensor(float(ebno_db), device=device)
            bits, bits_hat, _ = model(batch_size, ebno, h_freq)
            # one codeword per stream per slot
            block_errors = (bits != bits_hat).any(dim=-1).float()
            for ut in range(phy.num_ut):
                harq_success = 1.0 - block_errors[:, ut].mean().item()
                results[ut].append(
                    [float(ebno_db), harq_success * phy.k * phy.num_ut_ant]
                )
    return results


def build_ue_configs(args, num_ues):
    # one resolved scene config per UE
    if num_ues > 1:
        if args.placement_mode != "random":
            raise ValueError(
                "multi-UE (--num-ues > 1) requires --placement-mode random"
            )
        base = load_scene_config(args.scene_config, placement_mode="configured")
        bounds = scene_bounding_box(base["scene"])
        min_distance = (
            args.placement_min_distance
            if args.placement_min_distance is not None
            else base.get("placement", {}).get("min_distance_m", 0.0)
        )
        transmitter, receivers = sample_ue_positions(
            bounds, num_ues, seed=args.placement_seed, min_distance=min_distance
        )
        configs = []
        for receiver in receivers:
            config = copy.deepcopy(base)
            config["transmitter"]["position"] = list(transmitter)
            config["receiver"]["position"] = list(receiver)
            configs.append(config)
        return configs
    config = load_scene_config(
        args.scene_config,
        placement_mode=args.placement_mode,
        placement_seed=args.placement_seed,
        min_distance_m=args.placement_min_distance,
    )
    return [config]


def run_evaluation(args, radio):
    num_ues = int(args.num_ues)
    if num_ues < 1:
        raise ValueError("--num-ues must be at least one")
    configs = build_ue_configs(args, num_ues)
    antenna = configs[0]["antenna"]
    ue_rows, ue_cols = antenna_array_dims(antenna, "ue_array")
    bs_rows, bs_cols = antenna_array_dims(antenna, "bs_array")
    num_ut_ant = antenna_port_count(
        ue_rows, ue_cols, antenna["polarization"]
    )
    phy = build_phy(num_ues, num_ut_ant)
    h_freq, num_bs_ant = channel_from_scene_configs(
        configs, radio.carrier_hz, phy, num_samples=args.num_samples
    )
    print(
        f"  h_freq {tuple(h_freq.shape)}  num_bs_ant={num_bs_ant}  "
        f"num_ut={num_ues}  num_ut_ant={num_ut_ant}",
        flush=True,
    )
    if num_bs_ant < num_ues * num_ut_ant:
        print(
            f"  warning: {num_ues * num_ut_ant} uplink streams exceed "
            f"{num_bs_ant} BS antennas",
            flush=True,
        )
    print(f"  training {args.iterations} iterations", flush=True)
    model = train(
        num_bs_ant, phy, h_freq, args.iterations, args.batch_size
    )
    weights = model._neural_rx.state_dict()
    ber_curves = evaluate_ber(
        weights, num_bs_ant, phy, h_freq, args.eval_points, args.batch_size
    )
    throughput_curves = evaluate_throughput(
        weights, num_bs_ant, phy, h_freq,
        args.throughput_points, args.batch_size,
    )
    ues = []
    for index, config in enumerate(configs):
        ue_index = index + 1
        ber = ber_curves[index]
        throughput = throughput_curves[index]
        peak = max(t for _, t in throughput)
        best = min(b for _, b in ber)
        print(
            f"  UE {ue_index}: min BER={best:.2e}  "
            f"peak throughput={peak:.1f} bits/slot",
            flush=True,
        )
        ues.append({
            "ue_index": ue_index,
            "transmitter": config["transmitter"]["position"],
            "receiver": config["receiver"]["position"],
            "receiver_velocity": config["receiver"].get(
                "velocity", [0.0, 0.0, 0.0]
            ),
            "num_bs_ant": num_bs_ant,
            "num_ut_ant": num_ut_ant,
            "ber": ber,
            "throughput": throughput,
            "min_ber": best,
            "peak_throughput_bits_per_slot": peak,
        })
    return {
        "schema_version": 1,
        "measurement": "neural-receiver-link-eval",
        "num_ues": num_ues,
        "scene": configs[0]["scene"],
        "carrier_hz": radio.carrier_hz,
        "iterations": args.iterations,
        "batch_size": args.batch_size,
        "mu_mimo": {
            "joint_detection": True,
            "num_bs_ant": num_bs_ant,
            "num_ut_ant": num_ut_ant,
            "bs_array": [bs_rows, bs_cols],
            "ue_array": [ue_rows, ue_cols],
        },
        "ues": ues,
    }


def parse_args():
    parser = argparse.ArgumentParser(
        description="Neural receiver link evaluation over the eval channel"
    )
    parser.add_argument("--scene-config", default=str(DEFAULT_SCENE_CONFIG))
    parser.add_argument("--gnb-config", default=str(DEFAULT_GNB_CONFIG))
    parser.add_argument("--ue-config", default=str(DEFAULT_UE_CONFIG))
    parser.add_argument("--output", required=True)
    parser.add_argument("--num-ues", type=int, default=1)
    parser.add_argument("--placement-mode", choices=["configured", "random"])
    parser.add_argument("--placement-seed", type=int)
    parser.add_argument("--placement-min-distance", type=float)
    parser.add_argument("--iterations", type=int, default=NUM_TRAINING_ITERATIONS)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--eval-points", type=int, default=20)
    parser.add_argument("--throughput-points", type=int, default=30)
    parser.add_argument("--num-samples", type=int)
    return parser.parse_args()


def main():
    args = parse_args()
    radio = load_radio_config(args.gnb_config, args.ue_config)
    report = run_evaluation(args, radio)
    output = pathlib.Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"report={args.output}", flush=True)


if __name__ == "__main__":
    main()
