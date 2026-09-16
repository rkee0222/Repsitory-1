"""Plotting helpers shared by both modes.

  * `NoteAxis`      - a pyqtgraph AxisItem that labels the Y axis with note
                      names at semitone spacing (no per-semitone gridlines).
  * `AutoRangeY`    - a small controller that keeps the visible Y range centred
                      on recent pitch data but updates gently (hysteresis +
                      rate limiting) so the axis does not jitter.
"""
from __future__ import annotations

from typing import Iterable, Optional

import numpy as np
import pyqtgraph as pg

from .notes import midi_to_note_name, semitone_ticks


class NoteAxis(pg.AxisItem):
    """Y axis that renders tick labels as musical note names."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._max_ticks = 22

    def tickValues(self, minVal, maxVal, size):
        ticks = semitone_ticks(minVal, maxVal, max_ticks=self._max_ticks)
        if not ticks:
            return []
        positions = [p for p, _ in ticks]
        # Single "major" level; spacing is derived from the tick list.
        spacing = (positions[1] - positions[0]) if len(positions) > 1 else 1.0
        return [(spacing, positions)]

    def tickStrings(self, values, scale, spacing):
        return [midi_to_note_name(v) for v in values]


class AutoRangeY:
    """Compute a smoothly-updating Y range (in MIDI units) from pitch data.

    Strategy:
      * Track the min/max of recent valid pitch (in MIDI/semitone units).
      * Only move the visible window when the data leaves a comfortable inner
        margin (hysteresis), and then move the edges by at most `max_step`
        semitones per update (rate limiting).  This prevents nervous jumping
        while still following the singer up/down.
    """

    def __init__(self, min_span: float = 12.0, padding: float = 3.0,
                 max_step: float = 2.0, default_center: float = 57.0):
        # default_center 57 == A3, a comfortable mid-voice anchor.
        self.min_span = min_span
        self.padding = padding
        self.max_step = max_step
        self.lo = default_center - min_span / 2
        self.hi = default_center + min_span / 2

    def update(self, midi_values: Iterable[float]) -> Optional[tuple]:
        vals = [v for v in midi_values if v == v]  # drop NaN
        if not vals:
            return (self.lo, self.hi)
        arr = np.asarray(vals, dtype=np.float64)
        # Use robust percentiles so a single wild octave-error frame does not
        # yank the whole axis.
        data_lo = float(np.percentile(arr, 2))
        data_hi = float(np.percentile(arr, 98))

        target_lo = data_lo - self.padding
        target_hi = data_hi + self.padding
        # enforce minimum span
        span = target_hi - target_lo
        if span < self.min_span:
            center = 0.5 * (target_lo + target_hi)
            target_lo = center - self.min_span / 2
            target_hi = center + self.min_span / 2

        # Hysteresis: only adjust an edge if the target moves it "enough".
        moved = False
        if target_lo < self.lo - 0.01 or target_lo > self.lo + 1.0:
            step = np.clip(target_lo - self.lo, -self.max_step, self.max_step)
            self.lo += step
            moved = True
        if target_hi > self.hi + 0.01 or target_hi < self.hi - 1.0:
            step = np.clip(target_hi - self.hi, -self.max_step, self.max_step)
            self.hi += step
            moved = True
        if self.hi - self.lo < self.min_span:
            center = 0.5 * (self.lo + self.hi)
            self.lo = center - self.min_span / 2
            self.hi = center + self.min_span / 2
        _ = moved
        return (self.lo, self.hi)
