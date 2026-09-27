from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

LINUX_DIR = Path(__file__).resolve().parents[1]
if str(LINUX_DIR) not in sys.path:
    sys.path.insert(0, str(LINUX_DIR))

from deskworlds_core import (  # noqa: E402
    ConfigStore,
    Rect,
    calculate_exposure,
    choose_frame_rate,
    logical_monitor_rect,
    match_hyprland_monitor,
    parse_cursor_response,
    visible_client_rectangles,
)


class GeometryTests(unittest.TestCase):
    def test_scaled_monitor_uses_logical_size(self) -> None:
        monitor = {
            "x": 1920,
            "y": 0,
            "width": 3840,
            "height": 2160,
            "scale": 2,
            "transform": 0,
        }
        self.assertEqual(logical_monitor_rect(monitor), Rect(1920, 0, 1920, 1080))

    def test_rotated_monitor_swaps_logical_dimensions(self) -> None:
        monitor = {
            "x": 0,
            "y": 0,
            "width": 1920,
            "height": 1080,
            "scale": 1,
            "transform": 1,
        }
        self.assertEqual(logical_monitor_rect(monitor), Rect(0, 0, 1080, 1920))

    def test_matches_nearest_output(self) -> None:
        monitors = [
            {"name": "DP-1", "x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1},
            {"name": "DP-2", "x": 1920, "y": 0, "width": 2560, "height": 1440, "scale": 1},
        ]
        match = match_hyprland_monitor(Rect(1920, 0, 2560, 1440), monitors)
        self.assertEqual(match["name"], "DP-2")

    def test_full_blocker_has_zero_exposure(self) -> None:
        frame = Rect(0, 0, 1920, 1080)
        self.assertEqual(calculate_exposure(frame, [frame]), 0.0)

    def test_half_blocker_is_approximately_half_exposed(self) -> None:
        frame = Rect(0, 0, 1600, 1000)
        blocker = Rect(0, 0, 800, 1000)
        self.assertAlmostEqual(calculate_exposure(frame, [blocker]), 0.5, places=2)

    def test_only_active_workspace_clients_block(self) -> None:
        monitor = {
            "id": 1,
            "activeWorkspace": {"id": 4},
            "specialWorkspace": {"id": 0},
        }
        clients = [
            {"monitor": 1, "workspace": {"id": 4}, "at": [0, 0], "size": [800, 600], "mapped": True},
            {"monitor": 1, "workspace": {"id": 5}, "at": [0, 0], "size": [1920, 1080], "mapped": True},
            {"monitor": 0, "workspace": {"id": 4}, "at": [0, 0], "size": [1920, 1080], "mapped": True},
        ]
        self.assertEqual(visible_client_rectangles(monitor, clients), [Rect(0, 0, 800, 600)])


class PolicyTests(unittest.TestCase):
    def test_paused_and_dpms_stop_rendering(self) -> None:
        self.assertEqual(
            choose_frame_rate(1.0, paused=True, dpms_on=True, on_battery=False),
            0,
        )
        self.assertEqual(
            choose_frame_rate(1.0, paused=False, dpms_on=False, on_battery=False),
            0,
        )

    def test_covered_output_slows_down(self) -> None:
        self.assertEqual(
            choose_frame_rate(0.3, paused=False, dpms_on=True, on_battery=False),
            20,
        )
        self.assertEqual(
            choose_frame_rate(0.8, paused=False, dpms_on=True, on_battery=False),
            30,
        )

    def test_battery_ceiling_is_respected(self) -> None:
        self.assertEqual(
            choose_frame_rate(
                1.0,
                paused=False,
                dpms_on=True,
                on_battery=True,
                max_fps=60,
                battery_fps=24,
            ),
            24,
        )


class CursorTests(unittest.TestCase):
    def test_json_object(self) -> None:
        self.assertEqual(parse_cursor_response({"x": 12.5, "y": 44}), (12.5, 44.0))

    def test_plain_legacy_text(self) -> None:
        self.assertEqual(parse_cursor_response("123, 456"), (123.0, 456.0))

    def test_json_string(self) -> None:
        self.assertEqual(parse_cursor_response(json.dumps("10, 20")), (10.0, 20.0))


class ConfigTests(unittest.TestCase):
    def test_invalid_values_are_normalised_and_saved_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps({"world": "unknown", "max_fps": 999, "paused": 1}),
                encoding="utf-8",
            )
            store = ConfigStore(path)
            config = store.load()
            self.assertEqual(config["world"], "riverscape")
            self.assertEqual(config["max_fps"], 120)
            self.assertTrue(config["paused"])
            store.save(config)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["world"], "riverscape")


if __name__ == "__main__":
    unittest.main()
