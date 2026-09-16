"""pytest suite exercising the shipped NumPy code paths.

Run with:  pytest -q      (requires numpy; librosa only for the pyin test)

These tests use synthetic tones of known frequency (220 Hz A3, 261.63 Hz C4,
440 Hz A4) to validate accuracy, absence of octave errors, note mapping, the
stateful live detector, and the auto Y-range controller's stability.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from voicepitch.notes import (hz_to_midi, midi_to_hz, midi_to_note_name,
                              note_name_to_midi, semitone_ticks)
from voicepitch.pitch import YinDetector, yin_f0

SR = 44100
FRAME = 2048
TONES = {"A3": 220.0, "C4": 261.63, "A4": 440.0}


def _tone(freq, n=FRAME, sr=SR, amp=0.9, harmonics=()):
    t = np.arange(n) / sr
    sig = amp * np.sin(2 * np.pi * freq * t)
    for h, a in harmonics:
        sig += a * np.sin(2 * np.pi * freq * h * t)
    return sig.astype(np.float64)


def _cents(detected, truth):
    return abs(1200.0 * math.log2(detected / truth))


@pytest.mark.parametrize("name,freq", list(TONES.items()))
def test_pure_tone_accuracy(name, freq):
    est = yin_f0(_tone(freq), SR)
    assert _cents(est, freq) < 15, f"{name}: {est:.2f} Hz off by too much"


@pytest.mark.parametrize("name,freq", list(TONES.items()))
def test_no_octave_error_with_harmonics(name, freq):
    est = yin_f0(_tone(freq, harmonics=((2, 0.5), (3, 0.3))), SR)
    ratio = est / freq
    assert _cents(est, freq) < 60
    assert not (abs(ratio - 0.5) < 0.15 or abs(ratio - 2.0) < 0.3)


def test_note_mapping_roundtrip():
    assert midi_to_note_name(hz_to_midi(440.0)) == "A4"
    assert midi_to_note_name(hz_to_midi(261.63)) == "C4"
    assert midi_to_note_name(hz_to_midi(220.0)) == "A3"
    assert note_name_to_midi("A4") == 69
    assert abs(midi_to_hz(69) - 440.0) < 1e-6


def test_continuous_position_not_quantized():
    # A pitch a quarter-tone above A4 must map ~0.5 semitone above 69, not snap.
    f = 440.0 * 2 ** (0.5 / 12)
    m = hz_to_midi(f)
    assert 69.4 < m < 69.6


def test_semitone_ticks_are_note_names():
    ticks = semitone_ticks(57.0, 69.0)
    labels = [lbl for _, lbl in ticks]
    assert "A3" in labels and "A4" in labels


def test_live_detector_stream():
    det = YinDetector(sample_rate=SR, frame_size=FRAME, hop_size=512)
    sig = _tone(440.0, n=SR)  # 1 s
    ests = []
    for i in range(0, len(sig), 512):
        r = det.push(sig[i:i + 512])
        if r is not None and r == r:
            ests.append(r)
    assert ests, "detector produced no estimates"
    assert _cents(float(np.median(ests)), 440.0) < 15


def test_auto_range_is_stable():
    from voicepitch.plot import AutoRangeY

    ar = AutoRangeY()
    midi = hz_to_midi(261.63)
    last = None
    ranges = []
    for _ in range(30):
        lo, hi = ar.update([midi] * 20)
        ranges.append((lo, hi))
        last = (lo, hi)
    # After settling, consecutive updates should not move more than max_step.
    for (lo0, hi0), (lo1, hi1) in zip(ranges[5:], ranges[6:]):
        assert abs(lo1 - lo0) <= ar.max_step + 1e-6
        assert abs(hi1 - hi0) <= ar.max_step + 1e-6
    assert last[0] < midi < last[1]
