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
from sionna.phy.utils import ebnodb2no, insert_dims
from sionna.phy.fec.ldpc import LDPC5GEncoder, LDPC5GDecoder
from sionna.phy.mapping import Mapper, BinarySource
from sionna.rt import load_scene, Transmitter, Receiver, PlanarArray, PathSolver
from sionna.phy.channel import ApplyOFDMChannel

sionna.phy.config.seed = 42
device = sionna.phy.config.device

# Constants
CARRIER_FREQUENCY       = 2.6e9
NUM_UT                  = 1
NUM_UT_ANT              = 1
NUM_BS_ANT              = 2
NUM_BITS_PER_SYMBOL     = 2
CODERATE                = 0.5
NUM_STREAMS_PER_TX      = NUM_UT_ANT
EBN0_DB_MIN             = -3.0
EBN0_DB_MAX             = 5.0
BATCH_SIZE              = 128
NUM_TRAINING_ITERATIONS = 10000

RX_TX_ASSOCIATION = np.array([[1]])
STREAM_MANAGEMENT = StreamManagement(RX_TX_ASSOCIATION, NUM_STREAMS_PER_TX)

RESOURCE_GRID = ResourceGrid(
    num_ofdm_symbols          = 14,
    fft_size                  = 76,
    subcarrier_spacing        = 30e3,
    num_tx                    = NUM_UT,
    num_streams_per_tx        = NUM_STREAMS_PER_TX,
    cyclic_prefix_length      = 6,
    pilot_pattern             = "kronecker",
    pilot_ofdm_symbol_indices = [2, 11]
)

NUM_OFDM_SYMBOLS = RESOURCE_GRID.num_ofdm_symbols
FFT_SIZE         = RESOURCE_GRID.fft_size
N                = int(RESOURCE_GRID.num_data_symbols * NUM_BITS_PER_SYMBOL)
K                = int(N * CODERATE)

TX_POSITION = [8.5, 21.0, 27.0]
RX_POSITION = [45.0, 90.0, 1.5]
TX_VELOCITY = [1.5, 0.0, 0.0]
RX_VELOCITY = [0.0, 0.0, 0.0]

EFFECT_CONFIG = dict(specular_reflection=True, diffuse_reflection=True, diffraction=True)
WEIGHTS_FILE = "weights-all_effects.pt"
SCENE = sionna.rt.scene.munich
p_solver = PathSolver()

# Helpers
def build_scene(scene_ref):
    scene = load_scene(scene_ref, merge_shapes=True)
    scene.frequency = CARRIER_FREQUENCY
    scene.tx_array = PlanarArray(
        num_rows=1, num_cols=1,
        vertical_spacing=0.5, horizontal_spacing=0.5,
        pattern="tr38901", polarization="V"
    )
    scene.rx_array = PlanarArray(
        num_rows=1, num_cols=int(NUM_BS_ANT / 2),
        vertical_spacing=0.5, horizontal_spacing=0.5,
        pattern="tr38901", polarization="cross"
    )
    scene.add(Transmitter(name="ut", position=TX_POSITION))
    scene.add(Receiver(name="bs",   position=RX_POSITION))
    return scene

def compute_h_freq(scene, effect_config, num_samples=500000):
    scene.get("ut").velocity = TX_VELOCITY
    scene.get("bs").velocity = RX_VELOCITY

    paths = p_solver(
        scene           = scene,
        max_depth       = 5,
        samples_per_src = num_samples,
        los             = True,
        refraction      = False,
        seed            = 42,
        **effect_config
    )

    a, tau = paths.cir(
        sampling_frequency = RESOURCE_GRID.bandwidth,
        num_time_steps     = RESOURCE_GRID.num_ofdm_symbols,
        out_type           = "numpy"
    )

    a   = torch.tensor(a,   dtype=torch.complex64).to(device)
    tau = torch.tensor(tau, dtype=torch.float32).to(device)

    if a.dim() == 6:
        a   = a.unsqueeze(0)
        tau = tau.unsqueeze(0)

    num_subcarriers    = RESOURCE_GRID.fft_size
    subcarrier_spacing = RESOURCE_GRID.subcarrier_spacing
    freqs = subcarrier_frequencies(num_subcarriers, subcarrier_spacing).to(device)

    return cir_to_ofdm_channel(freqs, a, tau, normalize=True)

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
    def __init__(self, num_conv_channels=128):
        super().__init__()
        num_input_channels = 2 * NUM_BS_ANT + 1
        self._input_conv  = nn.Conv2d(num_input_channels, num_conv_channels, kernel_size=3, padding=1)
        self._res_block_1 = ResidualBlock(num_conv_channels)
        self._res_block_2 = ResidualBlock(num_conv_channels)
        self._res_block_3 = ResidualBlock(num_conv_channels)
        self._res_block_4 = ResidualBlock(num_conv_channels)
        self._output_conv = nn.Conv2d(num_conv_channels, NUM_BITS_PER_SYMBOL, kernel_size=3, padding=1)

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
    def __init__(self, training=True):
        super().__init__()
        self._training      = training
        self._k             = K
        self._n             = N
        self._binary_source = BinarySource()
        if not training:
            self._encoder = LDPC5GEncoder(K, N)
        self._mapper      = Mapper("qam", NUM_BITS_PER_SYMBOL)
        self._rg_mapper   = ResourceGridMapper(RESOURCE_GRID)

        self._neural_rx   = NeuralReceiver().to(device)
        self._rg_demapper = ResourceGridDemapper(RESOURCE_GRID, STREAM_MANAGEMENT)
        self._apply_channel = ApplyOFDMChannel(add_awgn=True)
        if not training:
            self._decoder = LDPC5GDecoder(self._encoder, hard_out=True)

    def forward(self, batch_size, ebno_db, h_freq):
        no = ebnodb2no(ebno_db, num_bits_per_symbol=NUM_BITS_PER_SYMBOL,
                       coderate=CODERATE, resource_grid=RESOURCE_GRID)
        if no.dim() == 0:
            no = no.expand(batch_size)

        if self._training:
            codewords = self._binary_source([batch_size, NUM_UT, NUM_UT_ANT, self._n])
        else:
            bits      = self._binary_source([batch_size, NUM_UT, NUM_UT_ANT, self._k])
            codewords = self._encoder(bits)

        x    = self._mapper(codewords)
        x_rg = self._rg_mapper(x)

        y = self._apply_channel(x_rg, h_freq, no)

        y   = y.squeeze(1)
        llr = self._neural_rx(y, no)
        llr = insert_dims(llr, 2, 1)
        llr = self._rg_demapper(llr)
        llr = llr.reshape(batch_size, NUM_UT, NUM_UT_ANT, self._n)

        if self._training:
            return F.binary_cross_entropy_with_logits(llr, codewords.float())
        else:
            bits_hat = self._decoder(llr)
            return bits, bits_hat, llr


