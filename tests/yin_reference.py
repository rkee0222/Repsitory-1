"""Pure-Python (stdlib only) YIN reference implementation.

This mirrors the math in `voicepitch.pitch.yin_f0` without NumPy, so the pitch
accuracy tests can run in any Python interpreter even when third-party packages
(numpy/scipy/librosa) are not installed.  It is used by both the pytest suite
and the standalone `tests/run_core_tests.py` runner.
"""
from __future__ import annotations

import math
from typing import List


def sine(freq: float, sr: float, n: int, amp: float = 0.9,
         phase: float = 0.0) -> List[float]:
    two_pi_f = 2.0 * math.pi * freq
    return [amp * math.sin(two_pi_f * i / sr + phase) for i in range(n)]


def add(a: List[float], b: List[float]) -> List[float]:
    return [x + y for x, y in zip(a, b)]


def _difference_function(x: List[float], tau_max: int) -> List[float]:
    """Direct O(N*tau_max) cumulative squared difference (stdlib only)."""
    n = len(x)
    tau_max = min(tau_max, n)
    d = [0.0] * tau_max
    for tau in range(1, tau_max):
        s = 0.0
        for j in range(0, n - tau):
            diff = x[j] - x[j + tau]
            s += diff * diff
        d[tau] = s
    return d


def _cmnd(d: List[float]) -> List[float]:
    out = [1.0] * len(d)
    running = 0.0
    for tau in range(1, len(d)):
        running += d[tau]
        out[tau] = d[tau] * tau / running if running > 0 else 1.0
    return out


def _absolute_threshold(cmnd: List[float], threshold: float,
                        tau_min: int) -> int:
    n = len(cmnd)
    tau = tau_min
    while tau < n:
        if cmnd[tau] < threshold:
            while tau + 1 < n and cmnd[tau + 1] < cmnd[tau]:
                tau += 1
            return tau
        tau += 1
    if n > tau_min:
        best = tau_min
        for t in range(tau_min, n):
            if cmnd[t] < cmnd[best]:
                best = t
        return best
    return -1


def _parabolic(cmnd: List[float], tau: int) -> float:
    if tau <= 0 or tau >= len(cmnd) - 1:
        return float(tau)
    a, b, c = cmnd[tau - 1], cmnd[tau], cmnd[tau + 1]
    denom = a + c - 2.0 * b
    if denom == 0.0:
        return float(tau)
    return tau + 0.5 * (a - c) / denom


def yin_f0(frame: List[float], sr: float, fmin: float = 65.0,
           fmax: float = 1000.0, threshold: float = 0.15) -> float:
    n = len(frame)
    if n < 4:
        return float("nan")
    tau_min = max(1, int(sr / fmax))
    tau_max = min(n - 1, int(sr / fmin) + 1)
    if tau_max <= tau_min:
        return float("nan")
    d = _difference_function(frame, tau_max)
    cmnd = _cmnd(d)
    tau = _absolute_threshold(cmnd, threshold, tau_min)
    if tau <= 0:
        return float("nan")
    tau_r = _parabolic(cmnd, tau)
    if tau_r <= 0:
        return float("nan")
    return sr / tau_r


def hz_to_midi(f: float) -> float:
    if not (f > 0):
        return float("nan")
    return 69 + 12 * math.log2(f / 440.0)
