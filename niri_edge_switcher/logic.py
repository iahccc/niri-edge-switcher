from __future__ import annotations

from collections import defaultdict
from typing import Literal

from .model import OutputState, Snapshot, WindowState, WorkspaceState

Side = Literal["left", "right"]


def _layout_axis_key(value: float) -> float:
    return round(value, 6)


def find_edge_window(snapshot: Snapshot, output_name: str, side: Side) -> WindowState | None:
    return find_edge_window_with_spacing(snapshot, output_name, side, inter_column_spacing=0.0)


def find_edge_window_with_spacing(
    snapshot: Snapshot,
    output_name: str,
    side: Side,
    *,
    inter_column_spacing: float,
) -> WindowState | None:
    workspace = _active_workspace_for_output(snapshot, output_name)
    output = snapshot.outputs.get(output_name)
    if workspace is None or output is None:
        return None

    windows = [
        window
        for window in snapshot.windows.values()
        if window.workspace_id == workspace.id
        and not window.is_floating
        and window.layout.pos_in_scrolling_layout is not None
    ]
    if not windows:
        return None

    focused = _focused_window(snapshot, workspace, windows)
    if focused is None:
        return None

    geometry_match = _pick_by_workspace_view(output, windows, focused.id, side)
    if geometry_match is not None:
        return geometry_match

    return _pick_by_scrolling_layout(output, windows, focused, side, inter_column_spacing=inter_column_spacing)


def _active_workspace_for_output(snapshot: Snapshot, output_name: str) -> WorkspaceState | None:
    candidates = [
        workspace
        for workspace in snapshot.workspaces.values()
        if workspace.output == output_name and workspace.is_active
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda workspace: workspace.idx)[0]


def _focused_window(
    snapshot: Snapshot,
    workspace: WorkspaceState,
    windows: list[WindowState],
) -> WindowState | None:
    if workspace.active_window_id is not None:
        active = snapshot.windows.get(workspace.active_window_id)
        if active is not None:
            return active

    focused = [window for window in windows if window.is_focused]
    if focused:
        return max(focused, key=lambda window: window.focus_timestamp_ns)

    return max(windows, key=lambda window: window.focus_timestamp_ns)


def _pick_by_workspace_view(
    output: OutputState,
    windows: list[WindowState],
    focused_window_id: int,
    side: Side,
) -> WindowState | None:
    candidates: list[WindowState] = []
    for window in windows:
        if window.id == focused_window_id:
            continue
        tile_pos = window.layout.tile_pos_in_workspace_view
        if tile_pos is None:
            continue

        x = float(tile_pos[0])
        width = float(window.layout.tile_size[0])
        if side == "left" and x < 0:
            candidates.append(window)
        elif side == "right" and (x + width) > output.logical.width:
            candidates.append(window)

    if not candidates:
        return None

    if side == "left":
        return max(
            candidates,
            key=lambda window: (
                float(window.layout.tile_pos_in_workspace_view[0]) + float(window.layout.tile_size[0]),
                window.focus_timestamp_ns,
            ),
        )

    return min(
        candidates,
        key=lambda window: (
            float(window.layout.tile_pos_in_workspace_view[0]),
            -window.focus_timestamp_ns,
        ),
    )


def _pick_by_scrolling_layout(
    output: OutputState,
    windows: list[WindowState],
    focused: WindowState,
    side: Side,
    *,
    inter_column_spacing: float,
) -> WindowState | None:
    focused_pos = focused.layout.pos_in_scrolling_layout
    if focused_pos is None:
        return None

    focused_column = _layout_axis_key(float(focused_pos[0]))
    columns: dict[float, list[WindowState]] = defaultdict(list)
    for window in windows:
        column = _layout_axis_key(float(window.layout.pos_in_scrolling_layout[0]))
        columns[column].append(window)

    ordered_columns = sorted(columns)
    column_widths = {
        column: max(float(window.layout.tile_size[0]) for window in column_windows)
        for column, column_windows in columns.items()
    }

    column_left_edges: dict[int, float] = {}
    cursor = 0.0
    for index, column in enumerate(ordered_columns):
        if index > 0:
            cursor += inter_column_spacing
        column_left_edges[column] = cursor
        cursor += column_widths[column]

    workspace_width = cursor
    output_width = float(output.logical.width)
    if workspace_width <= output_width:
        return None

    focused_width = column_widths[focused_column]
    focused_left = column_left_edges[focused_column]
    viewport_left = focused_left + (focused_width / 2.0) - (output_width / 2.0)
    viewport_left = max(0.0, min(viewport_left, workspace_width - output_width))
    viewport_right = viewport_left + output_width

    if side == "left":
        target_columns = [
            column
            for column in ordered_columns
            if column != focused_column and column_left_edges[column] < viewport_left
        ]
        if not target_columns:
            return None
        target_column = max(target_columns, key=lambda column: column_left_edges[column] + column_widths[column])
    else:
        target_columns = [
            column
            for column in ordered_columns
            if column != focused_column and (column_left_edges[column] + column_widths[column]) > viewport_right
        ]
        if not target_columns:
            return None
        target_column = min(target_columns, key=lambda column: column_left_edges[column])

    target_windows = [window for window in columns[target_column] if window.id != focused.id]
    if not target_windows:
        return None

    return max(
        target_windows,
        key=lambda window: (
            window.focus_timestamp_ns,
            -float(window.layout.pos_in_scrolling_layout[1]),
        ),
    )
