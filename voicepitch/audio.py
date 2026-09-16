"""Audio input: live microphone capture and recorded-file loading.

Kept independent of any GUI so it can be unit-tested and reused.
"""
from __future__ import annotations

import queue
from typing import Callable, Optional

import numpy as np


# --------------------------------------------------------------------------- #
# Live microphone
# --------------------------------------------------------------------------- #
class MicStream:
    """Non-blocking microphone capture using sounddevice (PortAudio).

    Audio arrives on PortAudio's own thread via a callback and is pushed onto a
    thread-safe queue, so the GUI thread never blocks.  Consumers poll `read()`.
    """

    def __init__(self, sample_rate: int = 44100, blocksize: int = 1024,
                 channels: int = 1, device: Optional[int] = None) -> None:
        self.sample_rate = sample_rate
        self.blocksize = blocksize
        self.channels = channels
        self.device = device
        self._queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=64)
        self._stream = None

    def _callback(self, indata, frames, time_info, status):  # noqa: D401
        # `status` may report overflows; we ignore and keep going (no gating).
        try:
            self._queue.put_nowait(indata[:, 0].copy())
        except queue.Full:
            # Drop the oldest block to stay real-time.
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(indata[:, 0].copy())
            except queue.Empty:
                pass

    def start(self) -> None:
        import sounddevice as sd  # lazy import

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            blocksize=self.blocksize,
            channels=self.channels,
            dtype="float32",
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()

    def read(self) -> Optional[np.ndarray]:
        """Return the next available audio block, or None if none is queued."""
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def read_all(self) -> Optional[np.ndarray]:
        """Drain every queued block and return them concatenated (or None)."""
        chunks = []
        while True:
            try:
                chunks.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if not chunks:
            return None
        return np.concatenate(chunks)

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
        # flush queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break


def list_input_devices():
    """Return a list of (index, name) for available input devices."""
    import sounddevice as sd

    devices = []
    for idx, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) > 0:
            devices.append((idx, dev["name"]))
    return devices


# --------------------------------------------------------------------------- #
# File loading (MP3 / WAV / M4A)
# --------------------------------------------------------------------------- #
def load_audio_file(path: str, target_sr: Optional[int] = None,
                    mono: bool = True):
    """Load an audio file to a float32 numpy array.

    WAV/FLAC/OGG are read directly via soundfile.  MP3/M4A/AAC and anything
    soundfile cannot open are loaded through librosa (which falls back to
    audioread/ffmpeg).  Returns (samples, sample_rate).
    """
    import os

    ext = os.path.splitext(path)[1].lower()
    # Fast path for formats soundfile handles natively.
    if ext in (".wav", ".flac", ".ogg", ".aiff", ".aif"):
        try:
            import soundfile as sf

            data, sr = sf.read(path, dtype="float32", always_2d=False)
            data = np.asarray(data, dtype=np.float32)
            if data.ndim > 1 and mono:
                data = data.mean(axis=1).astype(np.float32)
            if target_sr and target_sr != sr:
                data, sr = _resample(data, sr, target_sr), target_sr
            return data, sr
        except Exception:
            pass  # fall through to librosa

    # General path (handles MP3/M4A via ffmpeg/audioread).
    import librosa

    data, sr = librosa.load(path, sr=target_sr, mono=mono)
    return np.asarray(data, dtype=np.float32), int(sr)


def _resample(data: np.ndarray, sr: int, target_sr: int) -> np.ndarray:
    try:
        import librosa

        return librosa.resample(data, orig_sr=sr, target_sr=target_sr).astype(
            np.float32
        )
    except Exception:
        # crude linear fallback
        n_out = int(round(len(data) * target_sr / sr))
        x_old = np.linspace(0, 1, num=len(data), endpoint=False)
        x_new = np.linspace(0, 1, num=n_out, endpoint=False)
        return np.interp(x_new, x_old, data).astype(np.float32)
