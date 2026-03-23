from __future__ import annotations

import argparse
import ctypes
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .logic import Side, find_edge_window_with_spacing
from .model import OutputState, Snapshot, WindowState
from .niri import NiriClient, NiriEventWatcher


def _import_gtk() -> tuple[Any, Any, Any, Any, Any]:
    try:
        ctypes.CDLL("libgtk4-layer-shell.so")
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        gi.require_version("Pango", "1.0")
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gdk, Gio, GLib, Gtk, Gtk4LayerShell, Pango
    except Exception as error:  # pragma: no cover - runtime env only
        raise RuntimeError(
            "GTK4 / PyGObject / gtk4-layer-shell is unavailable. Run this project inside `nix develop`."
        ) from error

    return Gtk, Gdk, Gio, GLib, Gtk4LayerShell, Pango


Gtk, Gdk, Gio, GLib, Gtk4LayerShell, Pango = _import_gtk()


@dataclass(frozen=True)
class AppConfig:
    edge_width: int = 1
    preview_delay_ms: int = 120
    hide_delay_ms: int = 140
    post_click_delay_ms: int = 280
    icon_size: int = 64
    title_max_width: int = 220
    preview_margin: int = 10
    card_padding: int = 18
    inter_column_spacing: float | None = None
    log_level: str = "INFO"


def _load_inter_column_spacing() -> float:
    gaps = _load_niri_layout_gap()
    return max(0.0, gaps * 2.0)


def _load_niri_layout_gap() -> float:
    config_paths = []
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        config_paths.append(Path(xdg_config_home) / "niri" / "config.kdl")
    config_paths.append(Path.home() / ".config" / "niri" / "config.kdl")
    config_paths.append(Path("/etc/niri/config.kdl"))

    for path in config_paths:
        gap = _parse_layout_gap_from_kdl(path)
        if gap is not None:
            return gap
    return 0.0


def _parse_layout_gap_from_kdl(path: Path) -> float | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    depth = 0
    layout_depth: int | None = None
    gap_pattern = re.compile(r"^gaps\s+(-?\d+(?:\.\d+)?)\b")

    for raw_line in lines:
        line = raw_line.split("//", 1)[0].strip()
        if not line:
            continue

        if layout_depth is None and line.startswith("layout") and "{" in line:
            layout_depth = depth + line.count("{") - line.count("}")
            depth += line.count("{") - line.count("}")
            continue

        if layout_depth is not None and depth == layout_depth:
            match = gap_pattern.match(line)
            if match is not None:
                return float(match.group(1))

        depth += line.count("{") - line.count("}")
        if layout_depth is not None and depth < layout_depth:
            layout_depth = None


