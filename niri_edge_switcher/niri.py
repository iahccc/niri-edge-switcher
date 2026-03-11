from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from typing import Callable

from .model import Snapshot


class NiriCommandError(RuntimeError):
    pass


class NiriClient:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.niri_bin = _resolve_niri_binary()

    def load_snapshot(self) -> Snapshot:
        outputs_payload = self._run_json("outputs")
        workspaces_payload = self._run_json("workspaces")
        windows_payload = self._run_json("windows")
        return Snapshot.from_json(outputs_payload, workspaces_payload, windows_payload)

    def focus_window(self, window_id: int) -> None:
        self._run("action", "focus-window", "--id", str(window_id))

    def _run_json(self, *args: str) -> object:
        completed = self._run("--json", *args)
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise NiriCommandError(f"invalid JSON from niri msg {' '.join(args)}: {error}") from error

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        command = [self.niri_bin, "msg", *args]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            stderr = completed.stderr.strip() or "unknown error"
            raise NiriCommandError(stderr)
        return completed


def _resolve_niri_binary() -> str:
    env_override = os.environ.get("NIRI_BIN")
    if env_override:
        return env_override

    detected = shutil.which("niri")
    if detected:
        return detected

    system_niri = "/run/current-system/sw/bin/niri"
    if os.path.exists(system_niri):
        return system_niri

    return "niri"


class NiriEventWatcher:
    def __init__(
        self,
        client: NiriClient,
        on_snapshot: Callable[[Snapshot], None],
        on_error: Callable[[str], None],
        logger: logging.Logger | None = None,
    ) -> None:
        self.client = client
        self.on_snapshot = on_snapshot
        self.on_error = on_error
        self.logger = logger or logging.getLogger(__name__)
        self._lock = threading.Lock()
        self._refresh_pending = False
        self._running = False
        self._worker: threading.Thread | None = None
        self._event_socket: socket.socket | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker = threading.Thread(target=self._run, name="niri-event-watcher", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._running = False
        event_socket = self._event_socket
        if event_socket is not None:
            try:
                event_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            event_socket.close()
        worker = self._worker
        if worker is not None:
            worker.join(timeout=1.0)

    def request_refresh(self) -> None:
        with self._lock:
            self._refresh_pending = True

    def _run(self) -> None:
        self.request_refresh()
        while self._running:
            self._flush_refresh()
            self._run_event_stream()
            if self._running:
                time.sleep(0.5)

    def _run_event_stream(self) -> None:
        self.logger.debug("starting event stream")
        socket_path = os.environ.get("NIRI_SOCKET")
        if not socket_path:
            self.on_error("NIRI_SOCKET is not set")
            return

        try:
            event_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            event_socket.connect(socket_path)
            event_socket.sendall(b'"EventStream"\n')
            event_socket.shutdown(socket.SHUT_WR)
            event_file = event_socket.makefile("r", encoding="utf-8", newline="\n")
        except OSError as error:
            self.on_error(f"failed to connect to niri event stream: {error}")
            return

        self._event_socket = event_socket
        try:
            for line in event_file:
                if not self._running:
                    break
                line = line.strip()
                if not line:
                    continue
                if self._should_refresh(line):
                    self.request_refresh()
                    self._flush_refresh()
        except OSError as error:
            if self._running:
                self.on_error(f"niri event stream disconnected: {error}")
        finally:
            self._event_socket = None
            try:
                event_file.close()
            except Exception:
                pass
            try:
                event_socket.close()
            except OSError:
                pass

    def _flush_refresh(self) -> None:
        with self._lock:
            if not self._refresh_pending:
                return
            self._refresh_pending = False

        try:
            snapshot = self.client.load_snapshot()
        except NiriCommandError as error:
            self.on_error(str(error))
            return

        self.on_snapshot(snapshot)

    def _should_refresh(self, line: str) -> bool:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return False

        if not isinstance(payload, dict) or not payload:
            return False

        event_name = next(iter(payload))
        if event_name in {"ConfigLoaded"}:
            return True
        return event_name.startswith(("Window", "Workspace", "Output"))
