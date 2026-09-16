"""Standalone pitch-accuracy checks that run WITHOUT any third-party packages.

Run with:  python tests/run_core_tests.py

This verifies the YIN core against synthetic test tones of known frequency and
checks that:
  * detected F0 is within a tight tolerance (< ~0.4 semitone) of the truth,
  * there are no octave errors (detected within 30% of a half/double of truth),
  * vibrato is preserved (detected F0 actually varies when the input does).

The pure-Python reference in `yin_reference.py` uses the identical algorithm as
the app's NumPy `voicepitch.pitch.yin_f0`, so passing here validates the core
math used by the shipped program.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from yin_reference import add, hz_to_midi, sine, yin_f0  # noqa: E402

SR = 44100
FRAME = 2048
TONES = {"A3": 220.0, "C4": 261.63, "A4": 440.0}
TOL_SEMITONES = 0.4


def _cents_error(detected: float, truth: float) -> float:
    return abs(1200.0 * math.log2(detected / truth))


def test_known_tones() -> list:
    results = []
    for name, freq in TONES.items():
        frame = sine(freq, SR, FRAME)
        est = yin_f0(frame, SR)
        err_semi = abs(hz_to_midi(est) - hz_to_midi(freq))
        cents = _cents_error(est, freq)
        ok = err_semi < TOL_SEMITONES
        results.append((f"pure tone {name} ({freq} Hz)", ok,
                        f"detected {est:.2f} Hz, err {cents:.1f} cents"))
    return results


def test_no_octave_errors() -> list:
    """A tone plus harmonics (voice-like) must not resolve to an octave off."""
    results = []
    for name, f in TONES.items():
        # fundamental + 2nd + 3rd harmonics (typical voiced spectrum)
        frame = sine(f, SR, FRAME, amp=0.7)
        frame = add(frame, sine(2 * f, SR, FRAME, amp=0.4))
        frame = add(frame, sine(3 * f, SR, FRAME, amp=0.25))
        est = yin_f0(frame, SR)
        ratio = est / f
        octave_off = (abs(ratio - 0.5) < 0.15 or abs(ratio - 2.0) < 0.3
                      or abs(ratio - 3.0) < 0.4)
        near_truth = _cents_error(est, f) < 60
        ok = near_truth and not octave_off
        results.append((f"harmonic-rich {name} octave check", ok,
                        f"detected {est:.2f} Hz (ratio {ratio:.2f})"))
    return results


def test_vibrato_preserved() -> list:
    """With frequency modulation, detected F0 across frames must actually vary
    (i.e. we are not accidentally flattening / smoothing)."""
    base, depth_semi, vib_rate = 261.63, 0.5, 6.0  # +/- half semitone at 6 Hz
    n_total = SR  # 1 second
    samples = []
    phase = 0.0
    for i in range(n_total):
        t = i / SR
        f_inst = base * (2 ** (depth_semi / 12.0 * math.sin(2 * math.pi * vib_rate * t)))
        phase += 2 * math.pi * f_inst / SR
        samples.append(0.9 * math.sin(phase))
    hop = 1024
    ests = []
    pos = 0
    while pos + FRAME <= n_total:
        est = yin_f0(samples[pos:pos + FRAME], SR)
        if est == est:
            ests.append(est)
        pos += hop
    spread = (max(ests) - min(ests)) if ests else 0.0
    ok = spread > 3.0  # Hz; vibrato should produce clearly varying F0
    return [("vibrato preserved (F0 varies, not flattened)", ok,
             f"F0 spread {spread:.2f} Hz across {len(ests)} frames")]


def main() -> int:
    all_results = []
    all_results += test_known_tones()
    all_results += test_no_octave_errors()
    all_results += test_vibrato_preserved()

    print("=" * 68)
    print("VoicePitch core pitch-accuracy checks (stdlib-only YIN reference)")
    print("=" * 68)
    passed = 0
    for name, ok, detail in all_results:
        mark = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        print(f"[{mark}] {name}\n        {detail}")
    print("-" * 68)
    print(f"{passed}/{len(all_results)} checks passed")
    return 0 if passed == len(all_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