class EdgePreviewWindow:
    def __init__(
        self,
        app: Gtk.Application,
        monitor: Gdk.Monitor,
        side: Side,
        config: AppConfig,
        on_enter: Callable[[], None],
        on_leave: Callable[[], None],
        on_click: Callable[[], None],
    ) -> None:
        self.config = config
        self.side = side
        self.window = Gtk.Window()
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.set_hide_on_close(True)
        self.window.set_focusable(False)
        self.window.add_css_class("edge-preview-window")
        self.side_padding = max(6, config.card_padding // 2)
        self.title_max_chars = max(1, config.title_max_width // 10)

        Gtk4LayerShell.init_for_window(self.window)
        Gtk4LayerShell.set_namespace(self.window, f"niri-edge-preview-{side}")
        Gtk4LayerShell.set_layer(self.window, Gtk4LayerShell.Layer.OVERLAY)
        Gtk4LayerShell.set_monitor(self.window, monitor)
        Gtk4LayerShell.set_keyboard_mode(self.window, Gtk4LayerShell.KeyboardMode.NONE)
        Gtk4LayerShell.set_exclusive_zone(self.window, -1)
        Gtk4LayerShell.set_anchor(self.window, Gtk4LayerShell.Edge.TOP, True)
        Gtk4LayerShell.set_anchor(self.window, Gtk4LayerShell.Edge.LEFT, side == "left")
        Gtk4LayerShell.set_anchor(self.window, Gtk4LayerShell.Edge.RIGHT, side == "right")
        Gtk4LayerShell.set_margin(self.window, Gtk4LayerShell.Edge.LEFT, config.preview_margin if side == "left" else 0)
        Gtk4LayerShell.set_margin(self.window, Gtk4LayerShell.Edge.RIGHT, config.preview_margin if side == "right" else 0)

        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.card.add_css_class("edge-preview-card")
        self.card.set_margin_top(config.card_padding)
        self.card.set_margin_bottom(config.card_padding)
        self.card.set_margin_start(self.side_padding)
        self.card.set_margin_end(self.side_padding)
        self.card.set_halign(Gtk.Align.CENTER)
        self.card.set_valign(Gtk.Align.CENTER)

        self.icon = Gtk.Image.new_from_icon_name("application-x-executable")
        self.icon.add_css_class("edge-preview-icon")
        self.icon.set_pixel_size(config.icon_size)
        self.icon.set_halign(Gtk.Align.CENTER)
        self.card.append(self.icon)

        self.title = Gtk.Label(label="")
        self.title.add_css_class("edge-preview-title")
        self.title.set_halign(Gtk.Align.CENTER)
        self.title.set_wrap(False)
        self.title.set_single_line_mode(True)
        self.title.set_max_width_chars(self.title_max_chars)
        self.title.set_ellipsize(Pango.EllipsizeMode.END)
        self.title.set_xalign(0.5)
        self.title.set_margin_top(8)
        self.card.append(self.title)
        self.window.set_child(self.card)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_args: on_enter())
        motion.connect("leave", lambda *_args: on_leave())
        self.card.add_controller(motion)

        gesture = Gtk.GestureClick()
        gesture.connect("pressed", lambda *_args: on_click())
        self.card.add_controller(gesture)

        self.window.set_visible(False)

    def show_icon(self, icon: Gio.Icon | None, title: str, output: OutputState, pointer_y: float) -> None:
        if icon is not None:
            self.icon.set_from_gicon(icon)
        else:
            self.icon.set_from_icon_name("application-x-executable")
        self.icon.set_pixel_size(self.config.icon_size)
        self.title.set_text(title or "")
        self._set_content_alignment(edge_aligned=self._title_overflows(title or ""))
        fallback_height = self.config.icon_size + (self.config.card_padding * 2) + 28
        self._reposition(output, pointer_y, fallback_height=fallback_height)
        self.window.set_visible(True)

    def hide(self) -> None:
        self.window.set_visible(False)

    def is_visible(self) -> bool:
        return self.window.get_visible()

    def reposition(self, output: OutputState, pointer_y: float) -> None:
        fallback_height = self.config.icon_size + (self.config.card_padding * 2) + 28
        self._reposition(output, pointer_y, fallback_height=fallback_height)

    def _reposition(self, output: OutputState, pointer_y: float, fallback_height: int) -> None:
        margin_top = int(pointer_y - (fallback_height / 2))
        max_margin = max(self.config.preview_margin, output.logical.height - fallback_height - self.config.preview_margin)
        margin_top = max(self.config.preview_margin, min(max_margin, margin_top))
        Gtk4LayerShell.set_margin(self.window, Gtk4LayerShell.Edge.TOP, margin_top)

    def _set_content_alignment(self, *, edge_aligned: bool) -> None:
        if not edge_aligned:
            self.card.set_halign(Gtk.Align.CENTER)
            self.icon.set_halign(Gtk.Align.CENTER)
            self.title.set_halign(Gtk.Align.CENTER)
            self.title.set_xalign(0.5)
            return

        side_align = Gtk.Align.START if self.side == "left" else Gtk.Align.END
        self.card.set_halign(side_align)
        self.icon.set_halign(side_align)
        self.title.set_halign(side_align)
        self.title.set_xalign(0.0 if self.side == "left" else 1.0)

    def _title_overflows(self, title: str) -> bool:
        if not title:
            return False

        layout = self.title.create_pango_layout(title)
        width, _height = layout.get_pixel_size()
        return width > (self.config.icon_size * 2)


class EdgeStripWindow:
    def __init__(
        self,
        app: Gtk.Application,
        monitor: Gdk.Monitor,
        output: OutputState,
        side: Side,
        config: AppConfig,
        on_enter: Callable[[float], None],
        on_leave: Callable[[], None],
        on_motion: Callable[[float], None],
        on_click: Callable[[], None],
    ) -> None:
        self.config = config
        self.output = output
        self.pointer_y_offset = 0.0
        self.window = Gtk.Window()
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.set_default_size(config.edge_width, self._strip_height(output))
        self.window.set_hide_on_close(True)
        self.window.set_focusable(False)
        self.window.add_css_class("edge-strip-window")

        Gtk4LayerShell.init_for_window(self.window)
        Gtk4LayerShell.set_namespace(self.window, f"niri-edge-strip-{side}")
        Gtk4LayerShell.set_layer(self.window, Gtk4LayerShell.Layer.OVERLAY)
        Gtk4LayerShell.set_monitor(self.window, monitor)
        Gtk4LayerShell.set_keyboard_mode(self.window, Gtk4LayerShell.KeyboardMode.NONE)
        Gtk4LayerShell.set_exclusive_zone(self.window, -1)
        Gtk4LayerShell.set_anchor(self.window, Gtk4LayerShell.Edge.TOP, True)
        Gtk4LayerShell.set_anchor(self.window, Gtk4LayerShell.Edge.BOTTOM, False)
        Gtk4LayerShell.set_anchor(self.window, Gtk4LayerShell.Edge.LEFT, side == "left")
        Gtk4LayerShell.set_anchor(self.window, Gtk4LayerShell.Edge.RIGHT, side == "right")

        self.box = Gtk.Box()
        self.box.add_css_class("edge-hot-strip")
        self.box.set_halign(Gtk.Align.FILL)
        self.box.set_valign(Gtk.Align.FILL)
        self.box.set_hexpand(True)
        self.box.set_vexpand(True)
        self.window.set_child(self.box)
        self.update_output(output)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda _controller, _x, y: on_enter(float(y) + self.pointer_y_offset))
        motion.connect("leave", lambda *_args: on_leave())
        motion.connect("motion", lambda _controller, _x, y: on_motion(float(y) + self.pointer_y_offset))
        self.box.add_controller(motion)

        gesture = Gtk.GestureClick()
        gesture.connect("pressed", lambda *_args: on_click())
        self.box.add_controller(gesture)

        self.window.set_visible(False)

    def update_output(self, output: OutputState) -> None:
        self.output = output
        height = self._strip_height(output)
        self.pointer_y_offset = self._strip_top_margin(output)
        self.window.set_default_size(self.config.edge_width, height)
        self.window.set_size_request(self.config.edge_width, height)
        self.box.set_size_request(self.config.edge_width, height)
        Gtk4LayerShell.set_margin(self.window, Gtk4LayerShell.Edge.TOP, int(self.pointer_y_offset))
        self.window.queue_resize()

    def _strip_height(self, output: OutputState) -> int:
        return max(1, int(output.logical.height / 2))

    def _strip_top_margin(self, output: OutputState) -> float:
        strip_height = self._strip_height(output)
        return max(0.0, (output.logical.height - strip_height) / 2.0)

    def show(self) -> None:
        self.window.set_visible(True)

    def hide(self) -> None:
        self.window.set_visible(False)

    def destroy(self) -> None:
        self.window.destroy()


