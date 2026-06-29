# Benchmark-parameters cleanup report

Date: 2026-06-29

Reconciled the experiment orchestration with the rewritten streaming /
GPU channel engine. Two files changed:

- `experiment_framework/runner.py` (orchestration glue)
- `experiments/benchmark-parameters.json` (parameters)

The actual Sionna RT solver parameters live in
`channel_emulation/scenes/default_scene.json`, **not** here, and were
**not touched**. Verified: 116 tests pass
(`python3 -m unittest discover -s tests`); JSON valid; no references to
the removed names remain.

---

## Parameters ERASED (all from the `channel` block)

| Parameter | Old value | Why it is now dead |
|---|---|---|
| `activation_lead_ms` | 100.0 | Drove `--activation-lead-ms`; the sample-accurate activation/transaction layer was deleted in the rewrite. Stationary controller no longer accepts the flag. |
| `activation_timeout_seconds` | 8.0 | Drove `--activation-timeout` (waited for an activation ack). No transaction layer to ack. |
| `movement_lead_ms` | 250.0 | Drove `--movement-lead-ms` (scheduling lead for sample-accurate trajectory activation). The moving controller now streams interpolated CIRs in order — no lead. |
| `late_margin_ms` | 10.0 | Drove `--late-margin-ms` (lateness tolerance for `activate_at_sample`). No activation timestamps exist anymore. |
| `noise_measurement_duration_seconds` | 2.0 | Drove the per-level `measure` subcommand. Noise is now open-loop (analytic sigma); there is no `measure` subcommand. |
| `noise_measurement_interval_seconds` | 0.05 | Same as above. |

6 parameters removed.

---

## Parameters KEPT

### Scene / placement (explicitly preserved)

The `scene` block is untouched and still drives `runner._placement_args`:

- `scene.min_link_distance_m` = 0.05
- `scene.placement_seed` = 42
- `scene.randomize_positions` = true

> Sionna RT solver params (`max_depth`, `max_num_paths_per_src`,
> `samples_per_src`, `synthetic_array`, `seed`, propagation toggles) are
> in `channel_emulation/scenes/default_scene.json` and were not modified.

### `channel` block — still in use

- `continuous_ping_interval_seconds`, `continuous_ping_start_sleep_seconds`
- `control_endpoint`
- `final_hold_seconds` (moving controller `--final-hold-seconds`)
- `moving_dry_timeout_seconds`, `moving_live_timeout_seconds`
- `signal_calibration_duration_seconds`, `signal_calibration_interval_seconds`
  (feed the signal-power `calibrate` step; renamed from `noise_calibration_*`)
- `signal_calibration_ping_count`, `signal_calibration_ping_interval_seconds`
  (background ping during signal calibration; renamed from `noise_calibration_*`)
- `noise_command_timeout_seconds`
- `port_forward`, `port_forward_host`, `port_forward_port`,
  `port_forward_ready_seconds`
- `require_absolute_coefficients` (validated in `config.py`)
- `stationary_dry_timeout_seconds`, `stationary_live_timeout_seconds`,
  `stationary_repeats`

### All other top-level blocks — kept unchanged

`host_python`, `kubernetes`, `logs`, `monitoring` (incl. `enable_gpu`),
`radio`, `result_root`, `results_must_be_outside_repo`,
`runtime_images`, `study`, `throughput`, `timeouts`.

---

## Runner code changes (the loose-end)

- Stationary live call: dropped `--activation-lead-ms` / `--activation-timeout`.
- Moving live call: dropped `--movement-lead-ms` / `--late-margin-ms`
  (kept `--final-hold-seconds`).
- Noise `plan` call: dropped `--noise-calibration` and the
  `condition["noise_calibration_resolved"]` lookup (a guaranteed
  `KeyError` — `config.py` stopped resolving that artifact in the rewrite).
- Noise per-level loop: replaced the dead `measure` subcommand with a
  `status` snapshot (signal power + channel liveness); the level-result
  key `measurement` became `status`.

---

## Follow-ups completed (second pass)

- **Renamed** `noise_calibration_*` -> `signal_calibration_*` (4 keys in
  `benchmark-parameters.json` + 4 refs in `runner.py`). The `calibrate`
  subcommand measures **signal** power (noise off) so the plan can
  compute sigma; the names now match.
- **Tidied `summarize.py`** to the streaming / open-loop schemas:
  - `noise-levels.csv`: dropped `downlink/uplink_measured_snr_db` (open-loop
    noise does not measure achieved SNR); added
    `downlink/uplink_applied_sigma` (the exact sigma from the frozen plan).
  - `channel-updates.csv`: dropped `ack_rtt_ms`, `schedule_us`,
    `downlink/uplink_activation_error_samples` (transaction layer gone);
    kept `sequence`, added `tap_count`.
  - `moving-positions.csv`: streaming records now carry `{index, alpha,
    tap_count}`, so the table reflects those; dropped the absent
    position / timing / phase / schedule columns.
  - Removed two now-empty plots: `update-ack.svg` (no ack RTT) and
    `moving-phase.svg` (no per-step position/phase). File names asserted
    by tests are unchanged.

## Still source-limited (not a summarize bug)

The streaming moving controller records only `{index, alpha, tap_count}`
per step — it no longer logs per-step UE position, Sionna solve time, or
unwrapped phase. So position-vs-phase analysis and moving solve timing
are gone at the **source**. Restoring them is a moving-controller change
(re-add those fields to each record), not a summarize change. Say the
word if you want that.
