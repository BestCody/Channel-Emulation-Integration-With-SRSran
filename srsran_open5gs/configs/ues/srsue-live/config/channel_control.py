#!/usr/bin/env python3

import threading
import time

from channel_protocol import (
    PROTOCOL_VERSION,
    decode_message,
    encode_message,
    parse_update,
)
try:
    from sionna_channel import commit_both as compiled_commit_both
except ImportError:
    compiled_commit_both = None


NO_PENDING_SEQUENCE = (1 << 64) - 1


def block_status(block):
    pending = int(block.pending_sequence())
    return {
        "sample_count": int(block.sample_count()),
        "active_sequence": int(block.active_sequence()),
        "pending_sequence": (
            None if pending == NO_PENDING_SEQUENCE else pending
        ),
        "requested_activation_sample": int(
            block.requested_activation_sample()
        ),
        "actual_activation_sample": int(
            block.actual_activation_sample()
        ),
        "activation_time_ns": int(block.activation_time_ns()),
        "latest_received_sequence": int(
            block.latest_received_sequence()
        ),
    }


class ChannelControlServer:
    def __init__(
        self,
        bind_endpoint,
        downlink,
        uplink,
        sample_rate,
        commit_both_function=None,
    ):
        self.bind_endpoint = bind_endpoint
        self.downlink = downlink
        self.uplink = uplink
        self.sample_rate = float(sample_rate)
        self.commit_both_function = (
            commit_both_function or compiled_commit_both
        )
        if self.commit_both_function is None:
            raise RuntimeError("compiled commit_both binding is unavailable")
        self._stop_event = threading.Event()
        self._thread = None
        self._transaction_lock = threading.Lock()
        self.accepted_updates = 0
        self.rejected_updates = 0
        self.last_schedule_us = 0.0
        self.last_accepted_sequence = max(
            int(downlink.latest_received_sequence()),
            int(uplink.latest_received_sequence()),
        )

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="channel-control",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def config_response(self):
        return {
            "version": PROTOCOL_VERSION,
            "msg_type": "config_response",
            "backend": "sparse-cpp-live",
            "sample_rate": self.sample_rate,
            "max_taps": 48,
            "max_delay": 255,
            "directions": ["both", "downlink", "uplink"],
            "per_symbol_channels": False,
        }

    def status(self):
        return {
            "version": PROTOCOL_VERSION,
            "msg_type": "status_response",
            "accepted_updates": self.accepted_updates,
            "rejected_updates": self.rejected_updates,
            "last_schedule_us": self.last_schedule_us,
            "last_accepted_sequence": self.last_accepted_sequence,
            "downlink": block_status(self.downlink),
            "uplink": block_status(self.uplink),
        }

    def _prepare(self, block, update):
        coefficients = tuple(tap.coefficient for tap in update.taps)
        delays = tuple(tap.delay for tap in update.taps)
        return block.prepare_channel(
            update.sequence,
            update.activate_at_sample,
            coefficients,
            delays,
        )

    def _handle_request(self, request):
        message_type = request.get("msg_type")
        if request.get("version") != PROTOCOL_VERSION:
            raise ValueError("unsupported protocol version")
        if message_type == "config_request":
            return self.config_response()
        if message_type == "status_request":
            return self.status()
        if message_type != "channel_update":
            raise ValueError(f"unsupported message type: {message_type}")

        update = parse_update(request)
        server_receive_ns = time.time_ns()
        with self._transaction_lock:
            if update.sequence <= self.last_accepted_sequence:
                raise ValueError(
                    f"sequence {update.sequence} is not newer than "
                    f"{self.last_accepted_sequence}"
                )

            selected = []
            if update.direction in ("both", "downlink"):
                selected.append(self.downlink)
            if update.direction in ("both", "uplink"):
                selected.append(self.uplink)
            current_sample = max(
                int(block.sample_count()) for block in selected
            )
            if update.activate_at_sample < current_sample:
                raise ValueError(
                    f"activation sample {update.activate_at_sample} "
                    f"is older than current sample {current_sample}"
                )

            started = time.perf_counter_ns()
            if update.direction == "both":
                downlink_prepared = self._prepare(
                    self.downlink,
                    update,
                )
                uplink_prepared = self._prepare(
                    self.uplink,
                    update,
                )
                if not self.commit_both_function(
                    self.downlink,
                    downlink_prepared,
                    self.uplink,
                    uplink_prepared,
                ):
                    raise RuntimeError(
                        "two-direction transaction commit failed"
                    )
            else:
                block = (
                    self.downlink
                    if update.direction == "downlink"
                    else self.uplink
                )
                prepared = self._prepare(block, update)
                if not block.commit_channel(prepared):
                    raise RuntimeError("single-direction commit failed")

            self.last_schedule_us = (
                time.perf_counter_ns() - started
            ) / 1000.0
            self.last_accepted_sequence = update.sequence
            self.accepted_updates += 1

        return {
            "version": PROTOCOL_VERSION,
            "msg_type": "channel_ack",
            "status": "scheduled",
            "sequence": update.sequence,
            "requested_activation_sample": update.activate_at_sample,
            "server_receive_ns": server_receive_ns,
            "server_ack_ns": time.time_ns(),
            "schedule_us": self.last_schedule_us,
            "downlink_sample_at_accept": int(
                self.downlink.sample_count()
            ),
            "uplink_sample_at_accept": int(self.uplink.sample_count()),
        }

    def _run(self):
        import zmq

        context = zmq.Context()
        socket = context.socket(zmq.REP)
        socket.linger = 0
        socket.bind(self.bind_endpoint)
        poller = zmq.Poller()
        poller.register(socket, zmq.POLLIN)
        print(
            f"Channel control listening on {self.bind_endpoint}",
            flush=True,
        )

        try:
            while not self._stop_event.is_set():
                events = dict(poller.poll(timeout=100))
                if socket not in events:
                    continue
                try:
                    request = decode_message(socket.recv())
                    response = self._handle_request(request)
                except Exception as error:
                    self.rejected_updates += 1
                    response = {
                        "version": PROTOCOL_VERSION,
                        "msg_type": "error",
                        "error": str(error),
                    }
                socket.send(encode_message(response))
        finally:
            socket.close()
            context.term()
