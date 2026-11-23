"""Probe and publication boundary tests; no research libraries or model required."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from nicegui_dashboard.team_capture import FlightMeasurements, capture, main, write_result


class CaptureBoundaryTests(unittest.TestCase):
    def test_forecast_probe_observes_unblended_probabilities_and_preserves_model_output(self):
        try:
            import torch
        except ImportError:
            self.skipTest("The team model's PyTorch environment is required")
        logits = torch.log(torch.tensor([[.1, .2, .3, .4]]))
        head = torch.nn.Identity()
        tap = FlightMeasurements()
        tap.frame = 25
        with head.register_forward_hook(tap.observe_fleet_head):
            result = head(logits)
        self.assertIs(result, logits)
        self.assertEqual(tap.forecast_frames, [5])
        self.assertAlmostEqual(tap.onset_probabilities[0], .7, places=6)

    def test_different_commit_cannot_claim_an_existing_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            write_result(base / "snapshot.json", {"sha": "a" * 40, "release_root": str(base)})
            with self.assertRaises(ValueError):
                capture(base / "code", base, {}, "b" * 40)

    def test_packet_probe_preserves_return_and_uses_completed_intervals(self):
        class Packets:
            def __init__(self):
                self.ic = [0] * 12

            def interval(self, generated):
                self.ic[0] += generated
                self.ic[1] += 1
                self.ic[8] += 1
                return self.ic[0]

        tap = FlightMeasurements()
        packets = tap.watch_packets(Packets())
        self.assertEqual([packets.interval(2) for _ in range(11)], list(range(2, 23, 2)))
        self.assertEqual(tap.counters, [(10, 5, 5), (20, 10, 10)])

    def test_short_smoke_cannot_replace_a_published_region(self):
        argv = ["capture", "--code", ".", "--core", ".", "--output-root", ".", "--result", "result.json",
                "--region", "bundang", "--commit", "a" * 40, "--max-seconds", "24"]
        with patch.object(sys, "argv", argv), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                main()
        self.assertEqual(raised.exception.code, 2)

    def test_invalid_result_does_not_overwrite_verified_output(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "result.json"
            write_result(path, {"revision": "complete"})
            with self.assertRaises(ValueError):
                write_result(path, {"value": float("nan")})
            self.assertEqual(json.loads(path.read_bytes()), {"revision": "complete"})
            self.assertEqual(list(path.parent.iterdir()), [path])
