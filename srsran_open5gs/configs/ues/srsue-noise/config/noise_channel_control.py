#!/usr/bin/env python3

from channel_control import ChannelControlServer


class NoiseChannelControlServer(ChannelControlServer):
    # CIR-stream noise exposes signal power for sweeps
    def __init__(
        self,
        bind_endpoint,
        downlink,
        uplink,
        sample_rate,
        power_readers,
        stream_endpoint="tcp://0.0.0.0:5556",
    ):
        super().__init__(
            bind_endpoint,
            downlink,
            uplink,
            sample_rate,
            stream_endpoint=stream_endpoint,
        )
        self.power_readers = dict(power_readers)

    def _read_power(self, name):
        try:
            return float(self.power_readers[name]())
        except Exception:
            return None

    def config_response(self):
        response = super().config_response()
        response["noise_control"] = {
            "enabled": True,
            "carried_in": "cir_stream",
            "field": "noise_sigma",
        }
        return response

    def status(self):
        response = super().status()
        response["signal"] = {
            "downlink": self._read_power("downlink_signal"),
            "uplink": self._read_power("uplink_signal"),
        }
        return response