class SideController:
    def __init__(self, app: "EdgePreviewApplication", output: OutputState, monitor: Gdk.Monitor, side: Side) -> None:
        self.app = app
        self.side = side
        self.output = output
        self.pointer_y = output.logical.height / 2
        self.target: WindowState | None = None
        self.strip_hover = False
        self.preview_hover = False
        self.show_timer_id: int | None = None
        self.hide_timer_id: int | None = None

        self.preview = EdgePreviewWindow(
            app=app,
            monitor=monitor,
            side=side,
            config=app.config,
            on_enter=self.on_preview_enter,
            on_leave=self.on_preview_leave,
            on_click=self.activate_target,
        )
        self.strip = EdgeStripWindow(
            app=app,
            monitor=monitor,
            output=output,
            side=side,
            config=app.config,
            on_enter=self.on_strip_enter,
            on_leave=self.on_strip_leave,
            on_motion=self.on_strip_motion,
            on_click=self.activate_target,
        )

    def destroy(self) -> None:
        self._cancel_show()
        self._cancel_hide()
        self.preview.hide()
        self.preview.window.destroy()
        self.strip.destroy()

    def update_output(self, output: OutputState) -> None:
        self.output = output
        self.strip.update_output(output)

    def update_target(self, target: WindowState | None) -> None:
        previous_id = self.target.id if self.target is not None else None
        current_id = target.id if target is not None else None
        self.target = target
        if target is None:
            self.strip_hover = False
            self.preview_hover = False
            self.preview.hide()
            self.strip.hide()
            self._cancel_show()
            self._cancel_hide()
            return
        self.strip.show()
        if self.preview.is_visible() and previous_id != current_id:
            self.schedule_show(delay_ms=0)

    def on_strip_enter(self, y: float) -> None:
        self.strip_hover = True
        self.pointer_y = y
        self.app.logger.debug("strip enter: side=%s target=%s y=%s", self.side, self.target.id if self.target else None, y)
        self._cancel_hide()
        self.schedule_show()

    def on_strip_motion(self, y: float) -> None:
        self.pointer_y = y
        if self.preview.is_visible() and self.target is not None:
            self.preview.reposition(self.output, self.pointer_y)

    def on_strip_leave(self) -> None:
        self.strip_hover = False
        self._cancel_show()
        self._schedule_hide()

    def on_preview_enter(self) -> None:
        self.preview_hover = True
        self._cancel_hide()

    def on_preview_leave(self) -> None:
        self.preview_hover = False
        self._schedule_hide()

    def schedule_show(self, delay_ms: int | None = None) -> None:
        if self.target is None:
            return
        self._cancel_show()
        delay = self.app.config.preview_delay_ms if delay_ms is None else delay_ms
        self.show_timer_id = GLib.timeout_add(delay, self._show_preview)

    def activate_target(self) -> None:
        if self.target is None:
            return
        self.app.logger.debug("activate target: window=%s", self.target.id)
        self.preview_hover = False
        self.preview.hide()
        self._cancel_show()
        self._cancel_hide()
        self.app.focus_window(self.target.id)
        if self.strip_hover:
            self.schedule_show(delay_ms=self.app.config.post_click_delay_ms)

    def _show_preview(self) -> bool:
        self.show_timer_id = None
        if self.target is None or not (self.strip_hover or self.preview_hover):
            return False
        icon = self.app.resolve_window_icon(self.target)
        self.preview.show_icon(icon, self.target.title, self.output, self.pointer_y)
        return False

    def _schedule_hide(self) -> None:
        self._cancel_hide()
        self.hide_timer_id = GLib.timeout_add(self.app.config.hide_delay_ms, self._hide_if_unhovered)

    def _hide_if_unhovered(self) -> bool:
        self.hide_timer_id = None
        if not self.strip_hover and not self.preview_hover:
            self.preview.hide()
        return False

    def _cancel_show(self) -> None:
        if self.show_timer_id is not None:
            GLib.source_remove(self.show_timer_id)
            self.show_timer_id = None

    def _cancel_hide(self) -> None:
        if self.hide_timer_id is not None:
            GLib.source_remove(self.hide_timer_id)
            self.hide_timer_id = None


