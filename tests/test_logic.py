from __future__ import annotations

import unittest

from niri_edge_switcher.logic import find_edge_window, find_edge_window_with_spacing
from niri_edge_switcher.model import LayoutState, LogicalOutputState, OutputState, Snapshot, WindowState, WorkspaceState


def make_window(
    window_id: int,
    workspace_id: int,
    column: int,
    row: int,
    *,
    focused: bool = False,
    tile_x: float | None = None,
    tile_width: float = 400.0,
    timestamp: int = 0,
) -> WindowState:
    return WindowState(
        id=window_id,
        title=f"window-{window_id}",
        app_id="test",
        workspace_id=workspace_id,
        is_focused=focused,
        is_floating=False,
        is_urgent=False,
        layout=LayoutState(
            pos_in_scrolling_layout=(column, row),
            tile_size=(tile_width, 300.0),
            window_size=(400, 300),
            tile_pos_in_workspace_view=(tile_x, 10.0) if tile_x is not None else None,
            window_offset_in_tile=(0.0, 0.0),
        ),
        focus_timestamp_ns=timestamp,
    )


def make_snapshot(*windows: WindowState, active_window_id: int) -> Snapshot:
    output = OutputState(
        name="eDP-1",
        make=None,
        model=None,
        serial=None,
        logical=LogicalOutputState(x=0, y=0, width=1000, height=800, scale=1.0, transform="Normal"),
    )
    workspace = WorkspaceState(
        id=1,
        idx=1,
        name=None,
        output="eDP-1",
        is_urgent=False,
        is_active=True,
        is_focused=True,
        active_window_id=active_window_id,
    )
    return Snapshot(
        outputs={"eDP-1": output},
        workspaces={1: workspace},
        windows={window.id: window for window in windows},
    )


class LogicTests(unittest.TestCase):
    def test_prefers_geometry_nearest_screen_edge_when_tile_positions_are_available(self) -> None:
        focused = make_window(1, 1, 2, 1, focused=True, tile_x=100.0, timestamp=10)
        left_far = make_window(2, 1, 1, 1, tile_x=-600.0, tile_width=300.0, timestamp=20)
        left_near = make_window(3, 1, 1, 2, tile_x=-120.0, tile_width=260.0, timestamp=5)
        snapshot = make_snapshot(focused, left_far, left_near, active_window_id=1)

        target = find_edge_window(snapshot, "eDP-1", "left")

        self.assertIsNotNone(target)
        self.assertEqual(target.id, 3)

    def test_uses_column_nearest_screen_edge_when_workspace_view_positions_are_missing(self) -> None:
        far_left = make_window(2, 1, 1, 1, tile_width=400.0, timestamp=10)
        left_old = make_window(3, 1, 2, 1, tile_width=400.0, timestamp=20)
        left_mru = make_window(4, 1, 2, 2, tile_width=400.0, timestamp=80)
        partially_visible = make_window(5, 1, 3, 1, tile_width=400.0, timestamp=90)
        fully_visible = make_window(6, 1, 4, 1, tile_width=400.0, timestamp=70)
        focused = make_window(1, 1, 5, 1, focused=True, tile_width=400.0, timestamp=100)
        snapshot = make_snapshot(far_left, left_old, left_mru, partially_visible, fully_visible, focused, active_window_id=1)

        target = find_edge_window(snapshot, "eDP-1", "left")

        self.assertIsNotNone(target)
        self.assertEqual(target.id, 5)

    def test_prefers_partially_visible_column_over_fully_offscreen_column(self) -> None:
        focused = make_window(1, 1, 1, 1, focused=True, tile_width=500.0, timestamp=100)
        fully_visible_right = make_window(2, 1, 2, 1, tile_width=300.0, timestamp=50)
        edge_right = make_window(3, 1, 3, 1, tile_width=300.0, timestamp=40)
        far_right = make_window(4, 1, 4, 1, tile_width=700.0, timestamp=30)
        snapshot = make_snapshot(focused, fully_visible_right, edge_right, far_right, active_window_id=1)

        target = find_edge_window(snapshot, "eDP-1", "right")

        self.assertIsNotNone(target)
        self.assertEqual(target.id, 3)

    def test_fallback_can_use_configured_inter_column_spacing(self) -> None:
        focused = make_window(1, 1, 1, 1, focused=True, tile_width=652.0, timestamp=100)
        fully_visible_right = make_window(2, 1, 2, 1, tile_width=300.0, timestamp=50)
        just_offscreen_right = make_window(3, 1, 3, 1, tile_width=300.0, timestamp=40)
        far_right = make_window(4, 1, 4, 1, tile_width=700.0, timestamp=30)
        snapshot = make_snapshot(focused, fully_visible_right, just_offscreen_right, far_right, active_window_id=1)

        target = find_edge_window_with_spacing(snapshot, "eDP-1", "right", inter_column_spacing=24.0)

        self.assertIsNotNone(target)
        self.assertEqual(target.id, 3)

    def test_returns_none_when_no_window_exists_on_that_side(self) -> None:
        focused = make_window(1, 1, 1, 1, focused=True, timestamp=100)
        right = make_window(2, 1, 2, 1, timestamp=10)
        snapshot = make_snapshot(focused, right, active_window_id=1)

        target = find_edge_window(snapshot, "eDP-1", "left")

        self.assertIsNone(target)
