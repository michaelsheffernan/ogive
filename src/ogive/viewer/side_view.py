"""A to-scale side view of a design: one scene, drawn as SVG and as PNG.

Every shape comes from OpenRocket's model (ogive.sim.openrocket.side_view): outlines, positions,
masses, CG and CP. Nothing is estimated here; this module only lays out and draws. Components the
user marks as assumed (by name, or "ASSUMED" in the component's comment) are hatched and tagged.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from functools import lru_cache
from html import escape

from ogive.sim.openrocket import SideView

WIDTH, HEIGHT = 1600, 640
MARGIN = 80
AXIS_Y = 300  # px, the rocket's centreline

INK, PAPER, MUTED = "#14181f", "#f4f1e8", "#5b6472"
BODY_FILL, FIN_FILL, INTERNAL_FILL = "#e3dfd2", "#d3cdbc", "#ffffff"
MOTOR_FILL, ASSUMED_INK = "#f5c46b", "#b26a00"
CG_INK, CP_INK = "#1f5fbf", "#c0392b"
FONT = "Segoe UI, Arial, Helvetica, sans-serif"


@dataclass(frozen=True)
class Shape:
    """One drawing primitive, in pixels. kind: polygon, line, circle or text."""

    kind: str
    points: tuple[tuple[float, float], ...] = ()
    fill: str | None = None
    stroke: str | None = INK
    width: float = 1.5
    dash: tuple[float, ...] = ()
    hatch: bool = False
    radius: float = 0.0
    text: str = ""
    size: float = 14.0
    anchor: str = "start"  # start, middle or end
    bold: bool = False
    opacity: float = 1.0


@dataclass
class Scene:
    width: int
    height: int
    scale: float  # px per metre, the same on both axes (to scale)
    shapes: list[Shape] = field(default_factory=list)
    assumed: list[str] = field(default_factory=list)  # what was drawn as ASSUMED, for the caller

    def add(self, shape: Shape) -> None:
        self.shapes.append(shape)


def build(
    view: SideView, title: str, sha256: str, version: str, assumed: set[str] | None = None
) -> Scene:
    """Lay out the drawing. assumed: component names, or "Component.field" entries."""
    flagged = _assumptions(view, assumed or set())
    scale = (WIDTH - 2 * MARGIN) / view.length
    scene = Scene(WIDTH, HEIGHT, scale)

    def px(x: float, y: float) -> tuple[float, float]:
        return MARGIN + x * scale, AXIS_Y - y * scale

    scene.add(Shape("polygon", ((0, 0), (WIDTH, 0), (WIDTH, HEIGHT), (0, HEIGHT)), PAPER, None))
    scene.add(Shape("text", ((MARGIN, 44),), fill=INK, text=title, size=24, bold=True))
    scene.add(
        Shape(
            "text",
            ((MARGIN, 72),),
            fill=MUTED,
            text=f"Side view, to scale: {1000 / scale:.2f} mm per pixel. "
            "Drawn from OpenRocket's model of the file.",
            size=14,
        )
    )
    _scale_bar(scene, scale)

    # Centreline first, so every part draws over it.
    left, right = px(0, 0), px(view.length, 0)
    scene.add(
        Shape(
            "line",
            ((left[0] - 20, AXIS_Y), (right[0] + 20, AXIS_Y)),
            None,
            MUTED,
            1,
            dash=(18, 5, 3, 5),
        )
    )
    order = {"body": 0, "fin": 1, "motor": 2, "internal": 3}
    for outline in sorted(view.outlines, key=lambda o: order[o.role]):
        is_assumed = outline.name in flagged
        fill = {"body": BODY_FILL, "fin": FIN_FILL, "motor": MOTOR_FILL}.get(
            outline.role, INTERNAL_FILL
        )
        scene.add(
            Shape(
                "polygon",
                tuple(px(x, y) for x, y in outline.points),
                fill,
                ASSUMED_INK if is_assumed else INK,
                1.4 if outline.role in ("internal", "motor") else 1.8,
                dash=(5, 3) if outline.role == "internal" else (),
                hatch=is_assumed,
                opacity=0.55 if outline.role == "internal" else 1.0,
            )
        )
    _labels(scene, view, px, flagged)
    _cg_cp(scene, view, px)
    _dimensions(scene, view, px)
    _footer(scene, view, title, sha256, version, flagged)
    scene.assumed = sorted(flagged)
    return scene


def _assumptions(view: SideView, assumed: set[str]) -> dict[str, list[str]]:
    """Component name -> assumed fields ([] = the whole component)."""
    flagged: dict[str, list[str]] = {}
    names = {o.name for o in view.outlines}
    for entry in assumed:
        name, _, field_name = entry.partition(".")
        if name not in names:
            raise ValueError(
                f"no component named {name!r} to mark as assumed; known: {sorted(names)}"
            )
        flagged.setdefault(name, [])
        if field_name:
            flagged[name].append(field_name)
    for outline in view.outlines:
        if "assumed" in outline.comment.lower():
            flagged.setdefault(outline.name, [])
    return flagged


def _scale_bar(scene: Scene, scale: float) -> None:
    """A 100 mm bar, so the drawing can be measured."""
    x0, y = WIDTH - MARGIN - 0.1 * scale, 64
    x1 = WIDTH - MARGIN
    scene.add(Shape("line", ((x0, y), (x1, y)), stroke=INK, width=2))
    for x in (x0, x1):
        scene.add(Shape("line", ((x, y - 6), (x, y + 6)), stroke=INK, width=2))
    scene.add(
        Shape("text", (((x0 + x1) / 2, y - 12),), fill=INK, text="100 mm", size=13, anchor="middle")
    )


def _labels(scene: Scene, view: SideView, px, flagged: dict[str, list[str]]) -> None:
    """One label per component, in rows above (body, fins) and below (internals, motor)."""
    seen, above, below = set(), [], []
    for o in view.outlines:
        if o.name in seen and o.role != "motor":
            continue
        seen.add(o.name)
        xs = [x for x, _ in o.points]
        ys = [y for _, y in o.points]
        cx = (min(xs) + max(xs)) / 2
        text = f"{o.name}  {o.mass * 1000:.1f} g"
        if o.name in flagged:
            fields = flagged[o.name]
            text += f"  ASSUMED{' (' + ', '.join(fields) + ')' if fields else ''}"
        entry = (cx, max(ys) if o.role in ("body", "fin") else min(ys), text, o.name in flagged)
        (above if o.role in ("body", "fin") else below).append(entry)

    for entries, direction, base in ((above, -1, 168), (below, 1, 420)):
        rows: list[float] = []  # right edge of the last label in each row
        for cx, edge_y, text, flagged_label in sorted(entries):
            width = _text_width(text, 13)
            # Keep the label on the canvas; its leader line still points at the part.
            label_x = min(max(px(cx, 0)[0], 16 + width / 2), WIDTH - 16 - width / 2)
            x0 = label_x - width / 2
            row = next((i for i, r in enumerate(rows) if x0 > r + 14), len(rows))
            if row == len(rows):
                rows.append(0.0)
            rows[row] = x0 + width
            y = base + direction * row * 22
            colour = ASSUMED_INK if flagged_label else INK
            target = px(cx, edge_y)
            scene.add(
                Shape(
                    "line",
                    ((target[0], target[1]), (target[0], y - 4 * direction)),
                    stroke=MUTED,
                    width=0.8,
                    opacity=0.8,
                )
            )
            scene.add(
                Shape(
                    "text",
                    ((label_x, y + (4 if direction > 0 else 0)),),
                    fill=colour,
                    text=text,
                    size=13,
                    anchor="middle",
                    bold=flagged_label,
                )
            )


def _cg_cp(scene: Scene, view: SideView, px) -> None:
    s = view.static
    # Markers on the axis; their values are spelled out in the summary line below the drawing.
    for x, colour, filled in (
        (s.cg_x, CG_INK, True),
        (s.cg_burnout_x, CG_INK, False),
        (s.cp_x, CP_INK, True),
    ):
        cx, cy = px(x, 0)
        scene.add(
            Shape(
                "circle",
                ((cx, cy),),
                "#ffffff" if not filled else colour,
                colour,
                2,
                radius=7,
                dash=(3, 2) if not filled else (),
            )
        )
        if filled and colour == CG_INK:  # OpenRocket-style CG mark: a cross in the circle
            scene.add(Shape("line", ((cx - 7, cy), (cx + 7, cy)), stroke="#ffffff", width=1.5))
            scene.add(Shape("line", ((cx, cy - 7), (cx, cy + 7)), stroke="#ffffff", width=1.5))
    y = 520
    cg_text = (
        f"CG {s.cg_x * 1000:.0f} mm (burnout {s.cg_burnout_x * 1000:.0f} mm)   "
        f"CP {s.cp_x * 1000:.0f} mm at M{s.mach:.2f}   "
        f"Stability {s.calibers:.2f} cal (burnout {s.burnout_calibers:.2f} cal)"
    )
    scene.add(Shape("text", ((MARGIN, y),), fill=INK, text=cg_text, size=15, bold=True))


def _dimensions(scene: Scene, view: SideView, px) -> None:
    """Overall length below the rocket; diameter at the widest body point; fin numbers."""
    s = view.static
    y = 474
    x0, x1 = px(0, 0)[0], px(view.length, 0)[0]
    scene.add(Shape("line", ((x0, y), (x1, y)), stroke=INK, width=1))
    for x in (x0, x1):
        scene.add(Shape("line", ((x, y - 7), (x, y + 7)), stroke=INK, width=1))
    scene.add(
        Shape(
            "text",
            (((x0 + x1) / 2, y + 20),),
            fill=INK,
            text=f"{view.length * 1000:.1f} mm overall,  {s.max_diameter * 1000:.1f} mm diameter",
            size=13,
            anchor="middle",
        )
    )
    masses = (
        f"Launch {s.mass_with_motors * 1000:.1f} g   Dry {s.mass_without_motors * 1000:.1f} g   "
        f"Motor {', '.join(view.motors) or 'none'}"
    )
    fins = "   ".join(
        f"{f.name}: {f.count} fins, root {f.root_chord * 1000:.1f}, tip {f.tip_chord * 1000:.1f}, "
        f"span {f.height * 1000:.1f}, sweep {f.sweep * 1000:.1f} mm"
        for f in view.fins
    )
    scene.add(
        Shape(
            "text",
            ((MARGIN, 546),),
            fill=INK,
            text=masses + ("   " + fins if fins else ""),
            size=14,
        )
    )


def _footer(
    scene: Scene,
    view: SideView,
    title: str,
    sha256: str,
    version: str,
    flagged: dict[str, list[str]],
) -> None:
    note = (
        f"{title}  sha256 {sha256[:12]}  {view.backend}  Ogive {version}.  "
        "Shapes, positions, masses, CG and CP as OpenRocket computes them. Not a CAD drawing."
    )
    scene.add(Shape("text", ((MARGIN, 590),), fill=MUTED, text=note, size=12))
    extra = []
    if flagged:
        extra.append("Hatched, orange: ASSUMED values (not measured). Check before trusting.")
    if view.not_drawn:
        extra.append("Not drawn: " + ", ".join(view.not_drawn) + ".")
    if extra:
        scene.add(
            Shape(
                "text",
                ((MARGIN, 612),),
                fill=ASSUMED_INK if flagged else MUTED,
                text="  ".join(extra),
                size=12,
                bold=bool(flagged),
            )
        )


# --- SVG -------------------------------------------------------------------------------------


def to_svg(scene: Scene) -> str:
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {scene.width} {scene.height}" '
        f'width="{scene.width}" height="{scene.height}" font-family="{FONT}">',
        '<defs><pattern id="assumed" width="8" height="8" patternUnits="userSpaceOnUse" '
        'patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="8" '
        f'stroke="{ASSUMED_INK}" stroke-width="2.5"/></pattern></defs>',
    ]
    for s in scene.shapes:
        common = _svg_stroke(s)
        if s.kind == "polygon":
            pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in s.points)
            fill = s.fill or "none"
            out.append(
                f'<polygon points="{pts}" fill="{fill}" fill-opacity="{s.opacity}"{common}/>'
            )
            if s.hatch:
                out.append(f'<polygon points="{pts}" fill="url(#assumed)" stroke="none"/>')
        elif s.kind == "line":
            pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in s.points)
            out.append(f'<polyline points="{pts}" fill="none" opacity="{s.opacity}"{common}/>')
        elif s.kind == "circle":
            ((cx, cy),) = s.points
            out.append(
                f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{s.radius}" '
                f'fill="{s.fill or "none"}"{common}/>'
            )
        elif s.kind == "text":
            ((x, y),) = s.points
            weight = ' font-weight="700"' if s.bold else ""
            out.append(
                f'<text x="{x:.2f}" y="{y:.2f}" font-size="{s.size}" fill="{s.fill}" '
                f'text-anchor="{s.anchor}"{weight}>{escape(s.text)}</text>'
            )
    out.append("</svg>")
    return "\n".join(out) + "\n"


def _svg_stroke(s: Shape) -> str:
    if not s.stroke:
        return ' stroke="none"'
    dash = f' stroke-dasharray="{" ".join(f"{d:g}" for d in s.dash)}"' if s.dash else ""
    return f' stroke="{s.stroke}" stroke-width="{s.width}" stroke-linejoin="round"{dash}'


# --- PNG (Pillow) ------------------------------------------------------------------------------

SUPERSAMPLE = 2


def to_png(scene: Scene, palette: int | None = None) -> bytes:
    """PNG bytes. palette: reduce to that many colours (smaller file); None keeps full colour.

    Full colour is the default: a reduced palette can drop small, meaningful colours (it turned
    the blue CG and red CP markers grey and brown at 128 colours).
    """
    from PIL import Image

    image = render_rgb(scene)
    if palette:
        image = image.quantize(colors=palette, method=Image.Quantize.FASTOCTREE)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def render_rgb(scene: Scene):
    """Rasterise the scene with Pillow (2x supersampled, then downscaled for smooth edges)."""
    from PIL import Image, ImageDraw

    k = SUPERSAMPLE
    image = Image.new("RGBA", (scene.width * k, scene.height * k), PAPER)
    for s in scene.shapes:
        pts = [(x * k, y * k) for x, y in s.points]
        if s.kind == "polygon":
            layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(layer)
            if s.fill:
                draw.polygon(pts, fill=_rgba(s.fill, s.opacity))
            if s.hatch:
                _hatch(layer, pts, k)
            if s.stroke:
                _polyline(draw, pts + pts[:1], s, k)
            image = Image.alpha_composite(image, layer)
        else:
            draw = ImageDraw.Draw(image)
            if s.kind == "line":
                _polyline(draw, pts, s, k)
            elif s.kind == "circle":
                (cx, cy), r = pts[0], s.radius * k
                box = (cx - r, cy - r, cx + r, cy + r)
                draw.ellipse(
                    box,
                    fill=_rgba(s.fill) if s.fill else None,
                    outline=_rgba(s.stroke) if s.stroke else None,
                    width=round(s.width * k),
                )
            elif s.kind == "text":
                font = _font(round(s.size * k), s.bold)
                ((x, y),) = pts
                anchor = {"start": "ls", "middle": "ms", "end": "rs"}[s.anchor]
                draw.text((x, y), s.text, fill=_rgba(s.fill), font=font, anchor=anchor)
    return image.resize((scene.width, scene.height), Image.Resampling.LANCZOS).convert("RGB")


def _polyline(draw, pts, s: Shape, k: int) -> None:
    colour, width = _rgba(s.stroke, s.opacity), max(1, round(s.width * k))
    if not s.dash:
        draw.line(pts, fill=colour, width=width, joint="curve")
        return
    pattern = [d * k for d in s.dash]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:], strict=False):
        length = math.hypot(x1 - x0, y1 - y0)
        pos, i = 0.0, 0
        while pos < length:
            seg = min(pattern[i % len(pattern)], length - pos)
            if i % 2 == 0:
                a, b = pos / length, (pos + seg) / length
                draw.line(
                    [
                        (x0 + (x1 - x0) * a, y0 + (y1 - y0) * a),
                        (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b),
                    ],
                    fill=colour,
                    width=width,
                )
            pos += seg
            i += 1


def _hatch(layer, pts, k: int) -> None:
    """Diagonal hatch clipped to the polygon, via a mask."""
    from PIL import Image, ImageDraw

    mask = Image.new("L", layer.size, 0)
    ImageDraw.Draw(mask).polygon(pts, fill=255)
    lines = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(lines)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    step = 8 * k
    for offset in range(int(min(xs) - max(ys)), int(max(xs) - min(ys)) + step, step):
        draw.line(
            [(offset + min(ys), min(ys)), (offset + max(ys), max(ys))],
            fill=_rgba(ASSUMED_INK),
            width=round(1.5 * k),
        )
    layer.paste(lines, (0, 0), Image.composite(lines, Image.new("RGBA", layer.size), mask))


def _rgba(colour: str | None, opacity: float = 1.0) -> tuple[int, int, int, int]:
    colour = (colour or "#000000").lstrip("#")
    r, g, b = (int(colour[i : i + 2], 16) for i in (0, 2, 4))
    return r, g, b, round(255 * opacity)


@lru_cache(maxsize=16)
def _font(size: int, bold: bool):
    from PIL import ImageFont

    for name in ("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _text_width(text: str, size: float) -> float:
    return _font(round(size), False).getlength(text)
