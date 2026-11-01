"""A to-scale map of a landing dispersion, seen from above: pad at the centre, north up.

Range rings at a round step, the field radius if given, the 95 % ellipse, every landing (the
farthest labelled), the mean landing, a north arrow and the wind (arrow points where it blows).
Pillow, drawn 2x and downscaled for smooth edges. Same palette and fonts as the side view.
"""

from __future__ import annotations

import io
import math

from ogive.sim.dispersion import Dispersion
from ogive.viewer.side_view import CG_INK, CP_INK, INK, MUTED, PAPER, _font

WIDTH, HEIGHT, HEADER = 720, 790, 70
SS = 2  # supersampling
STEPS = (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000)
RING, FIELD_INK, DOT_FILL = "#d8d3c4", "#b26a00", "#14181f"


def ring_step(extent: float) -> float:
    """The smallest round step that needs at most 5 rings to reach `extent` metres."""
    return next((s for s in STEPS if extent / s <= 5), STEPS[-1])


def to_png(d: Dispersion) -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (WIDTH * SS, HEIGHT * SS), PAPER)
    draw = ImageDraw.Draw(image)
    e = d.ellipse
    extent = max(
        d.farthest.distance,
        math.hypot(e.east, e.north) + e.semi_major,
        d.field_radius or 0.0,
        10.0,
    )
    step = ring_step(extent * 1.1)
    radius_m = step * math.ceil(extent * 1.1 / step)
    cx, cy = WIDTH / 2, HEADER + (HEIGHT - HEADER) / 2
    scale = (min(WIDTH, HEIGHT - HEADER) / 2 - 34) / radius_m  # px per metre

    def xy(east: float, north: float) -> tuple[float, float]:
        return (cx + east * scale) * SS, (cy - north * scale) * SS

    def circle(east, north, r_px, outline=None, fill=None, width=1):
        x, y = xy(east, north)
        r = r_px * SS
        draw.ellipse((x - r, y - r, x + r, y + r), outline=outline, fill=fill, width=width * SS)

    def text(x, y, s, size=13, bold=False, fill=INK, anchor="la"):
        draw.text((x * SS, y * SS), s, font=_font(size * SS, bold), fill=fill, anchor=anchor)

    # Range rings, labelled on the north-east diagonal.
    r = step
    while r <= radius_m + 1e-9:
        circle(0, 0, r * scale, outline=RING, width=1)
        lx, ly = xy(r / math.sqrt(2), r / math.sqrt(2))
        text(lx / SS + 3, ly / SS - 3, f"{r:g} m", size=11, fill=MUTED, anchor="ld")
        r += step
    if d.field_radius:
        circle(0, 0, d.field_radius * scale, outline=FIELD_INK, width=2)
        fx, fy = xy(0, -d.field_radius)
        text(
            fx / SS,
            fy / SS + 4,
            f"field, {d.field_radius:g} m radius: {d.outside} of {len(d.landings)} land outside",
            size=11,
            fill=FIELD_INK,
            anchor="ma",
        )

    # 95 % ellipse, as a polygon: parametric in (major, minor), rotated to its bearing.
    b = math.radians(e.major_bearing)
    ux, uy = math.sin(b), math.cos(b)  # unit vector along the long axis (east, north)
    vx, vy = uy, -ux
    outline = [
        xy(
            e.east + e.semi_major * math.cos(t) * ux + e.semi_minor * math.sin(t) * vx,
            e.north + e.semi_major * math.cos(t) * uy + e.semi_minor * math.sin(t) * vy,
        )
        for t in (2 * math.pi * k / 180 for k in range(180))
    ]
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).polygon(outline, fill=(31, 95, 191, 38))
    image.paste(overlay, (0, 0), overlay)
    draw.polygon(outline, outline=CG_INK, width=2 * SS)

    # Pad, landings, mean.
    px, py = xy(0, 0)
    draw.regular_polygon((px, py, 7 * SS), 3, fill=INK)
    text(px / SS + 9, py / SS + 2, "pad", size=11, fill=MUTED)
    far = d.farthest
    for landing in d.landings:
        circle(landing.east, landing.north, 3.2, fill=DOT_FILL)
    circle(far.east, far.north, 6, outline=CP_INK, width=2)
    fx, fy = xy(far.east, far.north)
    text(fx / SS + 9, fy / SS, f"farthest {far.distance:.0f} m", size=12, bold=True, fill=CP_INK)
    mx, my = xy(e.east, e.north)
    arm = 7 * SS
    draw.line((mx - arm, my, mx + arm, my), fill=CG_INK, width=2 * SS)
    draw.line((mx, my - arm, mx, my + arm), fill=CG_INK, width=2 * SS)

    # North arrow (top right) and wind arrow (top left, pointing where the wind blows).
    _arrow(draw, (WIDTH - 40) * SS, (HEADER + 52) * SS, 0.0, 30 * SS, INK)
    text(WIDTH - 40, HEADER + 10, "N", size=14, bold=True, anchor="ma")
    _arrow(draw, 52 * SS, (HEADER + 46) * SS, (d.wind_from + 180) % 360, 34 * SS, MUTED)
    text(24, HEADER + 84, f"wind from {d.wind_from:.0f}°", size=11, fill=MUTED)

    # Header and footer.
    runs = len(d.landings)
    text(20, 16, f"Landing dispersion: {d.simulation}, {runs} runs", size=17, bold=True)
    text(
        20,
        42,
        f"Wind {d.wind_average:.1f} ± {d.speed_sd:g} m/s from {d.wind_from:.0f}° ± "
        f"{d.direction_sd:g}°.  95 % ellipse {e.semi_major:.0f} × {e.semi_minor:.0f} m, "
        f"mean {math.hypot(e.east, e.north):.0f} m at {_bearing(e.east, e.north):.0f}°.",
        size=13,
        fill=MUTED,
    )
    text(
        WIDTH - 12,
        HEIGHT - 10,
        f"{d.backend} flights. The ellipse assumes normal scatter; few runs = rough.",
        size=11,
        fill=MUTED,
        anchor="rd",
    )
    image = image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _bearing(east: float, north: float) -> float:
    return math.degrees(math.atan2(east, north)) % 360.0


def _arrow(draw, x: float, y: float, bearing_deg: float, length: float, fill: str) -> None:
    """An arrow centred on (x, y) pointing along a compass bearing (screen y grows downward)."""
    b = math.radians(bearing_deg)
    dx, dy = math.sin(b), -math.cos(b)
    tail = (x - dx * length / 2, y - dy * length / 2)
    tip = (x + dx * length / 2, y + dy * length / 2)
    draw.line((*tail, *tip), fill=fill, width=int(length / 12))
    head = length / 3
    left = (tip[0] - head * (dx * 0.9 - dy * 0.5), tip[1] - head * (dy * 0.9 + dx * 0.5))
    right = (tip[0] - head * (dx * 0.9 + dy * 0.5), tip[1] - head * (dy * 0.9 - dx * 0.5))
    draw.polygon([tip, left, right], fill=fill)
