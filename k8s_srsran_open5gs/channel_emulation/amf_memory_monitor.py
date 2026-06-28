#!/usr/bin/env python3

import argparse
import json
import pathlib
import signal
import subprocess
import threading
import time


MIB = 1024 * 1024


def kubectl(*arguments):
    return subprocess.check_output(
        ["kubectl", *arguments],
        text=True,
        timeout=10,
    ).strip()


def discover_amf(namespace):
    return kubectl(
        "get",
        "pods",
        "-n",
        namespace,
        "-l",
        "app=open5gs,nf=amf",
        "--field-selector=status.phase=Running",
        "-o",
        "jsonpath={.items[0].metadata.name}",
    )


def read_sample(namespace):
    pod = discover_amf(namespace)
    metadata = json.loads(
        kubectl("get", "pod", pod, "-n", namespace, "-o", "json")
    )
    status = metadata["status"]["containerStatuses"][0]
    memory = kubectl(
        "exec",
        "-n",
        namespace,
        pod,
        "--",
        "sh",
        "-c",
        "printf '%s ' \"$(cat /sys/fs/cgroup/memory.current)\"; "
        "cat /sys/fs/cgroup/memory.max",
    ).split()
    maximum = None if memory[1] == "max" else int(memory[1])
    return {
        "time_ns": time.time_ns(),
        "pod": pod,
        "pod_uid": metadata["metadata"]["uid"],
        "container_id": status.get("containerID"),
        "restart_count": int(status["restartCount"]),
        "memory_current": int(memory[0]),
        "memory_max": maximum,
    }


def evaluate_sample(sample, baseline):
    reasons = []
    warnings = []
    if sample["restart_count"] != baseline["restart_count"]:
        reasons.append("AMF restart count changed")
    if sample["pod_uid"] != baseline["pod_uid"]:
        reasons.append("AMF pod UID changed")
    if sample["container_id"] != baseline["container_id"]:
        reasons.append("AMF container ID changed")

    growth = sample["memory_current"] - baseline["memory_current"]
    maximum = sample["memory_max"]
    if growth >= 128 * MIB:
        reasons.append("AMF memory grew by at least 128 MiB")
    elif growth >= 64 * MIB:
        warnings.append("AMF memory grew by at least 64 MiB")
    if maximum:
        fraction = sample["memory_current"] / maximum
        if fraction >= 0.90:
            reasons.append("AMF memory reached 90% of its limit")
        elif fraction >= 0.75:
            warnings.append("AMF memory reached 75% of its limit")
    return reasons, warnings


def append_json(path, value):
    with pathlib.Path(path).open("a", encoding="utf-8") as output:
        output.write(json.dumps(value, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", default="open5gs")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary", required=True)
    args = parser.parse_args()

    stop = threading.Event()

    def request_stop(sig=None, frame=None):
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    output = pathlib.Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("", encoding="utf-8")

    baseline = read_sample(args.namespace)
    baseline["event"] = "baseline"
    append_json(output, baseline)
    failures = 0
    final = {
        "status": "running",
        "baseline": baseline,
        "reason": None,
    }
    exit_code = 0
    while not stop.wait(args.interval):
        try:
            sample = read_sample(args.namespace)
            failures = 0
            reasons, warnings = evaluate_sample(sample, baseline)
            sample["warnings"] = warnings
            sample["stop_reasons"] = reasons
            append_json(output, sample)
            if reasons:
                final = {
                    "status": "unsafe",
                    "baseline": baseline,
                    "final": sample,
                    "reason": "; ".join(reasons),
                }
                exit_code = 2
                break
        except Exception as error:
            failures += 1
            append_json(
                output,
                {
                    "time_ns": time.time_ns(),
                    "sample_error": str(error),
                    "consecutive_failures": failures,
                },
            )
            if failures >= 3:
                final = {
                    "status": "unsafe",
                    "baseline": baseline,
                    "reason": "three consecutive AMF samples failed",
                }
                exit_code = 2
                break
    else:
        final = {
            "status": "stopped",
            "baseline": baseline,
            "reason": None,
        }

    pathlib.Path(args.summary).write_text(
        json.dumps(final, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
