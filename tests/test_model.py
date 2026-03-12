from __future__ import annotations

import unittest

from niri_edge_switcher.model import WindowState


class ModelTests(unittest.TestCase):
    def test_window_state_allows_null_workspace_id(self) -> None:
        window = WindowState.from_json(
            {
                "id": 1,
                "title": "dragging",
                "app_id": "test",
                "workspace_id": None,
                "is_focused": False,
                "is_floating": False,
                "is_urgent": False,
                "layout": {
                    "pos_in_scrolling_layout": [1, 1],
                    "tile_size": [400.0, 300.0],
                    "window_size": [396, 296],
                    "tile_pos_in_workspace_view": None,
                    "window_offset_in_tile": [2.0, 2.0],
                },
                "focus_timestamp": {"secs": 1, "nanos": 2},
            }
        )

        self.assertIsNone(window.workspace_id)
        self.assertEqual(window.id, 1)
