from __future__ import annotations

import struct
from math import ceil
from typing import Callable

from gi.repository import Gio, GLib


_FRAME_HEADER = struct.Struct("<HH")
_FIRST_FRAME_TIMEOUT_MS = 1500
_UNAVAILABLE_EXIT_CODE = 2
_PREVIEW_WIDTH = 320
_PREVIEW_HEIGHT = 200


class WindowCapture:
    def __init__(
        self,
        helper: str,
        window_id: int,
        on_frame: Callable[[int, int, bytes], None],
        on_failure: Callable[[str], None],
        *,
        scale: float,
    ) -> None:
        self.scale = scale
        self._max_width = ceil(_PREVIEW_WIDTH * scale)
        self._max_height = ceil(_PREVIEW_HEIGHT * scale)
        self._on_frame = on_frame
        self._on_failure = on_failure
        self._process = Gio.Subprocess.new(
            [helper, str(window_id), str(self._max_width), str(self._max_height)],
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE,
        )
        self._stream = self._process.get_stdout_pipe()
        self._cancellable = Gio.Cancellable()
        self._stopped = False
        self._dimensions: tuple[int, int] | None = None
        self._expected = _FRAME_HEADER.size
        self._buffer = bytearray()
        self._timeout: int | None = GLib.timeout_add(_FIRST_FRAME_TIMEOUT_MS, self._on_timeout)
        self._process.wait_async(None, self._on_exit)
        self._read()

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._clear_timeout()
        self._buffer.clear()
        self._cancellable.cancel()
        self._process.force_exit()

    def _read(self) -> None:
        self._stream.read_bytes_async(
            self._expected - len(self._buffer), GLib.PRIORITY_DEFAULT, self._cancellable, self._on_read
        )

    def _on_read(self, stream: Gio.InputStream, result: Gio.AsyncResult) -> None:
        try:
            chunk = stream.read_bytes_finish(result)
        except GLib.Error as error:
            self._fail(error.message)
            return

        if self._stopped or chunk.get_size() == 0:
            # The subprocess wait handles EOF and reports unsupported protocols.
            return

        self._buffer.extend(chunk.get_data())
        if len(self._buffer) == self._expected:
            if self._dimensions is None:
                width, height = _FRAME_HEADER.unpack(self._buffer)
                if not (1 <= width <= self._max_width and 1 <= height <= self._max_height):
                    self._fail("invalid capture dimensions")
                    return
                self._dimensions = width, height
                self._expected = width * height * 4
                self._buffer.clear()
            else:
                width, height = self._dimensions
                pixels = bytes(self._buffer)
                self._dimensions = None
                self._expected = _FRAME_HEADER.size
                self._buffer.clear()
                self._clear_timeout()
                self._on_frame(width, height, pixels)

        if not self._stopped:
            self._read()

    def _on_exit(self, process: Gio.Subprocess, result: Gio.AsyncResult) -> None:
        process.wait_finish(result)
        if process.get_if_exited() and process.get_exit_status() == _UNAVAILABLE_EXIT_CODE:
            self._fail("Wayland window capture protocols are unavailable")
        else:
            self._fail("window capture ended")

    def _clear_timeout(self) -> None:
        if self._timeout is not None:
            GLib.source_remove(self._timeout)
            self._timeout = None

    def _on_timeout(self) -> bool:
        self._timeout = None
        self._fail("timed out waiting for the first frame")
        return GLib.SOURCE_REMOVE

    def _fail(self, message: str) -> None:
        if not self._stopped:
            self.stop()
            self._on_failure(message)
