#!/usr/bin/env python3

import json
import os
import pathlib
import signal
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass

from .results import atomic_write_text, write_json
from .traffic import parse_ping


UE_SELECTOR = "app=srsran,component=ue,name=ue1"
GNB_SELECTOR = "app=srsran,component=gnb"


class CommandFailure(RuntimeError):
    def __init__(self, message, command=None, return_code=None):
        super().__init__(message)
        self.command = command
        self.return_code = return_code


class SafetyStop(RuntimeError):
    pass


class CommandExecutor:
    def __init__(self, *, cwd, safety_check=None):
        self.cwd = str(cwd)
        self.safety_check = safety_check

    def check_safety(self):
        if self.safety_check is not None:
            self.safety_check()

    @contextmanager
    def without_safety_checks(self):
        previous = self.safety_check
        self.safety_check = None
        try:
            yield
        finally:
            self.safety_check = previous

    def run(self, command, log_path, *, timeout=300, check=True, env=None):
        log_path = pathlib.Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        with log_path.open("w", encoding="utf-8") as output:
            process = subprocess.Popen(
                command,
                cwd=self.cwd,
                env=env,
                text=True,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                while process.poll() is None:
                    self.check_safety()
                    if timeout is not None and time.monotonic() - started > timeout:
                        raise CommandFailure("command timed out", list(command), None)
                    time.sleep(0.2)
            except BaseException:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise
        self.check_safety()
        if check and process.returncode != 0:
            raise CommandFailure(
                f"command failed with exit code {process.returncode}",
                list(command),
                process.returncode,
            )
        return process.returncode

    def capture(self, command, *, timeout=30, check=True):
        self.check_safety()
        started = time.monotonic()
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output:
            process = subprocess.Popen(
                command,
                cwd=self.cwd,
                text=True,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                while process.poll() is None:
                    self.check_safety()
                    if timeout is not None and time.monotonic() - started > timeout:
                        raise CommandFailure("command timed out", list(command), None)
                    time.sleep(0.1)
            except BaseException:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise
            output.seek(0)
            captured = output.read()
        self.check_safety()
        if check and process.returncode != 0:
            raise CommandFailure(
                captured.strip() or "command failed",
                list(command),
                process.returncode,
            )
        return captured.strip()


@dataclass(frozen=True)
class OriginalUEState:
    replicas: int
    configmap: str
    image: str
    pull_policy: str


class AMFMonitor:
    def __init__(self, repo_root, namespace, output_dir, interval):
        self.repo_root = pathlib.Path(repo_root)
        self.namespace = namespace
        self.output_dir = pathlib.Path(output_dir)
        self.interval = float(interval)
        self.process = None
        self.stopping = False

    @property
    def samples_path(self):
        return self.output_dir / "amf-memory.jsonl"

    @property
    def summary_path(self):
        return self.output_dir / "amf-monitor-summary.json"

    def start(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        command = [
            "/home/h3lou/miniforge3/envs/sionna2/bin/python",
            str(self.repo_root / "channel_emulation/amf_memory_monitor.py"),
            "--namespace", self.namespace,
            "--interval", str(self.interval),
            "--output", str(self.samples_path),
            "--summary", str(self.summary_path),
        ]
        log = (self.output_dir / "amf-monitor.log").open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            command,
            cwd=self.repo_root,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self._log_handle = log
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.samples_path.exists() and self.samples_path.stat().st_size:
                return
            if self.process.poll() is not None:
                break
            time.sleep(0.2)
        self.check()
        raise SafetyStop("AMF monitor did not produce its baseline sample")

    def check(self):
        if self.process is None:
            return
        return_code = self.process.poll()
        if return_code is None:
            return
        if self.stopping and return_code == 0:
            return
        reason = "AMF monitor stopped unexpectedly"
        if self.summary_path.exists():
            try:
                summary = json.loads(self.summary_path.read_text(encoding="utf-8"))
                reason = summary.get("reason") or reason
            except (OSError, json.JSONDecodeError):
                pass
        raise SafetyStop(reason)

    def samples(self):
        values = []
        if not self.samples_path.exists():
            return values
        for line in self.samples_path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "memory_current" in value:
                values.append(value)
        return values

    def stop(self):
        if self.process is None:
            return
        self.stopping = True
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=10)
        self._log_handle.close()
        if self.process.returncode != 0:
            self.stopping = False
            self.check()


class BackgroundCommand:
    def __init__(self, command, cwd, log_path):
        self.log = pathlib.Path(log_path).open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            command,
            cwd=cwd,
            text=True,
            stdout=self.log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def check(self):
        if self.process.poll() is not None:
            raise CommandFailure("background command stopped", return_code=self.process.returncode)

    def stop(self):
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()
        self.log.close()


class ResourceMonitor:
    def __init__(self, lifecycle, trial_dir, interval):
        self.lifecycle = lifecycle
        self.trial_dir = pathlib.Path(trial_dir)
        self.interval = float(interval)
        self.stop_event = threading.Event()
        self.failure = None
        self.thread = None
        self.gpu = None

    def start(self):
        monitoring = self.trial_dir / "condition/monitoring"
        monitoring.mkdir(parents=True, exist_ok=True)
        self.gpu = BackgroundCommand(
            [
                "nvidia-smi",
                "--query-gpu=timestamp,index,uuid,utilization.gpu,utilization.memory,memory.used,power.draw,temperature.gpu",
                "--format=csv",
                "-lms", "100",
            ],
            self.lifecycle.repo_root,
            monitoring / "gpu.csv",
        )
        self.thread = threading.Thread(target=self._run, name="stage8-resource-monitor", daemon=True)
        self.thread.start()

    def _run(self):
        output = self.trial_dir / "condition/monitoring/processes.jsonl"
        identity = self.lifecycle.radio_identity()
        while not self.stop_event.wait(self.interval):
            try:
                current = self.lifecycle.radio_identity()
                if current != identity:
                    raise SafetyStop(f"radio process identity changed: {identity} -> {current}")
                sample = {
                    "time_ns": time.time_ns(),
                    "identity": current,
                    "ue_ps": self.lifecycle.ue_capture(
                        f"ps -p '{current['flowgraph_pid']}','{current['ue_pid']}' -o pid,ppid,pcpu,pmem,rss,vsz,etime,args"
                    ),
                    "gnb_ps": self.lifecycle.gnb_capture(
                        f"ps -p '{current['gnb_pid']}' -o pid,ppid,pcpu,pmem,rss,vsz,etime,args"
                    ),
                }
                with output.open("a", encoding="utf-8") as destination:
                    destination.write(json.dumps(sample, sort_keys=True) + "\n")
            except BaseException as error:
                self.failure = error
                return

    def check(self):
        if self.failure:
            raise self.failure
        if self.gpu:
            self.gpu.check()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
        if self.gpu:
            self.gpu.stop()


class KubernetesLifecycle:
    def __init__(self, repo_root, namespace, executor):
        self.repo_root = pathlib.Path(repo_root)
        self.namespace = namespace
        self.executor = executor
        self.original = None
        self.ue_pod = None
        self.gnb_pod = None

    def kubectl(self, *arguments):
        return ["kubectl", *arguments]

    def capture(self, *arguments, check=True):
        return self.executor.capture(self.kubectl(*arguments), check=check)

    def discover_ue(self):
        return self.capture(
            "get", "pods", "-n", self.namespace,
            "-l", UE_SELECTOR,
            "--field-selector=status.phase=Running",
            "-o", "jsonpath={.items[0].metadata.name}",
        )

    def discover_gnb(self):
        return self.capture(
            "get", "pods", "-n", self.namespace,
            "-l", GNB_SELECTOR,
            "--field-selector=status.phase=Running",
            "-o", "jsonpath={.items[0].metadata.name}",
        )

    def save_original(self, output_dir):
        output_dir = pathlib.Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        self.executor.run(
            self.kubectl("get", "deployment", "srsran-ue1", "-n", self.namespace, "-o", "yaml"),
            output_dir / "original-deployment.yaml",
        )
        def value(expression):
            return self.capture("get", "deployment", "srsran-ue1", "-n", self.namespace, "-o", f"jsonpath={expression}")
        self.original = OriginalUEState(
            replicas=int(value("{.spec.replicas}")),
            configmap=value('{.spec.template.spec.volumes[?(@.name=="ue-volume")].configMap.name}'),
            image=value('{.spec.template.spec.containers[?(@.name=="ue")].image}'),
            pull_policy=value('{.spec.template.spec.containers[?(@.name=="ue")].imagePullPolicy}'),
        )
        write_json(output_dir / "original-state.json", asdict(self.original))
        return self.original

    def ue_capture(self, script, check=True):
        self.ue_pod = self.ue_pod or self.discover_ue()
        return self.capture("exec", "-n", self.namespace, self.ue_pod, "-c", "ue", "--", "bash", "-lc", script, check=check)

    def gnb_capture(self, script, check=True):
        self.gnb_pod = self.gnb_pod or self.discover_gnb()
        return self.capture("exec", "-n", self.namespace, self.gnb_pod, "-c", "gnb", "--", "bash", "-lc", script, check=check)

    def stop_radio(self):
        self.ue_pod = self.capture(
            "get", "pods", "-n", self.namespace, "-l", UE_SELECTOR,
            "--field-selector=status.phase=Running",
            "-o", "jsonpath={.items[0].metadata.name}",
            check=False,
        )
        self.gnb_pod = self.capture(
            "get", "pods", "-n", self.namespace, "-l", GNB_SELECTOR,
            "--field-selector=status.phase=Running",
            "-o", "jsonpath={.items[0].metadata.name}",
            check=False,
        )
        if self.ue_pod:
            self.capture(
                "exec", "-n", self.namespace, self.ue_pod, "-c", "ue", "--",
                "bash", "-lc",
                "pkill -TERM -f '[p]ing .*10.41.0.1' 2>/dev/null || true; "
                "pkill -INT -f '[/]opt/srsRAN_4G/build/srsue/src/srsue' 2>/dev/null || true",
                check=False,
            )
        time.sleep(2)
        if self.gnb_pod:
            self.capture(
                "exec", "-n", self.namespace, self.gnb_pod, "-c", "gnb", "--",
                "bash", "-lc", "pkill -INT -f '[/]srsran/gnb' 2>/dev/null || true",
                check=False,
            )
        time.sleep(2)
        if self.ue_pod:
            self.capture(
                "exec", "-n", self.namespace, self.ue_pod, "-c", "ue", "--",
                "bash", "-lc",
                "pkill -INT -f '[m]ulti_ue_.*channel.py|[m]ulti_ue_scenario.py' 2>/dev/null || true; "
                "pkill -TERM -f '[t]ail -f /dev/null' 2>/dev/null || true",
                check=False,
            )

    def wait_no_ue(self, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.capture("get", "pods", "-n", self.namespace, "-l", UE_SELECTOR, "--no-headers", check=False)
            if not value.strip():
                return
            time.sleep(1)
        raise CommandFailure("UE pod did not disappear")

    def apply_overlay(self, overlay, output_dir):
        if self.original is None:
            raise RuntimeError("original deployment state was not saved")
        output_dir = pathlib.Path(output_dir)
        self.stop_radio()
        self.executor.run(
            self.kubectl("scale", "deployment/srsran-ue1", "-n", self.namespace, "--replicas=0"),
            output_dir / "scale-zero.log",
        )
        self.wait_no_ue()
        self.executor.run(
            self.kubectl("apply", "-k", str(self.repo_root / overlay), "-n", self.namespace),
            output_dir / "apply.log",
        )
        self.executor.run(
            self.kubectl("scale", "deployment/srsran-ue1", "-n", self.namespace, f"--replicas={self.original.replicas}"),
            output_dir / "scale.log",
        )
        self.executor.run(
            self.kubectl("rollout", "status", "deployment/srsran-ue1", "-n", self.namespace, "--timeout=300s"),
            output_dir / "rollout.log",
            timeout=310,
        )
        self.executor.run(
            self.kubectl("wait", "--for=condition=Ready", "pod", "-l", UE_SELECTOR, "-n", self.namespace, "--timeout=300s"),
            output_dir / "ready.log",
            timeout=310,
        )
        self.ue_pod = self.discover_ue()
        self.gnb_pod = self.discover_gnb()
        wrapper = self.ue_capture("pgrep -af '[m]ulti_ue|[/]opt/srsRAN_4G/build/srsue/src/srsue' || true")
        atomic_write_text(output_dir / "wrapper-process-check.txt", wrapper + ("\n" if wrapper else ""))
        if wrapper:
            raise CommandFailure("replacement UE pod started radio processes automatically")

    def start_radio(self, condition, trial_dir):
        trial_dir = pathlib.Path(trial_dir)
        launcher = condition["launcher"]
        self.ue_capture(f"nohup {launcher} >/tmp/stage8-gnuradio.log 2>&1 </dev/null &")
        time.sleep(5)
        flow = self.ue_capture("pgrep -f '[m]ulti_ue_.*channel.py' | head -n1")
        if not flow:
            raise CommandFailure("GNU Radio process did not remain running")
        self.gnb_capture("nohup /srsran/config/start_gnb.sh >/tmp/stage8-gnb.log 2>&1 </dev/null &")
        time.sleep(3)
        self.ue_capture("nohup /srsran/config/start_ue.sh 1 >/tmp/stage8-ue.log 2>&1 </dev/null &")
        timeout = condition["measurement_profile_resolved"]["values"].get("attachment_timeout_seconds", 120)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.ue_capture("grep -q 'PDU Session Establishment successful' /tmp/stage8-ue.log", check=False) == "":
                if self.ue_capture("grep -q 'PDU Session Establishment successful' /tmp/stage8-ue.log; printf '%s' $?", check=False) == "0":
                    break
            if not self.ue_capture("pgrep -f '[/]opt/srsRAN_4G/build/srsue/src/srsue' || true"):
                raise CommandFailure("UE process exited before attachment")
            time.sleep(1)
        else:
            raise CommandFailure("UE attachment timed out")
        self.ue_capture("ip netns exec ue1 ip route replace default via 10.41.0.1")
        ue_ip = self.ue_capture("ip netns exec ue1 ip -4 -o addr show dev tun_srsue | awk '{print $4}'")
        atomic_write_text(trial_dir / "condition/ue-ip.txt", ue_ip + "\n")
        return ue_ip

    def radio_identity(self):
        self.ue_pod = self.discover_ue()
        self.gnb_pod = self.discover_gnb()
        return {
            "ue_pod": self.ue_pod,
            "pod_uid": self.capture("get", "pod", self.ue_pod, "-n", self.namespace, "-o", "jsonpath={.metadata.uid}"),
            "flowgraph_pid": self.ue_capture("pgrep -f '[m]ulti_ue_.*channel.py|[m]ulti_ue_scenario.py' | head -n1"),
            "ue_pid": self.ue_capture("pgrep -f '[/]opt/srsRAN_4G/build/srsue/src/srsue' | head -n1"),
            "gnb_pod": self.gnb_pod,
            "gnb_pid": self.gnb_capture("pgrep -f '[/]srsran/gnb' | head -n1"),
        }

    def ping(self, output_path, *, count=100, interval=0.1, deadline=2):
        output = self.ue_capture(f"ip netns exec ue1 ping -D -i {interval:g} -c {count} -W {deadline} 10.41.0.1", check=False)
        atomic_write_text(output_path, output + "\n")
        return parse_ping(output)

    def capture_logs(self, trial_dir):
        logs = pathlib.Path(trial_dir) / "condition/logs"
        logs.mkdir(parents=True, exist_ok=True)
        atomic_write_text(logs / "gnuradio.log", self.ue_capture("cat /tmp/stage8-gnuradio.log 2>/dev/null || true", check=False) + "\n")
        atomic_write_text(logs / "ue.log", self.ue_capture("cat /tmp/stage8-ue.log 2>/dev/null || true", check=False) + "\n")
        atomic_write_text(logs / "gnb.log", self.gnb_capture("cat /tmp/stage8-gnb.log 2>/dev/null || true", check=False) + "\n")

    def restore(self, output_dir):
        if self.original is None:
            return
        output_dir = pathlib.Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        self.stop_radio()
        self.executor.run(self.kubectl("scale", "deployment/srsran-ue1", "-n", self.namespace, "--replicas=0"), output_dir / "scale-zero.log")
        self.wait_no_ue()
        self.executor.run(self.kubectl("apply", "-k", str(self.repo_root / "configs/ues/srsue"), "-n", self.namespace), output_dir / "apply-baseline.log")
        self.executor.run(self.kubectl("scale", "deployment/srsran-ue1", "-n", self.namespace, f"--replicas={self.original.replicas}"), output_dir / "scale-original.log")
        if self.original.replicas:
            self.executor.run(self.kubectl("rollout", "status", "deployment/srsran-ue1", "-n", self.namespace, "--timeout=300s"), output_dir / "rollout.log", timeout=310)
            self.executor.run(self.kubectl("wait", "--for=condition=Ready", "pod", "-l", UE_SELECTOR, "-n", self.namespace, "--timeout=300s"), output_dir / "ready.log", timeout=310)
        restored = self.current_state()
        write_json(output_dir / "restored-state.json", asdict(restored))
        if restored != self.original:
            raise CommandFailure(f"restored deployment differs from original: {restored} != {self.original}")
        self.ue_pod = None
        self.gnb_pod = None

    def current_state(self):
        def value(expression):
            return self.capture("get", "deployment", "srsran-ue1", "-n", self.namespace, "-o", f"jsonpath={expression}")
        return OriginalUEState(
            replicas=int(value("{.spec.replicas}")),
            configmap=value('{.spec.template.spec.volumes[?(@.name=="ue-volume")].configMap.name}'),
            image=value('{.spec.template.spec.containers[?(@.name=="ue")].image}'),
            pull_policy=value('{.spec.template.spec.containers[?(@.name=="ue")].imagePullPolicy}'),
        )

    def baseline_check(self, output_dir, *, ping_count=100, monitor_trial_dir=None):
        output_dir = pathlib.Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        monitor = None
        try:
            self.executor.run([str(self.repo_root / "bin/baseline.sh"), "start"], output_dir / "start.log", timeout=180)
            self.ue_pod = self.discover_ue()
            self.gnb_pod = self.discover_gnb()
            ue_ip = self.ue_capture("ip netns exec ue1 ip -4 -o addr show dev tun_srsue | awk '{print $4}'")
            if monitor_trial_dir is not None:
                monitor = ResourceMonitor(self, monitor_trial_dir, 1.0)
                monitor.start()
            ping = self.ping(output_dir / "ping.txt", count=ping_count)
            if monitor is not None:
                monitor.check()
            self.executor.run([str(self.repo_root / "bin/baseline.sh"), "logs"], output_dir / "logs.txt", check=False)
            return {
                "status": "passed" if ping["packet_loss_percent"] == 0.0 else "failed",
                "attachment_success": True,
                "ue_ip": ue_ip,
                "ping": ping,
                "throughput": {
                    "status": "deferred",
                    "reason": "No verified user-plane throughput endpoint exists",
                },
            }
        finally:
            if monitor is not None:
                monitor.stop()
            self.executor.run([str(self.repo_root / "bin/baseline.sh"), "stop"], output_dir / "stop.log", check=False)
