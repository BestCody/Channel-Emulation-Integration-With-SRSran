# Channel Emulation for srsRAN

This project lets you run a small 5G network entirely in software and test it
over **realistic radio channels** that are computed with ray tracing instead of
real antennas. It connects three existing tools:

- **srsRAN** — a software 5G base station (gNB) and phone (UE).
- **Open5GS** — a software 5G core network.
- **Sionna RT** — NVIDIA's ray tracer, which calculates how a radio signal
  bounces around a scene (walls, reflections, distance) and arrives at the
  receiver.

## What it actually does

The base station and the phone don't use real antennas. They exchange their
radio samples over a local network link (ZeroMQ), like a virtual cable. This
project inserts a small program in the middle of that cable. For every scene we
ask Sionna, *"if the signal travelled through this room, how would it come out
the other side?"* Sionna returns a set of numbers (the channel), and the program
multiplies the passing signal by those numbers on the GPU. The base station and
phone behave exactly as if they were really transmitting through that room.

On top of this, there is an **evaluation tool** that runs the whole thing for
you: it sets up the radio, applies the channel, sends test traffic (pings),
records what happened, and writes the results to a folder. It can also measure
link quality with a small neural receiver.

```
  gNB (srsRAN) <--- virtual cable ---> [ channel program ] <---> UE (srsRAN)
                                              ^
                                              |
                                     channel numbers from
                                      Sionna ray tracing
```

## What you need

- A machine running **Ubuntu 22.04**.
- An **NVIDIA GPU** with recent drivers and CUDA (used for the channel math and
  the neural receiver).
- **Python 3.11**.
- A Kubernetes storage class named **`longhorn`** for MongoDB's persistent
  volume, or an edited MongoDB overlay that uses a storage class available on
  your cluster.

## Setting it up after cloning

```bash
git clone <this-repo-url>
cd sionna-srsran
```

**1. Set up the Kubernetes cluster.**
This installer prepares the single-node cluster and its networking (containerd,
kubeadm, Flannel, Multus). It does not deploy the 5G network itself.

```bash
cd srsran_open5gs/testbed-automator
./install.sh
cd ..
```

**2. Deploy the 5G core and the radio.**
These are applied as Kubernetes overlays. Apply the core first, then the radio:

```bash
kubectl create namespace open5gs --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n open5gs -k configs/open5gs/networks5g   # virtual networks
kubectl apply -n open5gs -k configs/open5gs/mongodb       # subscriber database
kubectl apply -n open5gs -k configs/open5gs/open5gs       # 5G core network
kubectl apply -n open5gs -k configs/srsRAN/srsran-gnb     # base station (gNB)
kubectl apply -n open5gs -k configs/ues/srsue             # phone (UE)
```

The phone must be registered as a subscriber in the core before it can connect.
The subscriber list (including the one the built-in phone uses) is already in
`configs/open5gs/data/subscribers.json`; you just load it into the core's
database. Run this from the `mongo-tools` folder — it opens a temporary
connection to the database and inserts the subscribers:

```bash
python3 -m pip install pymongo
cd configs/open5gs/mongo-tools
python3 modify-subscribers.py add
python3 list-subscribers.py
cd ../../..
```

The list command prints the registered phones.

**3. Create the Python environment for the ray tracing and neural receiver.**
These run on the host (not inside Kubernetes) because they use the GPU. There is
no requirements file yet; install the packages the code expects:

```bash
python3.11 -m venv ~/sionna-env
source ~/sionna-env/bin/activate
pip install "sionna==2.0.1" "sionna-rt==2.0.1" pyzmq numpy
# Install a CUDA build of PyTorch that matches your GPU/driver:
#   see https://pytorch.org/get-started/locally/
```

**4. Check that the network pods are running.**

```bash
kubectl get pods -n open5gs
```

You should see the Open5GS core, the gNB, and the UE pods in `Running` state.

## Running an evaluation

All commands are run from the `srsran_open5gs/` folder. The tool has four steps:

```bash
cd srsran_open5gs

# 1. Check the configuration is valid (no changes made)
python3 bin/evaluation-experiment.py resolve experiments/studies/neural-base.json --output /tmp/resolved.json

# 2. Print what a run would do, step by step (no changes made)
python3 bin/evaluation-experiment.py plan experiments/studies/neural-base.json

# 3. Actually run it (this starts the radio and changes the cluster)
python3 bin/evaluation-experiment.py run experiments/studies/neural-base.json --namespace open5gs --confirm-live

# 4. Rebuild the tables and plots from a finished run
python3 bin/evaluation-experiment.py summarize ../results/evaluation/neural-base/<run-id>
```

