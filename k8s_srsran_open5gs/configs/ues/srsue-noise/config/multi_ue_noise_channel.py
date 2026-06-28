#!/usr/bin/env python3

import signal
import threading
from argparse import ArgumentParser

from gnuradio import analog
from gnuradio import blocks
from gnuradio import gr
from gnuradio import zeromq
from sionna_channel import sparse_channel_cc

from fixed_channel import validate_sample_rate
from noise_channel_control import NoiseChannelControlServer


class MultiUeNoiseChannel(gr.top_block):
    def __init__(self, num_ues, sample_rate, control_bind):
        gr.top_block.__init__(self, "srsRAN live sparse channel with noise")
        if num_ues < 1:
            raise ValueError("num_ues must be at least one")
        sample_rate = validate_sample_rate(sample_rate)

        identity_coefficients = (1.0 + 0.0j,)
        identity_delays = (0,)
        self.downlink_channel = sparse_channel_cc(
            identity_coefficients,
            identity_delays,
        )
        self.uplink_channel = sparse_channel_cc(
            identity_coefficients,
            identity_delays,
        )
        self.downlink_noise = analog.noise_source_c(
            analog.GR_GAUSSIAN,
            0.0,
            -20260631,
        )
        self.uplink_noise = analog.noise_source_c(
            analog.GR_GAUSSIAN,
            0.0,
            -20260632,
        )
        self.downlink_noise_adder = blocks.add_vcc(1)
        self.uplink_noise_adder = blocks.add_vcc(1)

        power_window = max(1, int(round(sample_rate * 0.010)))
        self.downlink_signal_probe = self._power_probe(power_window)
        self.uplink_signal_probe = self._power_probe(power_window)
        self.downlink_noise_probe = self._power_probe(power_window)
        self.uplink_noise_probe = self._power_probe(power_window)

        zmq_timeout = 100
        zmq_hwm = -1
        self.gnb_downlink_source = zeromq.req_source(
            gr.sizeof_gr_complex,
            1,
            "tcp://10.10.3.231:2000",
            zmq_timeout,
            False,
            zmq_hwm,
        )
        self.gnb_uplink_sink = zeromq.rep_sink(
            gr.sizeof_gr_complex,
            1,
            "tcp://10.10.3.232:2001",
            zmq_timeout,
            False,
            zmq_hwm,
        )
        self.throttle = blocks.throttle(
            gr.sizeof_gr_complex,
            sample_rate,
            True,
        )
        self.uplink_adder = blocks.add_vcc(1)

        self.ue_uplink_sources = []
        self.ue_downlink_sinks = []
        for index in range(num_ues):
            uplink_source = zeromq.req_source(
                gr.sizeof_gr_complex,
                1,
                f"tcp://10.10.3.232:{2101 + index}",
                zmq_timeout,
                False,
                zmq_hwm,
            )
            downlink_sink = zeromq.rep_sink(
                gr.sizeof_gr_complex,
                1,
                f"tcp://10.10.3.232:{2201 + index}",
                zmq_timeout,
                False,
                zmq_hwm,
            )
            self.ue_uplink_sources.append(uplink_source)
            self.ue_downlink_sinks.append(downlink_sink)
            self.connect((uplink_source, 0), (self.uplink_adder, index))
            self.connect((self.throttle, 0), (downlink_sink, 0))

        self.connect(
            (self.gnb_downlink_source, 0),
            (self.downlink_channel, 0),
        )
        self.connect(
            (self.downlink_channel, 0),
            (self.downlink_noise_adder, 0),
        )
        self.connect(
            (self.downlink_noise, 0),
            (self.downlink_noise_adder, 1),
        )
        self.connect(
            (self.downlink_noise_adder, 0),
            (self.throttle, 0),
        )
        self.connect(
            (self.uplink_adder, 0),
            (self.uplink_channel, 0),
        )
        self.connect(
            (self.uplink_channel, 0),
            (self.uplink_noise_adder, 0),
        )
        self.connect(
            (self.uplink_noise, 0),
            (self.uplink_noise_adder, 1),
        )
        self.connect(
            (self.uplink_noise_adder, 0),
            (self.gnb_uplink_sink, 0),
        )

        self._connect_power(
            self.downlink_channel,
            self.downlink_signal_probe,
        )
        self._connect_power(
            self.uplink_channel,
            self.uplink_signal_probe,
        )
        self._connect_power(
            self.downlink_noise,
            self.downlink_noise_probe,
        )
        self._connect_power(
            self.uplink_noise,
            self.uplink_noise_probe,
        )

        self.control_server = NoiseChannelControlServer(
            bind_endpoint=control_bind,
            downlink=self.downlink_channel,
            uplink=self.uplink_channel,
            sample_rate=sample_rate,
            downlink_noise=self.downlink_noise,
            uplink_noise=self.uplink_noise,
            power_readers={
                "downlink_signal":
                    self.downlink_signal_probe["probe"].level,
                "uplink_signal":
                    self.uplink_signal_probe["probe"].level,
                "downlink_noise":
                    self.downlink_noise_probe["probe"].level,
                "uplink_noise":
                    self.uplink_noise_probe["probe"].level,
            },
        )
        print(
            "Stage 6 live sparse channel with zero initial noise; "
            "one control server on the configured endpoint",
            flush=True,
        )

    def _power_probe(self, window):
        return {
            "magnitude": blocks.complex_to_mag_squared(1),
            "average": blocks.moving_average_ff(
                window,
                1.0 / window,
                4096,
                1,
            ),
            "probe": blocks.probe_signal_f(),
        }

    def _connect_power(self, source, measurement):
        self.connect(
            source,
            measurement["magnitude"],
            measurement["average"],
            measurement["probe"],
        )

    def start_live(self):
        self.start()
        self.control_server.start()

    def stop_live(self):
        self.control_server.stop()
        self.stop()
        self.wait()


def parse_args():
    parser = ArgumentParser(
        description="srsRAN live sparse channel with controlled noise"
    )
    parser.add_argument("-n", "--num-ues", type=int, required=True)
    parser.add_argument("--sample-rate", type=float, required=True)
    parser.add_argument(
        "--control-bind",
        default="tcp://0.0.0.0:5555",
    )
    args = parser.parse_args()
    if args.num_ues < 1:
        parser.error("--num-ues must be at least one")
    try:
        validate_sample_rate(args.sample_rate)
    except ValueError as error:
        parser.error(str(error))
    return args


def main():
    args = parse_args()
    flowgraph = MultiUeNoiseChannel(
        args.num_ues,
        args.sample_rate,
        args.control_bind,
    )
    stop_event = threading.Event()

    def request_stop(sig=None, frame=None):
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    flowgraph.start_live()
    try:
        stop_event.wait()
    finally:
        flowgraph.stop_live()


if __name__ == "__main__":
    main()
