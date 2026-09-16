"""Musical note <-> frequency mapping utilities.

The Y axis of the pitch graph uses musical note names (e.g. A2, A#2, B2, C3...)
at *semitone* spacing.  Crucially, the pitch data itself is NOT quantized: a
detected frequency is mapped to a continuous "MIDI note number" position so that,
e.g., a pitch slightly above A3 lands between A3 and A#3 at its true position.

Reference: MIDI note 69 == A4 == 440 Hz.
    midi = 69 + 12 * log2(f / 440)
    f    = 440 * 2 ** ((midi - 69) / 12)
"""
from __future__ import annotations

import math
from typing import List, Tuple

A4_HZ = 440.0
A4_MIDI = 69

# Sharps notation (matches the examples in the spec: A#, C#, D# ...).
_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def hz_to_midi(freq_hz: float) -> float:
    """Convert a frequency in Hz to a continuous (fractional) MIDI note number.

    Returns NaN for non-positive / invalid frequencies.
    """
    if freq_hz is None or not (freq_hz > 0.0):
        return float("nan")
    return A4_MIDI + 12.0 * math.log2(freq_hz / A4_HZ)


def midi_to_hz(midi: float) -> float:
    """Convert a (fractional) MIDI note number back to Hz."""
    return A4_HZ * (2.0 ** ((midi - A4_MIDI) / 12.0))


def midi_to_note_name(midi: float) -> str:
    """Return the note name (e.g. 'A#3') for the nearest semitone of `midi`."""
    m = int(round(midi))
    name = _NOTE_NAMES[m % 12]
    octave = m // 12 - 1  # MIDI 60 == C4
    return f"{name}{octave}"


def note_name_to_midi(name: str) -> int:
    """Parse a note name like 'A#3' or 'C4' into a MIDI note number."""
    name = name.strip()
    i = 1
    if len(name) > 1 and name[1] in ("#", "b"):
        i = 2
    pitch_class = name[:i]
    octave = int(name[i:])
    # normalise flats to sharps
    flat_map = {"Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#"}
    pitch_class = flat_map.get(pitch_class, pitch_class)
    semitone = _NOTE_NAMES.index(pitch_class)
    return (octave + 1) * 12 + semitone


def semitone_ticks(midi_min: float, midi_max: float,
                   max_ticks: int = 24) -> List[Tuple[float, str]]:
    """Build (position, label) tick pairs at semitone resolution.

    Tick positions are integer MIDI numbers (semitones); labels are note names.
    If the range spans too many semitones for `max_ticks`, the tick *step* is
    increased (2, 3, 4, 6, 12 semitones) so labels stay readable, but data is
    still plotted continuously.
    """
    lo = int(math.floor(midi_min))
    hi = int(math.ceil(midi_max))
    if hi <= lo:
        hi = lo + 1

    span = hi - lo
    step = 1
    for candidate in (1, 2, 3, 4, 6, 12):
        if span / candidate <= max_ticks:
            step = candidate
            break
    else:
        step = 12

    # Align the first tick to a multiple of `step` for tidy labels.
    start = lo - (lo % step)
    ticks: List[Tuple[float, str]] = []
    m = start
    while m <= hi:
        if m >= lo:
            ticks.append((float(m), midi_to_note_name(float(m))))
        m += step
    return ticks
