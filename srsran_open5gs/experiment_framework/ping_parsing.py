#!/usr/bin/env python3

import re
import statistics


SUMMARY = re.compile(
    r"(?P<sent>\d+) packets transmitted, (?P<received>\d+) received, "
    r"(?P<loss>[0-9.]+)% packet loss"
)
RTT = re.compile(
    r"(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = "
    r"(?P<minimum>[0-9.]+)/(?P<average>[0-9.]+)/"
    r"(?P<maximum>[0-9.]+)/(?P<spread>[0-9.]+) ms"
)
REPLY = re.compile(r"icmp_seq=(?P<sequence>\d+).*time=(?P<time>[0-9.]+) ms")


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def parse_ping(text):
    summary = SUMMARY.search(text)
    replies = [(int(match.group("sequence")), float(match.group("time"))) for match in REPLY.finditer(text)]
    if summary is None and not replies:
        raise ValueError("ping output has no recognizable results")
    result = {
        "transmitted": None,
        "received": len(replies),
        "packet_loss_percent": None,
        "reply_count": len(replies),
        "missing_sequences": [],
        "rtt_ms": None,
    }
    if summary is not None:
        result.update(
            transmitted=int(summary.group("sent")),
            received=int(summary.group("received")),
            packet_loss_percent=float(summary.group("loss")),
        )
    if replies:
        sequences = [item[0] for item in replies]
        times = [item[1] for item in replies]
        missing = []
        for previous, current in zip(sequences, sequences[1:]):
            missing.extend(range(previous + 1, current))
        result["missing_sequences"] = missing
        result["rtt_ms"] = {
            "minimum": min(times),
            "mean": statistics.mean(times),
            "median": statistics.median(times),
            "p95": percentile(times, 0.95),
            "maximum": max(times),
        }
    else:
        rtt = RTT.search(text)
        if rtt:
            result["rtt_ms"] = {
                "minimum": float(rtt.group("minimum")),
                "mean": float(rtt.group("average")),
                "median": None,
                "p95": None,
                "maximum": float(rtt.group("maximum")),
            }
    return result
