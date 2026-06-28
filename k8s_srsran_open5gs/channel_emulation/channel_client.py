#!/usr/bin/env python3

import pathlib
import sys
import time


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
LIVE_CONFIG = REPO_ROOT / "configs" / "ues" / "srsue-live" / "config"
sys.path.insert(0, str(LIVE_CONFIG))

from channel_protocol import decode_message  # noqa: E402
from channel_protocol import encode_message  # noqa: E402


class ChannelClient:
    def __init__(self, endpoint="tcp://127.0.0.1:5555", timeout_ms=5000):
        import zmq

        self.zmq = zmq
        self.endpoint = endpoint
        self.timeout_ms = timeout_ms
        self.context = zmq.Context()
        self.socket = None
        self.connect()

    def connect(self):
        if self.socket is not None:
            self.socket.close()
        self.socket = self.context.socket(self.zmq.REQ)
        self.socket.linger = 0
        self.socket.connect(self.endpoint)

    def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None
        self.context.term()

    def request(self, message, raise_on_error=True):
        started = time.perf_counter_ns()
        self.socket.send(encode_message(message))
        if self.socket.poll(self.timeout_ms) == 0:
            self.connect()
            raise TimeoutError(
                f"no response after {self.timeout_ms} ms"
            )
        response = decode_message(self.socket.recv())
        response["request_rtt_ms"] = (
            time.perf_counter_ns() - started
        ) / 1_000_000.0
        if raise_on_error and response.get("msg_type") == "error":
            raise RuntimeError(response.get("error", "update failed"))
        return response

    def get_config(self):
        return self.request(
            {"version": 1, "msg_type": "config_request"}
        )

    def get_status(self):
        return self.request(
            {"version": 1, "msg_type": "status_request"}
        )
