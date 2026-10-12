"""Optimizer output: a CSV of every variant and an SVG plot of the Pareto front.

The SVG is hand-written (no plotting dependency). Hover a point for its numbers.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Sequence
from html import escape
from pathlib import Path

from ogive.opt.sweep import Row

CSV_COLUMNS = [
    "fin_height_mm",
    "nose_weight_g",
    "verdict",
    "meets_goals",
    "pareto",
    "launch_mass_g",
    "apogee_m",
    "max_mach",
    "worst_ascent_stability_cal",
    "static_stability_cal",
    "flutter_margin",
    "rod_velocity_ms",
    "ground_hit_ms",
    "failed_rules",
    "backend",
    "seed",
    "file",
]

NOSE_COLOURS = ["#9ecae1", "#6baed6", "#4292c6", "#2171b5", "#08519c", "#08306b", "#041a3a"]
FAIL_COLOUR = "#a0a0a0"
WIDTH, HEIGHT = 800, 500
LEFT, RIGHT, TOP, BOTTOM = 70, 230, 70, 60


def write_csv(rows: Sequence[Row], front: Sequence[Row], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    on_front = {id(r) for r in front}
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for r in rows:
            writer.writerow(
                [
                    f"{r.variant.fin_height * 1000:g}",
                    f"{r.variant.nose_mass * 1000:g}",
                    "PASS" if r.passed else "FAIL",
                    "yes" if r.meets_goals else "no",
                    "yes" if id(r) in on_front else "no",
                    f"{r.metric('launch_mass') * 1000:.1f}",
                    f"{r.metric('apogee'):.1f}",
                    f"{r.metric('max_mach'):.3f}",
                    _fmt(r.measured("stability.ascent"), ".3f"),
                    _fmt(r.measured("stability.static"), ".3f"),
                    _fmt(r.measured("flutter.margin"), ".3f"),
                    _fmt(r.measured("launch.rod_velocity"), ".2f"),
                    _fmt(r.measured("recovery.ground_hit"), ".2f"),
                    " ".join(r.failures),
                    r.report.backend,
                    r.report.seed,
                    r.file.as_posix(),
                ]
            )
    return path


def pareto_svg(
    rows: Sequence[Row],
    front: Sequence[Row],
    marks: dict[str, Row],
    min_cal: float,
    goal_cal: float,
    subtitle: str,
) -> str:
    """Max Mach against worst ascent stability. Filled = passes the judge, x = fails a rule."""
    plotted = [r for r in rows if r.measured("stability.ascent") is not None]
    if not plotted:
        raise ValueError("nothing to plot: no variant has an ascent stability value")
    xs = [r.measured("stability.ascent") for r in plotted] + [min_cal, goal_cal]
    ys = [r.metric("max_mach") for r in plotted]
    x_lo, x_hi = _padded(min(xs), max(xs))
    y_lo, y_hi = _padded(min(ys), max(ys))
    plot_w, plot_h = WIDTH - LEFT - RIGHT, HEIGHT - TOP - BOTTOM

    def px(x: float) -> float:
        return LEFT + (x - x_lo) / (x_hi - x_lo) * plot_w

    def py(y: float) -> float:
        return TOP + (y_hi - y) / (y_hi - y_lo) * plot_h

    masses = sorted({r.variant.nose_mass for r in rows})
    colour = {m: NOSE_COLOURS[min(i, len(NOSE_COLOURS) - 1)] for i, m in enumerate(masses)}
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'width="{WIDTH}" height="{HEIGHT}" font-family="Segoe UI, Helvetica, Arial, sans-serif">',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="#ffffff"/>',
        f'<text x="{LEFT}" y="30" font-size="17" font-weight="600" fill="#1a1a1a">'
        "Max Mach against worst stability in the ascent</text>",
        f'<text x="{LEFT}" y="50" font-size="12" fill="#555">{escape(subtitle)}</text>',
    ]
    for x in _ticks(x_lo, x_hi):
        out.append(_line(px(x), TOP, px(x), TOP + plot_h, "#eeeeee"))
        out.append(_text(px(x), TOP + plot_h + 18, f"{x:g}", anchor="middle"))
    for y in _ticks(y_lo, y_hi):
        out.append(_line(LEFT, py(y), LEFT + plot_w, py(y), "#eeeeee"))
        out.append(_text(LEFT - 8, py(y) + 4, f"{y:g}", anchor="end"))
    out.append(
        f'<rect x="{LEFT}" y="{TOP}" width="{plot_w}" height="{plot_h}" fill="none" '
        'stroke="#bbbbbb"/>'
    )
    out.append(
        _text(
            LEFT + plot_w / 2,
            HEIGHT - 16,
            "worst stability, rod clearance to ejection (cal)",
            anchor="middle",
            size=12,
        )
    )
    out.append(
        f'<text transform="translate(20 {TOP + plot_h / 2}) rotate(-90)" text-anchor="middle" '
        'font-size="12" fill="#333">max Mach</text>'
    )
    # Both labels sit right of their lines, at different heights, so they never collide.
    for value, name, stroke, rise in (
        (min_cal, "minimum", "#c0392b", 8),
        (goal_cal, "goal", "#d68910", 24),
    ):
        out.append(_line(px(value), TOP, px(value), TOP + plot_h, stroke, dash="5 4"))
        out.append(
            _text(px(value) + 4, TOP + plot_h - rise, f"{name} {value:g}", size=11, fill=stroke)
        )

    ordered = sorted(front, key=lambda r: r.measured("stability.ascent"))
    if len(ordered) > 1:
        points = " ".join(
            f"{px(r.measured('stability.ascent')):.1f},{py(r.metric('max_mach')):.1f}"
            for r in ordered
        )
        out.append(f'<polyline points="{points}" fill="none" stroke="#1a1a1a" stroke-width="1.5"/>')

    for r in plotted:
        x, y = px(r.measured("stability.ascent")), py(r.metric("max_mach"))
        tip = f"<title>{escape(_tooltip(r))}</title>"
        if r.passed:
            out.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{colour[r.variant.nose_mass]}" '
                f'stroke="#ffffff" stroke-width="0.8">{tip}</circle>'
            )
        else:
            out.append(
                f'<path d="M{x - 4:.1f},{y - 4:.1f} L{x + 4:.1f},{y + 4:.1f} '
                f'M{x - 4:.1f},{y + 4:.1f} L{x + 4:.1f},{y - 4:.1f}" stroke="{FAIL_COLOUR}" '
                f'stroke-width="1.6">{tip}</path>'
            )

    # Marked designs get a ring and a letter; the legend says what each letter is.
    labels: dict[int, tuple[Row, list[str]]] = {}
    for name, r in marks.items():
        labels.setdefault(id(r), (r, []))[1].append(name)
    lettered = [
        (chr(ord("A") + i), r, names)
        for i, (r, names) in enumerate(
            (r, n) for r, n in labels.values() if r.measured("stability.ascent") is not None
        )
    ]
    for letter, r, _ in lettered:
        x, y = px(r.measured("stability.ascent")), py(r.metric("max_mach"))
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="9" fill="none" stroke="#1a1a1a"/>')
        out.append(_text(x + 8, y - 10, letter, size=12, fill="#1a1a1a", weight="700"))

    lx, ly = WIDTH - RIGHT + 24, TOP + 8
    for letter, r, names in lettered:
        out.append(_text(lx, ly, letter, size=12, fill="#1a1a1a", weight="700"))
        for line in names:
            out.append(_text(lx + 16, ly, line, size=11, fill="#1a1a1a"))
            ly += 15
        out.append(_text(lx + 16, ly, _short(r), size=11))
        ly += 24
    ly += 4
    out.append(_text(lx, ly, "nose weight", size=12, fill="#1a1a1a"))
    for i, m in enumerate(masses):
        cy = ly + 18 + i * 18
        out.append(f'<circle cx="{lx + 6}" cy="{cy - 4}" r="4.5" fill="{colour[m]}"/>')
        out.append(_text(lx + 18, cy, f"{m * 1000:g} g", size=11))
    ly += 18 * (len(masses) + 2)
    out.append(
        f'<path d="M{lx + 2},{ly - 8} L{lx + 10},{ly} M{lx + 2},{ly} L{lx + 10},{ly - 8}" '
        f'stroke="{FAIL_COLOUR}" stroke-width="1.6"/>'
    )
    out.append(_text(lx + 18, ly, "fails a judge rule", size=11))
    out.append(_line(lx, ly + 16, lx + 12, ly + 16, "#1a1a1a"))
    out.append(_text(lx + 18, ly + 20, "Pareto front", size=11))
    out.append(_text(lx, ly + 44, "Advisory only.", size=11, fill="#555"))
    out.append(_text(lx, ly + 59, "OpenRocket and RASAero II", size=11, fill="#555"))
    out.append(_text(lx, ly + 74, "sign off on real flights.", size=11, fill="#555"))
    out.append("</svg>")
    return "\n".join(out) + "\n"


def _short(r: Row) -> str:
    return f"{r.variant.fin_height * 1000:g} mm fins, {r.variant.nose_mass * 1000:g} g nose"


def _tooltip(r: Row) -> str:
    stab = r.measured("stability.ascent")
    head = (
        f"{_short(r)}: M{r.metric('max_mach'):.3f}, "
        f"{'—' if stab is None else f'{stab:.2f}'} cal, "
        f"{r.metric('launch_mass') * 1000:.0f} g, apogee {r.metric('apogee'):.0f} m"
    )
    return head + (f". FAIL: {', '.join(r.failures)}" if r.failures else ". PASS")


def _padded(lo: float, hi: float) -> tuple[float, float]:
    pad = (hi - lo) * 0.08 or abs(hi) * 0.05 or 1.0
    return lo - pad, hi + pad


def _ticks(lo: float, hi: float, target: int = 6) -> list[float]:
    raw = (hi - lo) / target
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(s * magnitude for s in (1, 2, 2.5, 5, 10) if s * magnitude >= raw)
    first = math.ceil(lo / step) * step
    count = math.floor((hi - first) / step + 1e-9)
    return [round(first + i * step, 10) for i in range(count + 1)]


def _line(x1: float, y1: float, x2: float, y2: float, stroke: str, dash: str = "") -> str:
    dashed = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}"{dashed}/>'
    )


def _text(
    x: float,
    y: float,
    text: str,
    anchor: str = "start",
    size: int = 11,
    fill: str = "#333",
    weight: str = "400",
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}">{escape(text)}</text>'
    )


def _fmt(value: float | None, spec: str) -> str:
    return "" if value is None else format(value, spec)
