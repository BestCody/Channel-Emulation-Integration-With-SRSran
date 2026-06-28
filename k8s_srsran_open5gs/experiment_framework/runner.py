#!/usr/bin/env python3

import json
import os
import pathlib
import re
import signal
import socket
import time

from .config import REPO_ROOT, sha256_file
from .failures import FailureRecord
from .lifecycle import (
    AMFMonitor,
    BackgroundCommand,
    CommandExecutor,
    CommandFailure,
    KubernetesLifecycle,
    ResourceMonitor,
    SafetyStop,
)
from .provenance import collect_provenance
from .results import ResultStore, atomic_write_text, write_json
from .summarize import summarize_run
from .traffic import parse_ping


HOST_PYTHON = "/home/h3lou/miniforge3/envs/sionna2/bin/python"


class StudyLock:
    def __init__(self, result_root):
        self.path = pathlib.Path(result_root) / ".stage8.lock"
        self.descriptor = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(self.descriptor, f"pid={os.getpid()}\n".encode())
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.descriptor is not None:
            os.close(self.descriptor)
        self.path.unlink(missing_ok=True)


class PilotRunner:
    def __init__(self, resolved_study, *, namespace="open5gs"):
        self.study = resolved_study
        self.namespace = namespace
        self.store = None
        self.amf = None
        self.resource_monitor = None
        self.backgrounds = []
        self.executor = CommandExecutor(cwd=REPO_ROOT, safety_check=self.check_safety)
        self.lifecycle = KubernetesLifecycle(REPO_ROOT, namespace, self.executor)
        self.deployment_changed = False
        self.current_condition = None
        self.current_trial = None
        self._normal_shutdown = False

    def check_safety(self):
        if self.amf is not None:
            self.amf.check()
        if self.resource_monitor is not None:
            self.resource_monitor.check()
        for background in self.backgrounds:
            background.check()

    def checked_sleep(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check_safety()
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))

    def install_signal_handlers(self):
        def interrupt(signum, frame):
            raise InterruptedError(f"received signal {signum}")
        signal.signal(signal.SIGTERM, interrupt)
        signal.signal(signal.SIGINT, interrupt)

    def preflight(self):
        provenance = self.store.root / "provenance"
        image = self.study["runtime_images"]["stage4"]
        archive = image["archive"]
        actual = self.executor.capture(["sudo", "sha256sum", archive]).split()[0]
        if actual != image["archive_sha256"]:
            raise CommandFailure("Stage 4 image archive checksum mismatch")
        image_list = self.executor.capture(["sudo", "ctr", "-n", "k8s.io", "images", "list"])
        if image["reference"] not in image_list:
            self.executor.run(
                ["sudo", "ctr", "-n", "k8s.io", "images", "import", archive],
                provenance / "stage4-image-import.log",
                timeout=600,
            )
            image_list = self.executor.capture(["sudo", "ctr", "-n", "k8s.io", "images", "list"])
        matching = [line for line in image_list.splitlines() if line.split() and line.split()[0] == image["reference"]]
        if not matching or image["digest"] not in matching[0]:
            raise CommandFailure("Stage 4 image digest is not available in k8s.io")
        atomic_write_text(provenance / "stage4-containerd-image.txt", matching[0] + "\n")

        for condition in self.study["conditions"]:
            overlay = condition.get("overlay")
            if not overlay:
                continue
            rendered = self.executor.capture(["kubectl", "kustomize", str(REPO_ROOT / overlay)])
            output = provenance / "rendered-overlays" / f"{condition['condition_id']}.yaml"
            atomic_write_text(output, rendered + "\n")
            if re.search(r"(?m)^\s*type:\s*NodePort\s*$", rendered):
                raise CommandFailure(f"NodePort found in {condition['condition_id']} overlay")

    def amf_slice(self, start_index):
        samples = self.amf.samples()
        selected = samples[start_index:]
        if not selected:
            selected = samples[-1:]
        if not selected:
            return {}
        return {
            "restart_count_before": selected[0]["restart_count"],
            "restart_count_after": selected[-1]["restart_count"],
            "memory_first_bytes": selected[0]["memory_current"],
            "memory_last_bytes": selected[-1]["memory_current"],
            "memory_max_observed": max(item["memory_current"] for item in selected),
            "memory_limit_bytes": selected[-1]["memory_max"],
            "pod_uids": sorted(set(item["pod_uid"] for item in selected)),
            "sample_count": len(selected),
        }

    def start_port_forward(self, trial_dir):
        self.lifecycle.ue_pod = self.lifecycle.discover_ue()
        background = BackgroundCommand(
            ["kubectl", "port-forward", "-n", self.namespace, f"pod/{self.lifecycle.ue_pod}", "5555:5555"],
            REPO_ROOT,
            pathlib.Path(trial_dir) / "condition/logs/port-forward.log",
        )
        self.backgrounds.append(background)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            background.check()
            try:
                with socket.create_connection(("127.0.0.1", 5555), timeout=0.2):
                    return background
            except OSError:
                time.sleep(0.2)
        raise CommandFailure("port-forward did not become ready")

    def stop_backgrounds(self):
        for background in reversed(self.backgrounds):
            try:
                background.stop()
            except Exception:
                pass
        self.backgrounds.clear()

    def start_continuous_ping(self):
        self.lifecycle.ue_capture(
            "nohup ip netns exec ue1 ping -D -i 0.05 10.41.0.1 >/tmp/stage8-continuous-ping.log 2>&1 </dev/null &"
        )

    def stop_continuous_ping(self, trial_dir):
        self.lifecycle.ue_capture("pkill -TERM -f '[p]ing -D -i 0.05 10.41.0.1' 2>/dev/null || true", check=False)
        self.checked_sleep(1)
        output = self.lifecycle.ue_capture("cat /tmp/stage8-continuous-ping.log 2>/dev/null || true", check=False)
        path = pathlib.Path(trial_dir) / "condition/traffic/continuous-ping.txt"
        atomic_write_text(path, output + "\n")
        return parse_ping(output) if output.strip() else None

    def run_host(self, command, output_log, timeout=300):
        self.executor.run(command, output_log, timeout=timeout)

    def stationary_channel(self, condition, trial_dir):
        channel_dir = pathlib.Path(trial_dir) / "condition/channel"
        dry = channel_dir / "stationary-dry-run.json"
        self.run_host(
            [
                HOST_PYTHON,
                str(REPO_ROOT / "channel_emulation/stationary_sionna_controller.py"),
                "--dry-run",
                "--scene-config", condition["scene_resolved"]["absolute_path"],
                "--output", str(dry),
                "--repeats", "3",
            ],
            channel_dir / "stationary-dry-run.log",
            timeout=300,
        )
        digest = sha256_file(dry)
        atomic_write_text(channel_dir / "stationary-dry-run.sha256", f"{digest}  {dry.name}\n")
        live = channel_dir / "stationary-live-result.json"
        self.run_host(
            [
                HOST_PYTHON,
                str(REPO_ROOT / "channel_emulation/stationary_sionna_controller.py"),
                "--send-report", str(dry),
                "--expected-report-sha256", digest,
                "--endpoint", "tcp://127.0.0.1:5555",
                "--output", str(live),
            ],
            channel_dir / "stationary-live.log",
            timeout=60,
        )
        return {
            "dry_report": str(dry),
            "dry_report_sha256": digest,
            "dry": json.loads(dry.read_text(encoding="utf-8")),
            "live": json.loads(live.read_text(encoding="utf-8")),
        }

    def run_fixed(self, condition, trial_dir):
        channel_dir = pathlib.Path(trial_dir) / "condition/channel"
        if condition["mode"] == "fixed_attenuation":
            channel = {
                "attenuation_db": condition["attenuation_db"],
                "taps": [{"delay": 0, "real": condition["expected_amplitude"], "imag": 0.0}],
                "noise_added": False,
                "interpretation": condition["interpretation"],
            }
        else:
            source = pathlib.Path(condition["tap_profile_resolved"]["absolute_path"])
            channel = json.loads(source.read_text(encoding="utf-8"))
            channel["source_sha256"] = sha256_file(source)
        write_json(channel_dir / "channel.json", channel)
        profile = condition["measurement_profile_resolved"]["values"]["ping"]
        ping = self.lifecycle.ping(
            pathlib.Path(trial_dir) / "condition/traffic/ping.txt",
            count=profile["count"],
            interval=profile["interval_seconds"],
            deadline=profile["deadline_seconds"],
        )
        return {"channel": channel, "ping": ping}

    def run_stationary(self, condition, trial_dir):
        self.start_port_forward(trial_dir)
        self.start_continuous_ping()
        channel = self.stationary_channel(condition, trial_dir)
        self.checked_sleep(10)
        continuous = self.stop_continuous_ping(trial_dir)
        final = condition["measurement_profile_resolved"]["values"]["final_ping"]
        ping = self.lifecycle.ping(
            pathlib.Path(trial_dir) / "condition/traffic/final-ping.txt",
            count=final["count"],
            deadline=final["deadline_seconds"],
        )
        return {"channel": channel, "continuous_ping": continuous, "ping": ping}

    def run_moving(self, condition, trial_dir):
        self.start_port_forward(trial_dir)
        channel_dir = pathlib.Path(trial_dir) / "condition/channel"
        dry = channel_dir / "moving-dry-run.json"
        self.run_host(
            [
                HOST_PYTHON,
                str(REPO_ROOT / "channel_emulation/moving_sionna_controller.py"),
                "--dry-run",
                "--trajectory", condition["trajectory_resolved"]["absolute_path"],
                "--scene-config", condition["scene_resolved"]["absolute_path"],
                "--output", str(dry),
            ],
            channel_dir / "moving-dry-run.log",
            timeout=300,
        )
        digest = sha256_file(dry)
        self.start_continuous_ping()
        live = channel_dir / "moving-live-result.json"
        self.run_host(
            [
                HOST_PYTHON,
                str(REPO_ROOT / "channel_emulation/moving_sionna_controller.py"),
                "--live",
                "--trajectory", condition["trajectory_resolved"]["absolute_path"],
                "--scene-config", condition["scene_resolved"]["absolute_path"],
                "--dry-run-report", str(dry),
                "--expected-dry-run-sha256", digest,
                "--endpoint", "tcp://127.0.0.1:5555",
                "--output", str(live),
            ],
            channel_dir / "moving-live.log",
            timeout=300,
        )
        continuous = self.stop_continuous_ping(trial_dir)
        final = condition["measurement_profile_resolved"]["values"]["final_ping"]
        ping = self.lifecycle.ping(
            pathlib.Path(trial_dir) / "condition/traffic/final-ping.txt",
            count=final["count"],
            deadline=final["deadline_seconds"],
        )
        return {
            "dry_report_sha256": digest,
            "dry": json.loads(dry.read_text(encoding="utf-8")),
            "live": json.loads(live.read_text(encoding="utf-8")),
            "continuous_ping": continuous,
            "ping": ping,
        }

    def noise_command(self, arguments, output, log, timeout=120):
        self.run_host(
            [HOST_PYTHON, str(REPO_ROOT / "channel_emulation/noise_sweep_controller.py"), "--endpoint", "tcp://127.0.0.1:5555", *arguments, "--output", str(output)],
            log,
            timeout=timeout,
        )
        return json.loads(pathlib.Path(output).read_text(encoding="utf-8"))

    def run_noise(self, condition, trial_dir):
        self.start_port_forward(trial_dir)
        channel = self.stationary_channel(condition, trial_dir)
        channel_dir = pathlib.Path(trial_dir) / "condition/channel"
        traffic_dir = pathlib.Path(trial_dir) / "condition/traffic"
        profile = json.loads(pathlib.Path(condition["noise_profile_resolved"]["absolute_path"]).read_text(encoding="utf-8"))
        self.lifecycle.ue_capture("nohup ip netns exec ue1 ping -i 0.02 -c 400 10.41.0.1 >/tmp/stage8-calibration-ping.log 2>&1 </dev/null &")
        calibration = self.noise_command(
            ["calibrate", "--duration", "5", "--interval", "0.05"],
            channel_dir / "signal-calibration.json",
            channel_dir / "signal-calibration.log",
        )
        levels_text = ",".join(str(value) for value in profile["levels_db"])
        plan = channel_dir / "frozen-noise-plan.json"
        self.run_host(
            [
                HOST_PYTHON,
                str(REPO_ROOT / "channel_emulation/noise_sweep_controller.py"),
                "plan",
                "--signal-calibration", str(channel_dir / "signal-calibration.json"),
                "--noise-calibration", condition["noise_calibration_resolved"]["absolute_path"],
                "--levels", levels_text,
                "--output", str(plan),
            ],
            channel_dir / "noise-plan.log",
        )
        level_results = []
        first_failure = None
        try:
            for level in profile["levels_db"]:
                apply_result = self.noise_command(
                    ["apply", "--plan", str(plan), "--snr-db", str(level)],
                    channel_dir / f"level-{level}-apply.json",
                    channel_dir / f"level-{level}-apply.log",
                )
                self.checked_sleep(profile["settle_seconds"])
                measurement = self.noise_command(
                    ["measure", "--duration", "2", "--interval", "0.05"],
                    channel_dir / f"level-{level}-measurement.json",
                    channel_dir / f"level-{level}-measurement.log",
                )
                ping = self.lifecycle.ping(
                    traffic_dir / f"level-{level}-ping.txt",
                    count=profile["measurement_ping_count"],
                    interval=profile["measurement_ping_interval_seconds"],
                    deadline=1,
                )
                sustained = False
                confirmation = None
                if ping["packet_loss_percent"] == 100.0:
                    confirmation = self.lifecycle.ping(
                        traffic_dir / f"level-{level}-confirmation-ping.txt",
                        count=profile["attachment_loss_confirmation_ping_count"],
                        interval=profile["attachment_loss_confirmation_ping_interval_seconds"],
                        deadline=1,
                    )
                    sustained = confirmation["packet_loss_percent"] == 100.0
                level_results.append({
                    "target_snr_db": level,
                    "apply": apply_result,
                    "measurement": measurement,
                    "ping": ping,
                    "confirmation_ping": confirmation,
                    "sustained_attachment_loss": sustained,
                })
                if sustained:
                    first_failure = level
                    break
        finally:
            self.noise_command(
                ["off"],
                channel_dir / "noise-off.json",
                channel_dir / "noise-off.log",
            )
        return {
            "channel": channel,
            "signal_calibration": calibration,
            "noise_plan": json.loads(plan.read_text(encoding="utf-8")),
            "levels": level_results,
            "first_failing_level": first_failure,
            "ping": level_results[-1]["ping"] if level_results else None,
            "throughput": "deferred-no-verified-user-plane-endpoint",
        }

    def run_condition_mode(self, condition, trial_dir):
        if condition["mode"] in {"fixed_attenuation", "fixed_multipath"}:
            return self.run_fixed(condition, trial_dir)
        if condition["mode"] == "stationary_sionna":
            return self.run_stationary(condition, trial_dir)
        if condition["mode"] == "controlled_noise":
            return self.run_noise(condition, trial_dir)
        if condition["mode"] == "moving_sionna":
            return self.run_moving(condition, trial_dir)
        raise ValueError(f"unsupported runtime condition {condition['mode']}")

    def connection_failure_count(self, trial_dir):
        patterns = re.compile(r"error|failed|underflow|overflow|underrun|overrun|timeout|dropped", re.I)
        total = 0
        for path in (pathlib.Path(trial_dir) / "condition/logs").glob("*.log"):
            total += sum(bool(patterns.search(line)) for line in path.read_text(encoding="utf-8", errors="replace").splitlines())
        return total

    def trial_summary(self, condition, trial_number, ue_ip, result, amf_start):
        ping = result.get("ping") or result.get("continuous_ping")
        return {
            "condition_id": condition["condition_id"],
            "trial_number": trial_number,
            "status": "passed",
            "attachment_success": True,
            "ue_ip": ue_ip,
            "ping": ping,
            "connection_failures": 0,
            "amf": self.amf_slice(amf_start),
            "throughput": {
                "status": "deferred",
                "reason": "No verified user-plane throughput endpoint exists",
            },
        }

    def run_condition(self, condition, trial_number):
        trial_dir = self.store.trial(condition["condition_id"], trial_number)
        self.current_condition = condition["condition_id"]
        self.current_trial = trial_number
        write_json(trial_dir / "resolved-condition.json", condition)
        amf_start = len(self.amf.samples())
        failure = None
        try:
            self.deployment_changed = True
            self.lifecycle.apply_overlay(condition["overlay"], trial_dir / "condition/deployment")
            ue_ip = self.lifecycle.start_radio(condition, trial_dir)
            interval = condition["measurement_profile_resolved"]["values"].get("resource_interval_seconds", 1.0)
            self.resource_monitor = ResourceMonitor(self.lifecycle, trial_dir, interval)
            self.resource_monitor.start()
            result = self.run_condition_mode(condition, trial_dir)
            self.resource_monitor.check()
            write_json(trial_dir / "condition/result.json", result)
            self.lifecycle.capture_logs(trial_dir)
            summary = self.trial_summary(condition, trial_number, ue_ip, result, amf_start)
            summary["connection_failures"] = self.connection_failure_count(trial_dir)
            write_json(trial_dir / "summary.json", summary)
        except BaseException as error:
            failure = error
            record = FailureRecord(
                category="amf_safety" if isinstance(error, SafetyStop) else "unexpected",
                message=str(error),
                condition_id=condition["condition_id"],
                trial_number=trial_number,
                command=getattr(error, "command", None),
                return_code=getattr(error, "return_code", None),
            )
            write_json(trial_dir / "failure.json", record.to_dict())
        finally:
            if self.resource_monitor is not None:
                try:
                    self.resource_monitor.stop()
                finally:
                    self.resource_monitor = None
            self.stop_backgrounds()
            try:
                self.lifecycle.capture_logs(trial_dir)
            except Exception:
                pass
            try:
                with self.executor.without_safety_checks():
                    self.lifecycle.restore(trial_dir / "restoration")
                self.deployment_changed = False
            except BaseException as restore_error:
                write_json(
                    trial_dir / "restoration/failure.json",
                    FailureRecord(
                        category="restoration",
                        message=str(restore_error),
                        condition_id=condition["condition_id"],
                        trial_number=trial_number,
                    ).to_dict(),
                )
                raise
        if failure is not None:
            recovery = self.store.root / "failure-recovery" / f"{condition['condition_id']}-trial-{trial_number:03d}"
            if isinstance(failure, SafetyStop):
                write_json(recovery / "summary.json", {
                    "status": "not-run-amf-safety-stop",
                    "reason": "The study stopped and restored immediately; starting another radio test at an AMF safety threshold would violate the stop condition",
                })
            else:
                recovery_result = self.lifecycle.baseline_check(recovery, ping_count=20)
                write_json(recovery / "summary.json", recovery_result)
            raise failure

    def run(self):
        self.install_signal_handlers()
        with StudyLock(self.study["result_root"]):
            self.store = ResultStore(self.study["result_root"], self.study["study_id"])
            self.store.write_json("resolved-study.json", self.study)
            collect_provenance(self.store.root / "provenance", REPO_ROOT, self.study)
            self.lifecycle.save_original(self.store.root / "provenance/original-cluster-state")
            self.preflight()
            interval = min(
                condition["measurement_profile_resolved"]["values"].get("amf_interval_seconds", 0.5)
                for condition in self.study["conditions"]
            )
            self.amf = AMFMonitor(REPO_ROOT, self.namespace, self.store.root / "monitoring", interval)
            self.amf.start()
            try:
                baseline_condition = self.study["conditions"][0]
                baseline_trial = self.store.trial("baseline", 1)
                write_json(baseline_trial / "resolved-condition.json", baseline_condition)
                baseline_start = len(self.amf.samples())
                pre = self.lifecycle.baseline_check(
                    self.store.root / "pre-pilot-baseline",
                    ping_count=baseline_condition["measurement_profile_resolved"]["values"]["ping"]["count"],
                    monitor_trial_dir=baseline_trial,
                )
                pre.update({
                    "condition_id": "baseline",
                    "trial_number": 1,
                    "serves_as_pre_pilot_baseline": True,
                    "connection_failures": 0,
                    "amf": self.amf_slice(baseline_start),
                })
                write_json(baseline_trial / "summary.json", pre)
                write_json(baseline_trial / "condition/result.json", pre)
                write_json(self.store.root / "pre-pilot-baseline/summary.json", pre)
                if pre["status"] != "passed":
                    raise CommandFailure("pre-pilot baseline failed")

                for trial_number in range(2, self.study["trials_per_condition"] + 1):
                    trial = self.store.trial("baseline", trial_number)
                    write_json(trial / "resolved-condition.json", baseline_condition)
                    start = len(self.amf.samples())
                    result = self.lifecycle.baseline_check(
                        trial / "condition/logs/baseline",
                        ping_count=baseline_condition["measurement_profile_resolved"]["values"]["ping"]["count"],
                        monitor_trial_dir=trial,
                    )
                    result.update({
                        "condition_id": "baseline",
                        "trial_number": trial_number,
                        "connection_failures": 0,
                        "amf": self.amf_slice(start),
                    })
                    write_json(trial / "condition/result.json", result)
                    write_json(trial / "summary.json", result)
                    if result["status"] != "passed":
                        raise CommandFailure(f"baseline trial {trial_number} failed")

                for condition in self.study["conditions"][1:]:
                    for trial_number in range(1, self.study["trials_per_condition"] + 1):
                        self.run_condition(condition, trial_number)

                final = self.lifecycle.baseline_check(self.store.root / "post-pilot-baseline", ping_count=100)
                write_json(self.store.root / "post-pilot-baseline/summary.json", final)
                if final["status"] != "passed":
                    raise CommandFailure("post-pilot baseline failed")
                self._normal_shutdown = True
            finally:
                self.stop_backgrounds()
                if self.resource_monitor is not None:
                    self.resource_monitor.stop()
                    self.resource_monitor = None
                if self.deployment_changed:
                    with self.executor.without_safety_checks():
                        self.lifecycle.restore(self.store.root / "emergency-restoration")
                    self.deployment_changed = False
                if self.amf is not None:
                    self.amf.stop()

            summarize_run(self.store.root)
            self.store.write_checksums()
            return self.store.root
