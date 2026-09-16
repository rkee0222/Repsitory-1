"""VoicePitch GUI (PySide6 + pyqtgraph).

Layout:
  * Top bar: mode toggle (Live mic / File), plus the minimal controls each mode
    needs (Start/Stop for live; Open + status for file).
  * Centre: a large pitch graph filling most of the window.

Threading model:
  * Live mode: a QTimer polls the mic queue (~30 Hz) on the GUI thread, runs the
    lightweight YIN detector on new audio, and appends points.  Audio capture
    itself happens on PortAudio's thread, so the UI never blocks.
  * File mode: pYIN analysis runs on a QThread worker; the GUI shows progress
    and stays responsive.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from .audio import MicStream, load_audio_file
from .notes import hz_to_midi
from .pitch import YinDetector, pyin_track
from .plot import AutoRangeY, NoteAxis

# Live-mode configuration.
LIVE_SR = 44100
LIVE_BLOCK = 1024
LIVE_FRAME = 2048
LIVE_HOP = 512
WINDOW_SECONDS = 5.0
POLL_MS = 33  # ~30 fps UI updates

pg.setConfigOptions(antialias=True, background="#101216", foreground="#d8dee9")


# --------------------------------------------------------------------------- #
# File analysis worker
# --------------------------------------------------------------------------- #
class FileAnalyzer(QtCore.QThread):
    finished_ok = QtCore.Signal(object, object, float)  # times, f0, duration
    failed = QtCore.Signal(str)
    progress = QtCore.Signal(str)

    def __init__(self, path: str):
        super().__init__()
        self._path = path

    def run(self):
        try:
            self.progress.emit("Loading file...")
            y, sr = load_audio_file(self._path, target_sr=22050, mono=True)
            if y.size == 0:
                self.failed.emit("The file contains no audio samples.")
                return
            duration = len(y) / sr
            self.progress.emit(
                f"Analyzing pitch (pYIN) - {duration:.1f}s of audio..."
            )
            times, f0 = pyin_track(y, sr, fmin=65.0, fmax=1000.0,
                                   frame_length=2048, hop_length=256)
            self.finished_ok.emit(times, f0, duration)
        except Exception as exc:  # surface errors to the UI
            self.failed.emit(f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VoicePitch - Voice Pitch Analyzer")
        self.resize(1100, 680)

        self._mic: Optional[MicStream] = None
        self._detector: Optional[YinDetector] = None
        self._analyzer: Optional[FileAnalyzer] = None

        # Rolling live buffers (time in seconds, pitch in MIDI units).
        self._t = deque()
        self._m = deque()
        self._t0 = 0.0

        self._auto_y = AutoRangeY()

        self._build_ui()
        self._show_live_controls(True)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._on_tick)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # --- Top bar ---------------------------------------------------
        top = QtWidgets.QHBoxLayout()
        self.mode_group = QtWidgets.QButtonGroup(self)
        self.btn_live = QtWidgets.QRadioButton("Live microphone")
        self.btn_file = QtWidgets.QRadioButton("Recorded file")
        self.btn_live.setChecked(True)
        self.mode_group.addButton(self.btn_live)
        self.mode_group.addButton(self.btn_file)
        self.btn_live.toggled.connect(self._on_mode_changed)
        top.addWidget(self.btn_live)
        top.addWidget(self.btn_file)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.VLine)
        top.addWidget(sep)

        # Live controls
        self.btn_start = QtWidgets.QPushButton("Start")
        self.btn_stop = QtWidgets.QPushButton("Stop")
        self.btn_stop.setEnabled(False)
        self.btn_start.clicked.connect(self._start_live)
        self.btn_stop.clicked.connect(self._stop_live)
        top.addWidget(self.btn_start)
        top.addWidget(self.btn_stop)

        # File controls
        self.btn_open = QtWidgets.QPushButton("Open file...")
        self.btn_open.clicked.connect(self._open_file)
        top.addWidget(self.btn_open)

        top.addStretch(1)
        self.status = QtWidgets.QLabel("Ready.")
        self.status.setStyleSheet("color:#9aa5b1;")
        top.addWidget(self.status)
        root.addLayout(top)

        # --- Graph -----------------------------------------------------
        self.note_axis = NoteAxis(orientation="left")
        self.plot = pg.PlotWidget(axisItems={"left": self.note_axis})
        self.plot.setLabel("bottom", "Time", units="s")
        self.plot.showGrid(x=True, y=False, alpha=0.15)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.setMenuEnabled(False)
        self.curve = self.plot.plot(
            [], [], pen=None, symbol="o", symbolSize=3,
            symbolBrush=pg.mkBrush("#4cc2ff"), symbolPen=None,
        )
        root.addWidget(self.plot, stretch=1)

    def _show_live_controls(self, live: bool):
        self.btn_start.setVisible(live)
        self.btn_stop.setVisible(live)
        self.btn_open.setVisible(not live)

    # -------------------------------------------------------------- mode
    def _on_mode_changed(self):
        live = self.btn_live.isChecked()
        if not live:
            self._stop_live()
        self._show_live_controls(live)
        self._clear_plot()
        self.status.setText("Ready.")
        if live:
            self.plot.setLabel("bottom", "Time", units="s")
            self.plot.setXRange(-WINDOW_SECONDS, 0, padding=0)
        else:
            self.plot.enableAutoRange(axis="x")

    def _clear_plot(self):
        self._t.clear()
        self._m.clear()
        self.curve.setData([], [])

    # -------------------------------------------------------------- live
    def _start_live(self):
        if self._mic is not None:
            return
        try:
            self._mic = MicStream(sample_rate=LIVE_SR, blocksize=LIVE_BLOCK,
                                  channels=1)
            self._mic.start()
        except Exception as exc:
            self._mic = None
            QtWidgets.QMessageBox.critical(
                self, "Microphone error",
                f"Could not start microphone:\n{exc}")
            return
        self._detector = YinDetector(sample_rate=LIVE_SR, frame_size=LIVE_FRAME,
                                     hop_size=LIVE_HOP, fmin=65.0, fmax=1000.0)
        self._clear_plot()
        self._t0 = time.monotonic()
        self._sample_clock = 0
        self._timer.start()
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_file.setEnabled(False)
        self.status.setText("Listening...")

    def _stop_live(self):
        self._timer.stop()
        if self._mic is not None:
            self._mic.stop()
            self._mic = None
        self._detector = None
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_file.setEnabled(True)
        if self.btn_live.isChecked():
            self.status.setText("Stopped.")

    def _on_tick(self):
        if self._mic is None or self._detector is None:
            return
        block = self._mic.read_all()
        if block is None:
            return
        # Feed audio to the detector in hop-sized steps for even time spacing.
        # Advance the sample clock by every sample actually consumed (whether or
        # not a frame produced an estimate) so the time axis stays accurate.
        n = len(block)
        pos = 0
        while pos < n:
            chunk = block[pos:pos + LIVE_HOP]
            consumed = len(chunk)
            pos += LIVE_HOP
            f0 = self._detector.push(chunk)
            self._sample_clock += consumed
            if f0 is None:
                continue
            # Timestamp the estimate at the centre of its analysis frame.
            t = (self._sample_clock - LIVE_FRAME / 2) / LIVE_SR
            midi = hz_to_midi(f0)
            self._t.append(t)
            self._m.append(midi)

        # Trim to the 10s window.
        if self._t:
            t_now = self._t[-1]
            while self._t and self._t[0] < t_now - WINDOW_SECONDS:
                self._t.popleft()
                self._m.popleft()
            self._redraw_live(t_now)

    def _redraw_live(self, t_now: float):
        ts = np.fromiter(self._t, dtype=np.float64)
        ms = np.fromiter(self._m, dtype=np.float64)
        # Scroll: x=0 at the right edge (now), negative to the left.
        x = ts - t_now
        valid = ~np.isnan(ms)
        self.curve.setData(x[valid], ms[valid])
        self.plot.setXRange(-WINDOW_SECONDS, 0, padding=0)
        lo, hi = self._auto_y.update(ms[valid])
        self.plot.setYRange(lo, hi, padding=0)

    # -------------------------------------------------------------- file
    def _open_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open audio file", "",
            "Audio files (*.mp3 *.wav *.m4a *.flac *.ogg);;All files (*)")
        if not path:
            return
        self.btn_open.setEnabled(False)
        self._clear_plot()
        self.status.setText("Loading file...")
        self._analyzer = FileAnalyzer(path)
        self._analyzer.progress.connect(self.status.setText)
        self._analyzer.finished_ok.connect(self._on_file_done)
        self._analyzer.failed.connect(self._on_file_failed)
        self._analyzer.finished.connect(lambda: self.btn_open.setEnabled(True))
        self._analyzer.start()

    def _on_file_failed(self, msg: str):
        self.status.setText("Analysis failed.")
        QtWidgets.QMessageBox.warning(self, "Analysis failed", msg)

    def _on_file_done(self, times, f0, duration):
        midi = np.array([hz_to_midi(f) for f in f0], dtype=np.float64)
        valid = ~np.isnan(midi)
        self.curve.setData(np.asarray(times)[valid], midi[valid])
        self.plot.setXRange(0, duration, padding=0.01)
        if valid.any():
            lo, hi = self._auto_y.update(midi[valid])
            self.plot.setYRange(lo, hi, padding=0)
            n_voiced = int(valid.sum())
        else:
            n_voiced = 0
        self.status.setText(
            f"Done. {duration:.1f}s, {n_voiced} pitched frames.")

    # ------------------------------------------------------------- close
    def closeEvent(self, event):
        self._stop_live()
        if self._analyzer is not None and self._analyzer.isRunning():
            self._analyzer.wait(2000)
        super().closeEvent(event)


def main():
    import sys

    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
