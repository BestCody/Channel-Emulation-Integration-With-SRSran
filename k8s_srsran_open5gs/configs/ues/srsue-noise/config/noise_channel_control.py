#!/usr/bin/env python3

import time

from channel_control import ChannelControlServer
from noise_protocol import MAXIMUM_NOISE_AMPLITUDE
from noise_protocol import parse_noise_update


class NoiseChannelControlServer(ChannelControlServer):
    def __init__(
        self,
        bind_endpoint,
        downlink,
        uplink,
        sample_rate,
        downlink_noise,
        uplink_noise,
        power_readers,
        commit_both_function=None,
    ):
        super().__init__(
            bind_endpoint,
            downlink,
            uplink,
            sample_rate,
            commit_both_function=commit_both_function,
        )
        self.downlink_noise = downlink_noise
        self.uplink_noise = uplink_noise
        self.power_readers = dict(power_readers)
        self.last_accepted_noise_sequence = 0
        self.noise_accepted_updates = 0
        self.noise_rejected_updates = 0
        self.noise_amplitudes = {
            "downlink": 0.0,
            "uplink": 0.0,
        }
        self.last_noise_set_us = 0.0

    def config_response(self):
        response = super().config_response()
        response["noise_control"] = {
            "enabled": True,
            "message_type": "noise_update",
            "maximum_amplitude": MAXIMUM_NOISE_AMPLITUDE,
            "separate_sequence": True,
            "continuous_adjustment": False,
        }
        return response

    def _read_power(self, name):
        try:
            return float(self.power_readers[name]())
        except Exception:
            return None

    def status(self):
        response = super().status()
        response["noise"] = {
            "last_accepted_noise_sequence":
                self.last_accepted_noise_sequence,
            "accepted_updates": self.noise_accepted_updates,
            "rejected_updates": self.noise_rejected_updates,
            "maximum_amplitude": MAXIMUM_NOISE_AMPLITUDE,
            "last_set_us": self.last_noise_set_us,
            "downlink": {
                "amplitude": self.noise_amplitudes["downlink"],
                "signal_power": self._read_power("downlink_signal"),
                "noise_power": self._read_power("downlink_noise"),
            },
            "uplink": {
                "amplitude": self.noise_amplitudes["uplink"],
                "signal_power": self._read_power("uplink_signal"),
                "noise_power": self._read_power("uplink_noise"),
            },
        }
        return response

    def _handle_noise_update(self, request):
        update = parse_noise_update(request)
        server_receive_ns = time.time_ns()
        with self._transaction_lock:
            if update.sequence <= self.last_accepted_noise_sequence:
                raise ValueError(
                    f"noise sequence {update.sequence} is not newer than "
                    f"{self.last_accepted_noise_sequence}"
                )

            selected = []
            if update.direction in ("both", "downlink"):
                selected.append(
                    (
                        "downlink",
                        self.downlink_noise,
                        update.amplitudes["downlink"],
                    )
                )
            if update.direction in ("both", "uplink"):
                selected.append(
                    (
                        "uplink",
                        self.uplink_noise,
                        update.amplitudes["uplink"],
                    )
                )

            previous = dict(self.noise_amplitudes)
            started = time.perf_counter_ns()
            changed = []
            try:
                for name, source, amplitude in selected:
                    source.set_amplitude(amplitude)
                    changed.append((name, source))
            except Exception:
                for name, source in changed:
                    source.set_amplitude(previous[name])
                raise

            for name, _source, amplitude in selected:
                self.noise_amplitudes[name] = amplitude
            self.last_noise_set_us = (
                time.perf_counter_ns() - started
            ) / 1000.0
            self.last_accepted_noise_sequence = update.sequence
            self.noise_accepted_updates += 1

        return {
            "version": 1,
            "msg_type": "noise_ack",
            "status": "applied",
            "noise_sequence": update.sequence,
            "amplitudes": {
                name: self.noise_amplitudes[name]
                for name in ("downlink", "uplink")
            },
            "server_receive_ns": server_receive_ns,
            "server_ack_ns": time.time_ns(),
            "set_us": self.last_noise_set_us,
        }

    def _handle_request(self, request):
        if request.get("msg_type") != "noise_update":
            return super()._handle_request(request)
        try:
            return self._handle_noise_update(request)
        except Exception:
            self.noise_rejected_updates += 1
            raise
