# Stage 8 experiment framework

The pilot compares the completed baseline, fixed attenuation, fixed multipath,
stationary Sionna, controlled noise, and moving Sionna modes. It contains one
trial per condition.

Generated results are stored only below:

```text
/home/h3lou/sionna-srsran/results/stage8/
```

The pilot performs one complete baseline check before the study. That check is
also the baseline condition trial. Successful non-baseline conditions are
followed by deployment restoration validation without reconnecting the radio.
One complete baseline check runs after the pilot. A failed condition triggers
restoration and an immediate baseline recovery check, unless the AMF safety
threshold itself fired; in that case the framework restores and stops without
adding more radio load.

AMF memory and identity are monitored continuously. The pilot stops on a
restart, pod/container identity change, 90 percent memory use, or 128 MiB growth
from the pilot AMF baseline.

Throughput remains deferred because no verified user-plane endpoint exists.
Reports always show individual trials. Confidence intervals are intentionally
not generated for the planned small trial counts.

Offline commands:

```bash
python3 bin/stage8-experiment.py resolve \
  experiments/studies/stage8-pilot.json \
  --output /tmp/stage8-resolved.json

python3 bin/stage8-experiment.py plan \
  experiments/studies/stage8-pilot.json
```

Proposed live pilot command, to be run only after review:

```bash
python3 bin/stage8-experiment.py run \
  experiments/studies/stage8-pilot.json \
  --confirm-live
```