class OutputController:
    def __init__(self, app: "EdgePreviewApplication", output: OutputState, monitor: Gdk.Monitor) -> None:
        self.output = output
        self.left = SideController(app, output, monitor, "left")
        self.right = SideController(app, output, monitor, "right")

    def destroy(self) -> None:
        self.left.destroy()
        self.right.destroy()

    def update_output(self, output: OutputState) -> None:
        self.output = output
        self.left.update_output(output)
        self.right.update_output(output)


class EdgePreviewApplication(Gtk.Application):
    def __init__(self, config: AppConfig) -> None:
        super().__init__(application_id="dev.codex.niri-edge-switcher")
        self.config = config
        self.logger = logging.getLogger("niri-edge-switcher")
        self.client = NiriClient(self.logger)
        self.inter_column_spacing = (
            config.inter_column_spacing if config.inter_column_spacing is not None else _load_inter_column_spacing()
        )
        self.watcher = NiriEventWatcher(
            client=self.client,
            on_snapshot=lambda snapshot: GLib.idle_add(self.apply_snapshot, snapshot),
            on_error=lambda error: GLib.idle_add(self.report_error, error),
            logger=self.logger,
        )
        self.snapshot: Snapshot | None = None
        self.outputs: dict[str, OutputController] = {}
        self._shutting_down = False
        self._watcher_started = False

    def do_activate(self) -> None:
        self._install_css()
        self.hold()
        self.logger.info("edge switcher is starting")
        self.logger.debug("using inter-column spacing estimate: %.2f", self.inter_column_spacing)
        try:
            snapshot = self.client.load_snapshot()
        except Exception as error:  # pragma: no cover - runtime integration only
            self.logger.error("failed to load initial niri snapshot: %s", error)
        else:
            self.apply_snapshot(snapshot)
        if not self._watcher_started:
            self._watcher_started = True
            GLib.idle_add(self._start_watcher)

    def do_shutdown(self) -> None:
        self._shutting_down = True
        self.watcher.stop()
        for controller in self.outputs.values():
            controller.destroy()
        self.outputs.clear()
        try:
            self.release()
        except Exception:
            pass
        Gtk.Application.do_shutdown(self)

    def apply_snapshot(self, snapshot: Snapshot) -> bool:
        if self._shutting_down:
            return False
        self.snapshot = snapshot
        monitor_map = self._monitor_map()

        removed = set(self.outputs) - set(snapshot.outputs)
        for output_name in removed:
            self.outputs.pop(output_name).destroy()

        for output_name, output in snapshot.outputs.items():
            monitor = monitor_map.get(output_name)
            if monitor is None:
                continue

            controller = self.outputs.get(output_name)
            if controller is None:
                controller = OutputController(self, output, monitor)
                self.outputs[output_name] = controller
            else:
                controller.update_output(output)

            left_target = find_edge_window_with_spacing(
                snapshot,
                output_name,
                "left",
                inter_column_spacing=self.inter_column_spacing,
            )
            right_target = find_edge_window_with_spacing(
                snapshot,
                output_name,
                "right",
                inter_column_spacing=self.inter_column_spacing,
            )
            self.logger.debug(
                "targets for %s: left=%s right=%s",
                output_name,
                left_target.id if left_target else None,
                right_target.id if right_target else None,
            )
            controller.left.update_target(left_target)
            controller.right.update_target(right_target)

        return False

    def focus_window(self, window_id: int) -> None:
        try:
            self.client.focus_window(window_id)
        except Exception as error:  # pragma: no cover - runtime integration only
            self.report_error(str(error))

    def resolve_window_icon(self, window: WindowState) -> Gio.Icon:
        app_id = (window.app_id or "").strip()
        if app_id:
            desktop_ids = [app_id] if app_id.endswith(".desktop") else [f"{app_id}.desktop", app_id]
            for desktop_id in desktop_ids:
                try:
                    app_info = Gio.DesktopAppInfo.new(desktop_id)
                except TypeError:
                    continue
                if app_info is None:
                    continue
                icon = app_info.get_icon()
                if icon is not None:
                    return icon

        display = Gdk.Display.get_default()
        theme = Gtk.IconTheme.get_for_display(display) if display is not None else None
        seen: set[str] = set()
        for candidate in self._icon_name_candidates(app_id):
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            if theme is None or theme.has_icon(candidate):
                return Gio.ThemedIcon.new(candidate)
        return Gio.ThemedIcon.new("application-x-executable")

    def _icon_name_candidates(self, app_id: str) -> list[str]:
        if not app_id:
            return ["application-x-executable", "application-default-icon"]

        base = app_id[:-8] if app_id.endswith(".desktop") else app_id
        tail = base.rsplit(".", 1)[-1]
        candidates = [
            app_id,
            base,
            base.lower(),
            tail,
            tail.lower(),
            base.replace(".", "-"),
            base.replace(".", "-").lower(),
            base.replace(".", "_"),
            base.replace(".", "_").lower(),
            "application-x-executable",
            "application-default-icon",
        ]
        return [candidate for candidate in candidates if candidate]

    def report_error(self, error: str) -> bool:
        if self._shutting_down:
            return False
        self.logger.error("%s", error)
        return False

    def _start_watcher(self) -> bool:
        if not self._shutting_down:
            self.watcher.start()
        return False

    def _monitor_map(self) -> dict[str, Gdk.Monitor]:
        display = Gdk.Display.get_default()
        if display is None:
            return {}

        monitors = display.get_monitors()
        count = monitors.get_n_items()
        resolved: dict[str, Gdk.Monitor] = {}
        by_geometry: dict[tuple[int, int, int, int], Gdk.Monitor] = {}
        fallback: list[Gdk.Monitor] = []

        for index in range(count):
            monitor = monitors.get_item(index)
            geometry = monitor.get_geometry()
            by_geometry[(geometry.x, geometry.y, geometry.width, geometry.height)] = monitor
            fallback.append(monitor)

        for output_name, output in (self.snapshot.outputs.items() if self.snapshot else []):
            for monitor in fallback:
                connector_getter = getattr(monitor, "get_connector", None)
                if connector_getter is not None and connector_getter() == output_name:
                    resolved[output_name] = monitor
                    break
            if output_name in resolved:
                continue

            geometry_key = (
                output.logical.x,
                output.logical.y,
                output.logical.width,
                output.logical.height,
            )
            monitor = by_geometry.get(geometry_key)
            if monitor is not None:
                resolved[output_name] = monitor

        if len(fallback) == 1:
            only_monitor = fallback[0]
            for output_name in (self.snapshot.outputs if self.snapshot else {}):
                resolved.setdefault(output_name, only_monitor)

        return resolved

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(
            b"""
            window.edge-strip-window,
            window.edge-preview-window,
            window.edge-preview-window:backdrop,
            window.edge-strip-window:backdrop {
              background: transparent;
              background-color: transparent;
              background-image: none;
              box-shadow: none;
              border: none;
            }

            .edge-hot-strip {
              background: rgba(0, 0, 0, 0.012);
              border: none;
              box-shadow: none;
            }

            .edge-preview-card {
              background: transparent;
              background-color: transparent;
              background-image: none;
              border: none;
              border-radius: 18px;
              box-shadow: none;
            }

            .edge-preview-icon {
              color: #f5f7ff;
            }

            .edge-preview-title {
              color: #f5f7ff;
            }
            """
        )
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )


