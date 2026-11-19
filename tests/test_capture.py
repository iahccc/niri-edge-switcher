from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Callable

try:
    from gi.repository import GLib
except ModuleNotFoundError:
    GLib = None
else:
    from niri_edge_switcher.capture import WindowCapture


@unittest.skipUnless(GLib is not None, "requires PyGObject")
class CaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.loop = GLib.MainLoop()
        self.frames: list[tuple[int, int, bytes]] = []
        self.errors: list[str] = []

    def start_capture(
        self, script: str, on_frame: Callable[[int, int, bytes], None] | None = None, *, scale: float = 1.0
    ) -> WindowCapture:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        helper = Path(directory.name) / "capture"
        helper.write_text(f"#!{sys.executable}\n" + textwrap.dedent(script))
        helper.chmod(0o700)

        def on_failure(message: str) -> None:
            self.errors.append(message)
            self.loop.quit()

        capture = WindowCapture(str(helper), 7, on_frame or self.record_frame, on_failure, scale=scale)
        self.addCleanup(capture.stop)
        return capture

    def record_frame(self, width: int, height: int, pixels: bytes) -> None:
        self.frames.append((width, height, pixels))

    def run_loop(self, time_limit_ms: int = 3000) -> None:
        deadline = GLib.timeout_source_new(time_limit_ms)
        deadline.set_callback(lambda *_args: self.loop.quit())
        deadline.attach()
        try:
            self.loop.run()
        finally:
            deadline.destroy()

    def test_reads_fragmented_headers_and_frames_of_different_sizes(self) -> None:
        def on_frame(width: int, height: int, pixels: bytes) -> None:
            self.record_frame(width, height, pixels)
            if len(self.frames) == 2:
                self.loop.quit()

        self.start_capture(
            """
            import os
            import struct
            import sys
            import time
            assert sys.argv[1:] == ['7', '640', '400']
            for width, height, pixels in ((2, 1, b'a' * 8), (640, 400, b'b' * (640 * 400 * 4))):
                packet = struct.pack('<HH', width, height) + pixels
                for part in (packet[:1], packet[1:6], packet[6:]):
                    os.write(1, part)
                    time.sleep(0.02)
            time.sleep(10)
            """,
            on_frame,
            scale=2.0,
        )
        self.run_loop()
        self.assertEqual(self.frames, [(2, 1, b"a" * 8), (640, 400, b"b" * (640 * 400 * 4))])
        self.assertEqual(self.errors, [])

    def test_cancelled_capture_does_not_publish_queued_frames(self) -> None:
        def on_frame(width: int, height: int, pixels: bytes) -> None:
            self.record_frame(width, height, pixels)
            capture.stop()

        capture = self.start_capture(
            """
            import os
            import struct
            import time
            packet = struct.pack('<HH', 1, 1) + b'abcd'
            os.write(1, packet * 2)
            time.sleep(10)
            """,
            on_frame,
        )
        self.run_loop(time_limit_ms=300)
        self.assertEqual(self.frames, [(1, 1, b"abcd")])
        self.assertEqual(self.errors, [])

    def test_timeout_and_unsupported_protocols_report_failure(self) -> None:
        for script, reason in (
            ("import time\ntime.sleep(10)", "timed out"),
            ("raise SystemExit(2)", "protocols are unavailable"),
        ):
            with self.subTest(reason=reason):
                self.errors.clear()
                self.start_capture(script)
                self.run_loop()
                self.assertEqual(len(self.errors), 1)
                self.assertIn(reason, self.errors[0])
        self.assertEqual(self.frames, [])

    def test_process_exit_after_a_frame_reports_failure(self) -> None:
        self.start_capture(
            """
            import os
            import struct
            import time
            os.write(1, struct.pack('<HH', 1, 1) + b'abcd')
            time.sleep(0.15)
            raise SystemExit(1)
            """
        )
        self.run_loop()
        self.assertEqual(self.frames, [(1, 1, b"abcd")])
        self.assertEqual(self.errors, ["window capture ended"])
