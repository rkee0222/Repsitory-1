"""Pitch (fundamental frequency, F0) detection.

Two detectors are provided:

  * `yin_f0` / `YinDetector`: a compact, well-tested implementation of the YIN
    algorithm (de Cheveigne & Kawahara, 2002) with parabolic interpolation.
    It is cheap enough to run block-by-block for the *live microphone* mode with
    a good responsiveness/accuracy balance.

  * `pyin_track`: a thin wrapper over librosa's probabilistic YIN (pYIN), used
    for *whole-file analysis*.  pYIN is the validated, probabilistic refinement
    of YIN and gives the most reliable voice F0 with markedly fewer octave
    errors, at a higher compute cost that is fine for offline file analysis.

No smoothing / moving-average is applied here.  We deliberately do NOT add a
voiced/unvoiced/noise classifier: whatever F0 the algorithm returns is returned
verbatim.  (For live YIN, a frame only yields NaN when the algorithm finds no
period at all within the search range; this is intrinsic to YIN, not an added
gate.)

The YIN core is written to depend only on NumPy so it is easy to reason about
and test.  A NumPy-free mirror of the same math lives in
`tests/yin_reference.py` so the accuracy tests can run without any third-party
packages installed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np


# --------------------------------------------------------------------------- #
# YIN core (real-time capable)
# --------------------------------------------------------------------------- #
def _difference_function(x: np.ndarray, tau_max: int) -> np.ndarray:
    """YIN step 2: cumulative squared difference d(tau).

    Computed efficiently via autocorrelation:
        d(tau) = r(0) + r_tau(0) - 2 * r(tau)
    where r is the autocorrelation.  This is O(N log N).
    """
    n = len(x)
    tau_max = min(tau_max, n)
    x = x.astype(np.float64, copy=False)

    # Autocorrelation via FFT.
    size = 1
    while size < 2 * n:
        size <<= 1
    fft = np.fft.rfft(x, size)
    acf = np.fft.irfft(fft * np.conjugate(fft), size)[:tau_max]

    # Cumulative energy terms.
    power = np.cumsum(x * x)
    # energy of first (n - tau) samples:
    e0 = power[n - 1]  # total energy
    d = np.empty(tau_max, dtype=np.float64)
    d[0] = 0.0
    for tau in range(1, tau_max):
        # energy of x[0:n-tau] and x[tau:n]
        e_left = power[n - tau - 1]
        e_right = e0 - power[tau - 1]
        d[tau] = e_left + e_right - 2.0 * acf[tau]
    return d


def _cumulative_mean_normalized_difference(d: np.ndarray) -> np.ndarray:
    """YIN step 3: CMND function d'(tau)."""
    cmnd = np.empty_like(d)
    cmnd[0] = 1.0
    running = 0.0
    for tau in range(1, len(d)):
        running += d[tau]
        cmnd[tau] = d[tau] * tau / running if running > 0 else 1.0
    return cmnd


def _absolute_threshold(cmnd: np.ndarray, threshold: float,
                        tau_min: int) -> int:
    """YIN step 4: pick the first dip below threshold (else global min)."""
    tau = tau_min
    n = len(cmnd)
    while tau < n:
        if cmnd[tau] < threshold:
            # descend to the local minimum of this dip
            while tau + 1 < n and cmnd[tau + 1] < cmnd[tau]:
                tau += 1
            return tau
        tau += 1
    # No dip below threshold: fall back to the global minimum (after tau_min).
    if n > tau_min:
        return int(np.argmin(cmnd[tau_min:]) + tau_min)
    return -1


def _parabolic_interpolation(cmnd: np.ndarray, tau: int) -> float:
    """YIN step 5: refine the period estimate to sub-sample precision."""
    if tau <= 0 or tau >= len(cmnd) - 1:
        return float(tau)
    a = cmnd[tau - 1]
    b = cmnd[tau]
    c = cmnd[tau + 1]
    denom = a + c - 2.0 * b
    if denom == 0.0:
        return float(tau)
    return tau + 0.5 * (a - c) / denom


def yin_f0(frame: np.ndarray, sample_rate: float,
           fmin: float = 65.0, fmax: float = 1000.0,
           threshold: float = 0.15) -> float:
    """Estimate F0 (Hz) of a single frame using YIN. Returns NaN if none found.

    fmin/fmax bound the search (human voice ~= 65-1000 Hz covers bass to soprano
    plus a margin).  `threshold` is YIN's aperiodicity threshold.
    """
    frame = np.asarray(frame, dtype=np.float64)
    n = len(frame)
    if n < 4:
        return float("nan")

    tau_min = max(1, int(sample_rate / fmax))
    tau_max = min(n - 1, int(sample_rate / fmin) + 1)
    if tau_max <= tau_min:
        return float("nan")

    d = _difference_function(frame, tau_max)
    cmnd = _cumulative_mean_normalized_difference(d)
    tau = _absolute_threshold(cmnd, threshold, tau_min)
    if tau <= 0:
        return float("nan")

    tau_refined = _parabolic_interpolation(cmnd, tau)
    if tau_refined <= 0:
        return float("nan")
    return float(sample_rate / tau_refined)


@dataclass
class YinDetector:
    """Stateful helper for the live mode.

    Call `push(block)` with successive audio blocks; it maintains an internal
    ring buffer of `frame_size` samples and returns an F0 estimate for the most
    recent window each time enough new audio has arrived (>= `hop_size`).
    """
    sample_rate: float
    frame_size: int = 2048
    hop_size: int = 512
    fmin: float = 65.0
    fmax: float = 1000.0
    threshold: float = 0.15

    def __post_init__(self) -> None:
        self._buf = np.zeros(self.frame_size, dtype=np.float64)
        self._filled = 0
        self._since_hop = 0

    def push(self, block: np.ndarray) -> Optional[float]:
        block = np.asarray(block, dtype=np.float64).reshape(-1)
        if block.size == 0:
            return None
        # shift buffer and append newest samples
        k = min(block.size, self.frame_size)
        if k < block.size:
            block = block[-k:]
        self._buf = np.roll(self._buf, -block.size)
        self._buf[-block.size:] = block
        self._filled = min(self.frame_size, self._filled + block.size)
        self._since_hop += block.size
        if self._filled < self.frame_size or self._since_hop < self.hop_size:
            return None
        self._since_hop = 0
        return yin_f0(self._buf, self.sample_rate,
                      self.fmin, self.fmax, self.threshold)


# --------------------------------------------------------------------------- #
# pYIN wrapper (offline / whole-file)
# --------------------------------------------------------------------------- #
def pyin_track(y: np.ndarray, sample_rate: float,
               fmin: float = 65.0, fmax: float = 1000.0,
               frame_length: int = 2048,
               hop_length: int = 256) -> Tuple[np.ndarray, np.ndarray]:
    """Run librosa's pYIN over an audio signal.

    Returns (times_seconds, f0_hz).  Frames pYIN marks unvoiced come back as
    NaN from librosa; we return them as-is (NaN simply produces a gap in the
    plot).  We do not add any extra classification or smoothing.
    """
    import librosa  # imported lazily so tests that don't need it still run

    y = np.asarray(y, dtype=np.float32)
    f0, _voiced_flag, _voiced_prob = librosa.pyin(
        y,
        fmin=fmin,
        fmax=fmax,
        sr=sample_rate,
        frame_length=frame_length,
        hop_length=hop_length,
        center=True,
    )
    times = librosa.times_like(f0, sr=sample_rate, hop_length=hop_length)
    return times, f0