def parse_args(argv: list[str]) -> AppConfig:
    parser = argparse.ArgumentParser(description="PaperWM-like edge switcher for niri")
    parser.add_argument("--edge-width", type=int, default=1)
    parser.add_argument("--preview-delay-ms", type=int, default=120)
    parser.add_argument("--hide-delay-ms", type=int, default=140)
    parser.add_argument("--post-click-delay-ms", type=int, default=280)
    parser.add_argument("--icon-size", type=int, default=64)
    parser.add_argument("--title-max-width", type=int, default=220)
    parser.add_argument("--preview-margin", type=int, default=10)
    parser.add_argument(
        "--inter-column-spacing",
        type=float,
        default=None,
        help="override the estimated spacing between columns used when niri omits tile_pos_in_workspace_view",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    return AppConfig(
        edge_width=args.edge_width,
        preview_delay_ms=args.preview_delay_ms,
        hide_delay_ms=args.hide_delay_ms,
        post_click_delay_ms=args.post_click_delay_ms,
        icon_size=args.icon_size,
        title_max_width=args.title_max_width,
        preview_margin=args.preview_margin,
        inter_column_spacing=args.inter_column_spacing,
        log_level=args.log_level.upper(),
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    config = parse_args(argv)
    logging.basicConfig(level=getattr(logging, config.log_level, logging.INFO), format="%(levelname)s %(message)s")

    if os.environ.get("WAYLAND_DISPLAY") is None:
        print("This program must run inside a Wayland session.", file=sys.stderr)
        return 1
    if os.environ.get("NIRI_SOCKET") is None:
        print("NIRI_SOCKET is not set. Run inside an active niri session.", file=sys.stderr)
        return 1

    app = EdgePreviewApplication(config)
    return int(app.run([sys.argv[0]]))
