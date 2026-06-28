#!/usr/bin/env python3

import signal
import threading
from argparse import ArgumentParser

from gnuradio import blocks
from gnuradio import gr
from gnuradio import zeromq
from sionna_channel import sparse_channel_cc

from fixed_channel import (
    fixed_attenuation_taps,
    load_taps_file,
    validate_attenuation_db,
    validate_sample_rate,
)


class MultiUeSparseChannel(gr.top_block):
    def __init__(self, num_ues, attenuation_db, sample_rate, taps_file=None):
        gr.top_block.__init__(self, "srsRAN compiled sparse channel")

        if num_ues < 1:
            raise ValueError("num_ues must be at least one")

        sample_rate = validate_sample_rate(sample_rate)
        if taps_file:
            taps = load_taps_file(taps_file)
        else:
            attenuation_db = validate_attenuation_db(attenuation_db)
            taps = fixed_attenuation_taps(attenuation_db)

        coefficients = tuple(tap.coefficient for tap in taps)
        delays = tuple(tap.delay for tap in taps)
        zmq_timeout = 100
        zmq_hwm = -1

        self.downlink_channel = sparse_channel_cc(coefficients, delays)
        self.uplink_channel = sparse_channel_cc(coefficients, delays)

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
            (self.throttle, 0),
        )
        self.connect(
            (self.uplink_adder, 0),
            (self.uplink_channel, 0),
            (self.gnb_uplink_sink, 0),
        )

        print(
            "Compiled sparse channel enabled: "
            f"{len(taps)} taps, sample_rate={sample_rate:g} samples/s",
            flush=True,
        )
        for tap in taps:
            print(
                f"tap delay={tap.delay} coefficient={tap.coefficient}",
                flush=True,
            )
        print(
            "No noise or live updates are enabled in Stage 3.",
            flush=True,
        )


def parse_args():
    parser = ArgumentParser(
        description="srsRAN compiled static sparse-channel flowgraph"
    )
    parser.add_argument(
        "-n",
        "--num-ues",
        type=int,
        required=True,
        help="number of UEs",
    )
    parser.add_argument(
        "--attenuation-db",
        type=float,
        default=None,
        help="fixed signal attenuation in dB",
    )
    parser.add_argument(
        "--taps-file",
        help="JSON file containing fixed delayed paths",
    )
    parser.add_argument(
        "--sample-rate",
        type=float,
        required=True,
        help="radio sample rate in samples per second",
    )
    args = parser.parse_args()

    if args.num_ues < 1:
        parser.error("--num-ues must be at least one")
    if args.attenuation_db is not None and args.taps_file:
        parser.error("--attenuation-db and --taps-file are mutually exclusive")
    if args.attenuation_db is None and not args.taps_file:
        args.attenuation_db = 6.0
    try:
        if args.attenuation_db is not None:
            validate_attenuation_db(args.attenuation_db)
        validate_sample_rate(args.sample_rate)
    except ValueError as error:
        parser.error(str(error))
    return args


def main():
    args = parse_args()
    flowgraph = MultiUeSparseChannel(
        num_ues=args.num_ues,
        attenuation_db=args.attenuation_db,
        sample_rate=args.sample_rate,
        taps_file=args.taps_file,
    )

    stop_event = threading.Event()

    def request_stop(sig=None, frame=None):
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    flowgraph.start()
    try:
        stop_event.wait()
    finally:
        flowgraph.stop()
        flowgraph.wait()


if __name__ == "__main__":
    main()
