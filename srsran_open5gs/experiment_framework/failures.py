#!/usr/bin/env python3

from dataclasses import asdict, dataclass
from datetime import datetime, timezone


FAILURE_CLASSES = {
    "preflight",
    "pre_baseline",
    "overlay_rollout",
    "wrapper_process",
    "radio_process_exit",
    "random_access",
    "rrc",
    "pdu_session",
    "ping",
    "sionna_calculation",
    "channel_update",
    "late_position",
    "amf_safety",
    "restoration",
    "post_baseline",
    "unexpected",
}


@dataclass(frozen=True)
class FailureRecord:
    category: str
    message: str
    condition_id: str | None = None
    trial_number: int | None = None
    command: list[str] | None = None
    return_code: int | None = None

    def to_dict(self):
        if self.category not in FAILURE_CLASSES:
            raise ValueError(f"unknown failure category: {self.category}")
        value = asdict(self)
        value["time_utc"] = datetime.now(timezone.utc).isoformat()
        return value
