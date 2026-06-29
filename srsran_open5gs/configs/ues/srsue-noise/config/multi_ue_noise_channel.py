#!/usr/bin/env python3

import signal
import threading
from argparse import ArgumentParser

from gnuradio import blocks
from gnuradio import gr
from gnuradio import zeromq
from sionna_channel import sparse_channel_cc

from fixed_channel import samples_per_symbol
from fixed_channel import validate_sample_rate
from noise_channel_control import NoiseChannelControlServer
from radio_endpoints import gnb_downlink_endpoint
from radio_endpoints import gnb_uplink_endpoint
from radio_endpoints import ue_downlink_endpoint
from radio_endpoints import ue_uplink_endpoint


class MultiUeNoiseChannel(gr.top_block):
    def __init__(self, num_ues, sample_rate, control_bind, scs_khz=15.0):
        gr.top_block.__init__(self, "srsRAN live channel with in-CIR noise")
        if num_ues < 1:
            raise ValueError("num_ues must be at least one")
        sample_rate = validate_sample_rate(sample_rate)
        sps = samples_per_symbol(sample_rate, scs_khz)

        identity_coefficients = (1.0 + 0.0j,)
        identity_delays = (0,)
        self.downlink_channel = sparse_channel_cc(
            identity_coefficients,
            identity_delays,
            sps,
        )
        self.uplink_channel = sparse_channel_cc(
            identity_coefficients,
            identity_delays,
            sps,
        )

        power_window = max(1, int(round(sample_rate * 0.010)))
        self.downlink_signal_probe = self._power_probe(power_window)
        self.uplink_signal_probe = self._power_probe(power_window)

        zmq_timeout = 100
        zmq_hwm = -1
        self.gnb_downlink_source = zeromq.req_source(
            gr.sizeof_gr_complex,
            1,
            gnb_downlink_endpoint(),
            zmq_timeout,
            False,
            zmq_hwm,
        )
        self.gnb_uplink_sink = zeromq.rep_sink(
            gr.sizeof_gr_complex,
            1,
            gnb_uplink_endpoint(),
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
            ue_number = index + 1
            uplink_source = zeromq.req_source(
                gr.sizeof_gr_complex,
                1,
                ue_uplink_endpoint(ue_number),
                zmq_timeout,
                False,
                zmq_hwm,
            )
            downlink_sink = zeromq.rep_sink(
                gr.sizeof_gr_complex,
                1,
                ue_downlink_endpoint(ue_number),
                zmq_timeout,
                False,
                zmq_hwm,
            )
            self.ue_uplink_sources.append(uplink_source)
            self.ue_downlink_sinks.append(downlink_sink)
            self.connect((uplink_source, 0), (self.uplink_adder, index))
            self.connect((self.throttle, 0), (downlink_sink, 0))

        # Noise is applied inside the channel block from the streamed
        # CIR sigma; there is no separate noise stage.
        self.connect(
            (self.gnb_downlink_source, 0),
            (self.downlink_channel, 0),
            (self.throttle, 0),
        )
        self.connect(
            (self.uplink_adder, 0),
            (self.uplink_channel, 0),
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

        self.control_server = NoiseChannelControlServer(
            bind_endpoint=control_bind,
            downlink=self.downlink_channel,
            uplink=self.uplink_channel,
            sample_rate=sample_rate,
            power_readers={
                "downlink_signal":
                    self.downlink_signal_probe["probe"].level,
                "uplink_signal":
                    self.uplink_signal_probe["probe"].level,
            },
        )
        print(
            "Live channel with in-CIR noise; sigma streamed per symbol",
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
        description="srsRAN live channel with in-CIR noise"
    )
    parser.add_argument("-n", "--num-ues", type=int, required=True)
    parser.add_argument("--sample-rate", type=float, required=True)
    parser.add_argument("--scs-khz", type=float, default=15.0)
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
        args.scs_khz,
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
