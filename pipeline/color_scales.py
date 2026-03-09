"""
Color scale definitions for ERA5 variables.

These scales are used by tile_generator.py to map floating-point data values
to RGBA pixel values in the output PNG tiles.

IMPORTANT: These numeric stops must stay in sync with the frontend's
constants/variables.js — both files define the same color scales so that the
Legend component accurately represents what's painted on the map.

Each entry in SCALES is:
    variable_id → list of (value, (R, G, B)) tuples

Values outside the [min, max] range are clamped before interpolation.
"""

from typing import NamedTuple


class ColorStop(NamedTuple):
    value: float
    r: int
    g: int
    b: int


def _stops(*pairs) -> list[ColorStop]:
    """Helper: convert (value, hex_str) pairs to ColorStop namedtuples."""
    result = []
    for value, hex_color in pairs:
        hex_color = hex_color.lstrip('#')
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        result.append(ColorStop(value, r, g, b))
    return result


# ---------------------------------------------------------------------------
# Scale definitions — (data_value, hex_color)
# ---------------------------------------------------------------------------

SCALES: dict[str, list[ColorStop]] = {
    "wind_speed": _stops(
        (0,   "#f0f9ff"),
        (5,   "#7dd3fc"),
        (10,  "#22c55e"),
        (20,  "#facc15"),
        (30,  "#f97316"),
        (40,  "#ef4444"),
        (55,  "#7c3aed"),
        (64,  "#1e1b4b"),
    ),
    "wind_dir": _stops(
        # Direction uses a cyclic hue scale; 0° and 360° must match
        (0,   "#ef4444"),
        (90,  "#22c55e"),
        (180, "#3b82f6"),
        (270, "#f97316"),
        (360, "#ef4444"),
    ),
    "pressure": _stops(
        (960,  "#7c3aed"),
        (990,  "#a78bfa"),
        (1013, "#f1f5f9"),
        (1025, "#fb923c"),
        (1040, "#92400e"),
    ),
    "wave_height": _stops(
        (0,   "#f8fafc"),
        (1,   "#bae6fd"),
        (3,   "#38bdf8"),
        (6,   "#0284c7"),
        (9,   "#1e3a8a"),
        (12,  "#0f0a1e"),
    ),
    "precip": _stops(
        (0,   "#f8fafc"),
        (1,   "#dbeafe"),
        (3,   "#93c5fd"),
        (7,   "#3b82f6"),
        (12,  "#1d4ed8"),
        (20,  "#0f172a"),
    ),
    "temp": _stops(
        (-40, "#0f172a"),
        (-20, "#3b82f6"),
        (-5,  "#67e8f9"),
        (10,  "#4ade80"),
        (20,  "#facc15"),
        (30,  "#f97316"),
        (40,  "#dc2626"),
        (45,  "#7f1d1d"),
    ),
}

# Clamp ranges — values outside these are pinned to the end colors
SCALE_RANGES: dict[str, tuple[float, float]] = {
    "wind_speed":   (0,    64),
    "wind_dir":     (0,   360),
    "pressure":     (960, 1040),
    "wave_height":  (0,    12),
    "precip":       (0,    20),
    "temp":         (-40,  45),
}


def value_to_rgba(variable: str, value: float, alpha: int = 230) -> tuple[int, int, int, int]:
    """
    Map a scalar data value to an RGBA tuple for the given variable.

    Args:
        variable: ERA5 variable ID (must be a key in SCALES)
        value:    Data value in the variable's native unit
        alpha:    Output alpha channel (0–255). Default 230 ≈ 90% opacity.

    Returns:
        (R, G, B, A) tuple each in 0–255 range.

    The function linearly interpolates between the two nearest ColorStop
    entries. NaN values are returned as fully transparent (0, 0, 0, 0).
    """
    import math

    if math.isnan(value):
        return (0, 0, 0, 0)

    stops = SCALES[variable]
    lo, hi = SCALE_RANGES[variable]

    # Clamp
    value = max(lo, min(hi, value))

    # Find surrounding stops
    if value <= stops[0].value:
        s = stops[0]
        return (s.r, s.g, s.b, alpha)
    if value >= stops[-1].value:
        s = stops[-1]
        return (s.r, s.g, s.b, alpha)

    for i in range(len(stops) - 1):
        s0, s1 = stops[i], stops[i + 1]
        if s0.value <= value <= s1.value:
            t = (value - s0.value) / (s1.value - s0.value)
            r = round(s0.r + t * (s1.r - s0.r))
            g = round(s0.g + t * (s1.g - s0.g))
            b = round(s0.b + t * (s1.b - s0.b))
            return (r, g, b, alpha)

    # Shouldn't reach here
    return (0, 0, 0, 0)
