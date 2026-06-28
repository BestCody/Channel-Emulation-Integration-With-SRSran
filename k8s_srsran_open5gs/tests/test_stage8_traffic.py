import unittest

from experiment_framework.traffic import parse_ping  # noqa: E402


PING = """PING 10.41.0.1 (10.41.0.1) 56(84) bytes of data.
[1.0] 64 bytes from 10.41.0.1: icmp_seq=1 ttl=64 time=20.0 ms
[1.1] 64 bytes from 10.41.0.1: icmp_seq=2 ttl=64 time=30.0 ms
[1.2] 64 bytes from 10.41.0.1: icmp_seq=4 ttl=64 time=40.0 ms
--- 10.41.0.1 ping statistics ---
4 packets transmitted, 3 received, 25% packet loss, time 3000ms
rtt min/avg/max/mdev = 20.000/30.000/40.000/8.000 ms
"""


class Stage8TrafficTests(unittest.TestCase):
    def test_ping_parser_reports_individual_reply_statistics(self):
        result = parse_ping(PING)
        self.assertEqual(result["packet_loss_percent"], 25.0)
        self.assertEqual(result["missing_sequences"], [3])
        self.assertEqual(result["rtt_ms"]["mean"], 30.0)
        self.assertEqual(result["rtt_ms"]["median"], 30.0)
        self.assertEqual(result["rtt_ms"]["maximum"], 40.0)

    def test_unrecognized_output_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_ping("not ping output")


if __name__ == "__main__":
    unittest.main()
