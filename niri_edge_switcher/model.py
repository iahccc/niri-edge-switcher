from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LayoutState:
    pos_in_scrolling_layout: tuple[float, float] | None
    tile_size: tuple[float, float]
    window_size: tuple[int, int]
    tile_pos_in_workspace_view: tuple[float, float] | None
    window_offset_in_tile: tuple[float, float]

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "LayoutState":
        return cls(
            pos_in_scrolling_layout=_tuple_or_none(payload.get("pos_in_scrolling_layout")),
            tile_size=_tuple_or_default(payload.get("tile_size"), (0.0, 0.0)),
            window_size=_tuple_or_default(payload.get("window_size"), (0, 0)),
            tile_pos_in_workspace_view=_tuple_or_none(payload.get("tile_pos_in_workspace_view")),
            window_offset_in_tile=_tuple_or_default(payload.get("window_offset_in_tile"), (0.0, 0.0)),
        )


@dataclass(frozen=True)
class WindowState:
    id: int
    title: str
    app_id: str | None
    workspace_id: int
    is_focused: bool
    is_floating: bool
    is_urgent: bool
    layout: LayoutState
    focus_timestamp_ns: int

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "WindowState":
        timestamp = payload.get("focus_timestamp") or {}
        return cls(
            id=int(payload["id"]),
            title=payload.get("title") or "",
            app_id=payload.get("app_id"),
            workspace_id=int(payload["workspace_id"]),
            is_focused=bool(payload.get("is_focused")),
            is_floating=bool(payload.get("is_floating")),
            is_urgent=bool(payload.get("is_urgent")),
            layout=LayoutState.from_json(payload.get("layout") or {}),
            focus_timestamp_ns=(int(timestamp.get("secs", 0)) * 1_000_000_000) + int(timestamp.get("nanos", 0)),
        )


@dataclass(frozen=True)
class WorkspaceState:
    id: int
    idx: int
    name: str | None
    output: str
    is_urgent: bool
    is_active: bool
    is_focused: bool
    active_window_id: int | None

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "WorkspaceState":
        return cls(
            id=int(payload["id"]),
            idx=int(payload["idx"]),
            name=payload.get("name"),
            output=payload["output"],
            is_urgent=bool(payload.get("is_urgent")),
            is_active=bool(payload.get("is_active")),
            is_focused=bool(payload.get("is_focused")),
            active_window_id=payload.get("active_window_id"),
        )


@dataclass(frozen=True)
class LogicalOutputState:
    x: int
    y: int
    width: int
    height: int
    scale: float
    transform: str

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "LogicalOutputState":
        return cls(
            x=int(payload["x"]),
            y=int(payload["y"]),
            width=int(payload["width"]),
            height=int(payload["height"]),
            scale=float(payload["scale"]),
            transform=payload["transform"],
        )


@dataclass(frozen=True)
class OutputState:
    name: str
    make: str | None
    model: str | None
    serial: str | None
    logical: LogicalOutputState

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "OutputState":
        return cls(
            name=payload["name"],
            make=payload.get("make"),
            model=payload.get("model"),
            serial=payload.get("serial"),
            logical=LogicalOutputState.from_json(payload["logical"]),
        )


@dataclass(frozen=True)
class Snapshot:
    outputs: dict[str, OutputState]
    workspaces: dict[int, WorkspaceState]
    windows: dict[int, WindowState]

    @classmethod
    def from_json(
        cls,
        outputs_payload: dict[str, Any],
        workspaces_payload: list[dict[str, Any]],
        windows_payload: list[dict[str, Any]],
    ) -> "Snapshot":
        outputs = {name: OutputState.from_json(value) for name, value in outputs_payload.items()}
        workspaces = {workspace["id"]: WorkspaceState.from_json(workspace) for workspace in workspaces_payload}
        windows = {window["id"]: WindowState.from_json(window) for window in windows_payload}
        return cls(outputs=outputs, workspaces=workspaces, windows=windows)


def _tuple_or_none(value: Any) -> tuple[Any, ...] | None:
    if value is None:
        return None
    return tuple(value)


def _tuple_or_default(value: Any, default: tuple[Any, ...]) -> tuple[Any, ...]:
    if value is None:
        return default
    return tuple(value)