Results are written to `results/evaluation/<study>/<run-id>/`, including per-test
tables (CSV), plots (SVG), the logs, and a copy of the exact settings used.

A **study** is a JSON file describing what to test (for example
`experiments/studies/neural-base.json`). You normally don't edit these by
hand — you override values from the terminal instead, as shown below.

## Terminal options

The override flags below go on the `resolve`, `plan`, and `run` commands.
Command-specific flags are noted in the table.

| Option | What it does |
|---|---|
| `<study file>` | Path to the study JSON to run (required, first argument). |
| `--namespace` | (`run` only) The Kubernetes namespace the network runs in (usually `open5gs`). |
| `--confirm-live` | (`run` only) Confirms you understand it will start the radio and change the cluster. Without it, nothing runs. |
| `--parameters FILE` | Load extra settings from another JSON file. Can be given more than once. |
| `--output FILE` | (`resolve` only) Where to write the checked, fully-expanded config. |

### Changing settings from the terminal

You can override almost any value without editing files. Each override is
`KEY=VALUE`, and values are read as JSON (so `true`, `2`, `[1,0,2]`, `"box"`).
You can repeat any of these flags to set several values.

**`--set` — run and network settings**

| Key | Description |
|---|---|
| `radio.ue_number` | How many phones (UEs) to simulate at once. |
| `trials_per_condition` | How many times to repeat each test. |
| `scene.randomize_positions` | `true` places the antennas randomly; `false` uses the fixed positions in the scene. |
| `scene.placement_seed` | The random seed for placement, so a random run can be repeated exactly. |
| `scene.min_link_distance_m` | Smallest allowed distance (in metres) between transmitter and receiver when placing them randomly. |

Example: `--set radio.ue_number=2 --set trials_per_condition=3`

**`--condition-set` — what channel to test**

| Key | Description |
|---|---|
| `propagation.los` | Turn on the direct line-of-sight path. |
| `propagation.specular_reflection` | Turn on mirror-like reflections off flat surfaces. |
| `propagation.diffuse_reflection` | Turn on scattered reflections off rough surfaces. |
| `propagation.refraction` | Turn on signal bending through materials. |
| `propagation.diffraction` | Turn on bending around edges. |
| `throughput.status` | `neural_receiver` to measure link throughput, or `deferred` to skip it. |

By default every propagation effect is off; you switch on the ones you want.

Example: `--condition-set propagation.los=true --condition-set propagation.specular_reflection=true`

**`--scene-set` — the physical scene**

| Key | Description |
|---|---|
| `scene` | Which built-in Sionna room to use, e.g. `"box"` or `"munich"`. |
| `transmitter.position` | Base station location as `[x, y, z]` in metres. |
| `receiver.position` | Phone location as `[x, y, z]` in metres. |
| `receiver.velocity` | Phone velocity as `[vx, vy, vz]` in m/s. Adds Doppler to the neural-receiver measurement. Default `[0,0,0]` (stationary). |
| `antenna.pattern` | Antenna shape, e.g. `"iso"` (equal in all directions). |
| `antenna.polarization` | Antenna polarization: `"V"` or `"H"`. `"cross"` is **not supported right now** 
| `solver.max_depth` | How many bounces to trace (higher = more detail, slower). |
| `solver.samples_per_src` | How many rays to shoot (higher = more accurate, slower). |
| `solver.seed` | Random seed for the ray tracing. |

Example: `--scene-set scene='"munich"' --scene-set 'transmitter.position=[-1.5,0,2]'`

**`--profile-set` — how the test is measured**

| Key | Description |
|---|---|
| `final_ping.count` | How many ping packets to send at the end of the test. |
| `final_ping.deadline_seconds` | How long to wait for those pings before giving up. |
| `attachment_timeout_seconds` | How long to wait for the phone to connect before failing. |
| `amf_interval_seconds` | How often to record the core network's memory use. |
| `resource_interval_seconds` | How often to record CPU and GPU use. |

Example: `--profile-set final_ping.count=20`

### A full example

Run one line-of-sight test with reflections, in the Munich scene, with the
phone moving at 10 m/s and 20 final pings:

```bash
python3 bin/evaluation-experiment.py run experiments/studies/neural-base.json \
  --namespace open5gs --confirm-live \
  --condition-set propagation.los=true \
  --condition-set propagation.specular_reflection=true \
  --scene-set scene='"munich"' \
  --scene-set 'receiver.velocity=[10,0,0]' \
  --profile-set final_ping.count=20
```

Every override you use is recorded in the results folder, so a run can always be
reproduced.