print("Precomputing channels...")
scene = build_scene(SCENE)
print("  Computing channel with all effects...", end=" ", flush=True)
h_freq = compute_h_freq(scene, EFFECT_CONFIG)
print("done")

# Diagnostic
print("\n-- Diagnostic --")
print(f"h_freq shape = {h_freq.shape}, mean abs = {h_freq.abs().mean().item():.4f}")

print("\n-- Test forward pass --")
model_test = OFDMSystemNeuralReceiverRT(training=True)
ebno_db_test = torch.tensor(5.0, device=device)
loss_test = model_test(BATCH_SIZE, ebno_db_test, h_freq)
print(f"Test loss: {loss_test.item():.4f}")


# Step 2: Train
print("\nTraining with all effects")
model = OFDMSystemNeuralReceiverRT(training=True)
optimizer = torch.optim.Adam(model.parameters())

for i in range(NUM_TRAINING_ITERATIONS):
    ebno_db = torch.empty(BATCH_SIZE, device=device).uniform_(EBN0_DB_MIN, EBN0_DB_MAX)
    loss = model(BATCH_SIZE, ebno_db, h_freq)
    optimizer.zero_grad()
    loss.backward()
    optimizer.step()
    if i % 1000 == 0:
        print(f"  {i}/{NUM_TRAINING_ITERATIONS}  Loss: {loss.item():.2E}")

torch.save(model._neural_rx.state_dict(), WEIGHTS_FILE)
print(f"  Saved {WEIGHTS_FILE}")

# Step 3: Evaluate
EBNO_EVAL = np.linspace(EBN0_DB_MIN, EBN0_DB_MAX, 20)
print("\nEvaluating with all effects")
model_eval = OFDMSystemNeuralReceiverRT(training=False)
model_eval._neural_rx.load_state_dict(
    torch.load(WEIGHTS_FILE, weights_only=True)
)
model_eval.eval()
bers = []

with torch.no_grad():
    for ebno_db in EBNO_EVAL:
        ebno_t = torch.tensor(float(ebno_db), device=device)
        bits, bits_hat, _ = model_eval(BATCH_SIZE, ebno_t, h_freq)
        ber = (bits != bits_hat).float().mean().item()
        bers.append(max(ber, 1e-5))
        print(f"  Eb/No={ebno_db:.1f} dB -> BER={ber:.4f}", end="\r")

print(f"\n  Done - min BER: {min(bers):.2e}")

# Step 4: Evaluate throughput
EBNO_SYS = np.linspace(EBN0_DB_MIN, 2.0, 30)
print("\nThroughput with all effects")

model_sys = OFDMSystemNeuralReceiverRT(training=False)
model_sys._neural_rx.load_state_dict(
    torch.load(WEIGHTS_FILE, weights_only=True)
)
model_sys.eval()

tput_list = []

with torch.no_grad():
    for ebno_db in EBNO_SYS:
        ebno_t = torch.tensor(float(ebno_db), device=device)

        bits, bits_hat, _ = model_sys(
            batch_size=BATCH_SIZE,
            ebno_db=ebno_t,
            h_freq=h_freq
        )

        block_errors = (bits != bits_hat).any(dim=-1).float()
        harq_success = 1.0 - block_errors.mean().item()
        tput = harq_success * K
        tput_list.append(tput)
        print(f"  EbN0={ebno_db:.1f}dB | BLER={1-harq_success:.3f} | Tput={tput:.0f}", end="\r")

print(f"\n  Peak throughput: {max(tput_list):.1f} bits/slot")