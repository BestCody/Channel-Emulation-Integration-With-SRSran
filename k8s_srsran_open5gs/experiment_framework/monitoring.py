#!/usr/bin/env python3

from dataclasses import dataclass


MIB = 1024 * 1024


@dataclass(frozen=True)
class AMFSafetyPolicy:
    stop_on_restart: bool = True
    stop_on_identity_change: bool = True
    stop_at_limit_fraction: float = 0.90
    stop_at_growth_bytes: int = 128 * MIB

    @classmethod
    def from_config(cls, config):
        return cls(
            stop_on_restart=bool(config["stop_on_restart"]),
            stop_on_identity_change=bool(config["stop_on_identity_change"]),
            stop_at_limit_fraction=float(config["stop_at_limit_fraction"]),
            stop_at_growth_bytes=int(config["stop_at_growth_bytes"]),
        )


def evaluate_amf_sample(baseline, sample, policy):
    reasons = []
    if policy.stop_on_restart and sample["restart_count"] != baseline["restart_count"]:
        reasons.append("AMF restart count changed")
    if policy.stop_on_identity_change:
        if sample["pod_uid"] != baseline["pod_uid"]:
            reasons.append("AMF pod UID changed")
        if sample.get("container_id") != baseline.get("container_id"):
            reasons.append("AMF container ID changed")
    growth = int(sample["memory_current"]) - int(baseline["memory_current"])
    if growth >= policy.stop_at_growth_bytes:
        reasons.append("AMF memory grew by at least 128 MiB from the pilot baseline")
    maximum = sample.get("memory_max")
    if maximum and int(sample["memory_current"]) / int(maximum) >= policy.stop_at_limit_fraction:
        reasons.append("AMF memory reached 90% of its limit")
    return {
        "safe": not reasons,
        "reasons": reasons,
        "growth_bytes": growth,
        "limit_fraction": None if not maximum else int(sample["memory_current"]) / int(maximum),
    }
