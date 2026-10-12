"""Draw the Ogive logo from real tangent-ogive geometry. Writes assets/*.svg and favicon.ico.

A tangent ogive of length L and base radius R is two circular arcs. Each arc has radius
    rho = (R^2 + L^2) / (2 R)
and its centre sits on the base line, rho - R across the axis from it. The arc passes through
the tip and meets the base with a vertical tangent, so the nose flows into the body (the
"tangent" in the name). SVG draws exact circular arcs, so nothing here is approximated.

    .venv\\Scripts\\python.exe scripts\\make_logo.py

Needs Pillow and Google Chrome (headless) for favicon.ico and the previews; the SVGs need neither.
"""

from __future__ import annotations

import io
import math
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ASSETS = REPO / "assets"
PREVIEW = REPO / "out" / "brand"

NAVY, PAPER, AMBER = "#14181f", "#f2efe6", "#f5b041"
LENGTH_RATIO = 2.5  # L / R, the concept's proportions: rho = 3.625 R, tip about 87 degrees
LEG_RATIO = 0.6  # straight body below the base, / R: the ogive stays open, never closed


@dataclass(frozen=True)
class Ogive:
    cx: float  # axis x
    base_y: float  # y where the arcs meet the body
    radius: float  # R
    length: float  # L

    @property
    def rho(self) -> float:
        return (self.radius**2 + self.length**2) / (2 * self.radius)

    @property
    def centre_offset(self) -> float:
        """Distance from the axis to each arc's centre (on the far side of the axis)."""
        return self.rho - self.radius

    @property
    def tip_y(self) -> float:
        return self.base_y - self.length

    def path(self, leg: float) -> str:
        """Left foot, up the left arc to the tip, down the right arc, right foot. Never closed."""
        r, rho = self.radius, self.rho
        left, right, foot = self.cx - r, self.cx + r, self.base_y + leg
        return (
            f"M{left:.3f},{foot:.3f} L{left:.3f},{self.base_y:.3f} "
            f"A{rho:.3f},{rho:.3f} 0 0 1 {self.cx:.3f},{self.tip_y:.3f} "
            f"A{rho:.3f},{rho:.3f} 0 0 1 {right:.3f},{self.base_y:.3f} "
            f"L{right:.3f},{foot:.3f}"
        )


def check(o: Ogive) -> None:
    """Tip and right base point lie on the right arc's circle; the base tangent is vertical."""
    cx, cy = o.cx - o.centre_offset, o.base_y
    for x, y in ((o.cx, o.tip_y), (o.cx + o.radius, o.base_y)):
        assert math.isclose(math.hypot(x - cx, y - cy), o.rho, rel_tol=1e-12)
    # Vertical tangent at the base: the radius to (cx + R, base_y) is horizontal.
    assert math.isclose(o.cx + o.radius - cx, o.rho, rel_tol=1e-12)


def full_logo(ink: str, paper: str, dot: bool) -> str:
    """512 tile: ogive, axis, the right arc's construction circle and its radius to the tip."""
    r = 70.0
    leg = LEG_RATIO * r
    length = LENGTH_RATIO * r
    height = length + leg
    o = Ogive(256.0, 256.0 + height / 2 - leg, r, length)
    check(o)
    ccx, ccy = o.cx - o.centre_offset, o.base_y
    axis_top, axis_bottom = o.tip_y - 0.55 * r, o.base_y + leg + 0.55 * r
    tick = 7.0
    parts = [
        f'<rect width="512" height="512" rx="96" fill="{paper}"/>',
        '<g clip-path="url(#tile)" fill="none">',
        f'  <circle cx="{ccx:.3f}" cy="{ccy:.3f}" r="{o.rho:.3f}" stroke="{ink}" '
        'stroke-opacity="0.32" stroke-width="2" stroke-dasharray="5 7"/>',
        f'  <line x1="{ccx:.3f}" y1="{ccy:.3f}" x2="{o.cx:.3f}" y2="{o.tip_y:.3f}" stroke="{ink}" '
        'stroke-opacity="0.45" stroke-width="2"/>',
        f'  <path d="M{ccx - tick:.3f},{ccy:.3f} H{ccx + tick:.3f} M{ccx:.3f},{ccy - tick:.3f} '
        f'V{ccy + tick:.3f}" stroke="{ink}" stroke-opacity="0.6" stroke-width="2"/>',
        f'  <line x1="{o.cx:.3f}" y1="{axis_top:.3f}" x2="{o.cx:.3f}" y2="{axis_bottom:.3f}" '
        f'stroke="{ink}" stroke-opacity="0.55" stroke-width="2" stroke-dasharray="22 6 4 6"/>',
        f'  <path d="{o.path(leg)}" stroke="{ink}" stroke-width="14" stroke-linecap="round" '
        'stroke-linejoin="round"/>',
        "</g>",
    ]
    if dot:
        parts.append(f'<circle cx="{o.cx:.3f}" cy="{o.tip_y:.3f}" r="9" fill="{AMBER}"/>')
    return svg(512, parts, clip=True)


def mark(ink: str, paper: str) -> str:
    """512 tile: the ogive and its axis only, drawn larger."""
    r = 86.0
    leg = LEG_RATIO * r
    length = LENGTH_RATIO * r
    o = Ogive(256.0, 256.0 + (length + leg) / 2 - leg, r, length)
    check(o)
    axis_top, axis_bottom = o.tip_y - 0.45 * r, o.base_y + leg + 0.45 * r
    parts = [
        f'<rect width="512" height="512" rx="96" fill="{paper}"/>',
        f'<line x1="{o.cx:.3f}" y1="{axis_top:.3f}" x2="{o.cx:.3f}" y2="{axis_bottom:.3f}" '
        f'stroke="{ink}" stroke-opacity="0.55" stroke-width="3" stroke-dasharray="26 7 5 7"/>',
        f'<path d="{o.path(leg)}" fill="none" stroke="{ink}" stroke-width="18" '
        'stroke-linecap="round" stroke-linejoin="round"/>',
    ]
    return svg(512, parts)


def favicon(size_hint: int = 32) -> str:
    """32 tile: open ogive only, thick stroke. size_hint 16 draws a bigger, bolder one for 16 px.

    Leg centres are snapped so the vertical strokes cover whole pixels at the target size.
    32 px: 3-unit stroke centred on x.5 (R = 6.5). 16 px: 4 units = 2 px, on whole pixels (R = 8).
    """
    if size_hint >= 32:
        stroke, r, leg = 3.0, 6.5, 0.8 * 6.5
    else:
        stroke, r, leg = 4.0, 8.0, 3.0
    length = LENGTH_RATIO * r
    o = Ogive(16.0, 16.0 + (length + leg) / 2 - leg, r, length)
    check(o)
    parts = [
        f'<rect width="32" height="32" rx="7" fill="{NAVY}"/>',
        f'<path d="{o.path(leg)}" fill="none" stroke="{PAPER}" stroke-width="{stroke}" '
        'stroke-linecap="round" stroke-linejoin="round"/>',
    ]
    return svg(32, parts)


def svg(size: int, parts: list[str], clip: bool = False) -> str:
    tile = f'<rect width="{size}" height="{size}" rx="96"/>'
    defs = f'<defs><clipPath id="tile">{tile}</clipPath></defs>' if clip else ""
    body = "\n".join(f"  {p}" for p in ([defs] if defs else []) + parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" '
        f'height="{size}" role="img" aria-label="Ogive">\n{body}\n</svg>\n'
    )


def social_preview() -> str:
    """1280 x 640 card for GitHub's link preview: construction drawing, then name and tagline."""
    r = 105.0
    leg = LEG_RATIO * r
    length = LENGTH_RATIO * r
    o = Ogive(380.0, 320.0 + (length + leg) / 2 - leg, r, length)
    check(o)
    ccx, ccy = o.cx - o.centre_offset, o.base_y
    axis_top, axis_bottom = o.tip_y - 0.5 * r, o.base_y + leg + 0.5 * r
    tick = 10.0
    font = "Segoe UI, Helvetica Neue, Helvetica, Arial, sans-serif"
    parts = [
        f'<rect width="1280" height="640" fill="{NAVY}"/>',
        '<g fill="none">',
        f'  <circle cx="{ccx:.3f}" cy="{ccy:.3f}" r="{o.rho:.3f}" stroke="{PAPER}" '
        'stroke-opacity="0.3" stroke-width="2.5" stroke-dasharray="7 9"/>',
        f'  <line x1="{ccx:.3f}" y1="{ccy:.3f}" x2="{o.cx:.3f}" y2="{o.tip_y:.3f}" '
        f'stroke="{PAPER}" stroke-opacity="0.42" stroke-width="2.5"/>',
        f'  <path d="M{ccx - tick:.3f},{ccy:.3f} H{ccx + tick:.3f} M{ccx:.3f},{ccy - tick:.3f} '
        f'V{ccy + tick:.3f}" stroke="{PAPER}" stroke-opacity="0.6" stroke-width="2.5"/>',
        f'  <line x1="{o.cx:.3f}" y1="{axis_top:.3f}" x2="{o.cx:.3f}" y2="{axis_bottom:.3f}" '
        f'stroke="{PAPER}" stroke-opacity="0.55" stroke-width="2.5" stroke-dasharray="30 8 5 8"/>',
        f'  <path d="{o.path(leg)}" stroke="{PAPER}" stroke-width="19" stroke-linecap="round" '
        'stroke-linejoin="round"/>',
        "</g>",
        f'<circle cx="{o.cx:.3f}" cy="{o.tip_y:.3f}" r="12" fill="{AMBER}"/>',
        f'<text x="620" y="292" fill="{PAPER}" font-family="{font}" font-size="132" '
        'font-weight="600" letter-spacing="-2">Ogive</text>',
        f'<text x="624" y="360" fill="{PAPER}" fill-opacity="0.78" font-family="{font}" '
        'font-size="38">AI tools for OpenRocket</text>',
        f'<text x="624" y="408" fill="{PAPER}" fill-opacity="0.78" font-family="{font}" '
        'font-size="38">that show their working.</text>',
        f'<text x="625" y="478" fill="{PAPER}" fill-opacity="0.45" font-family="{font}" '
        'font-size="25">github.com/michaelsheffernan/ogive</text>',
    ]
    body = "\n".join(f"  {p}" for p in parts)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 640" width="1280" '
        f'height="640" role="img" aria-label="Ogive">\n{body}\n</svg>\n'
    )


def render(svg_text: str, px: int, chrome: str, height: int | None = None) -> bytes:
    """Rasterise an SVG with headless Chrome at px x px (or px x height)."""
    height = height or px
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "in.html", Path(tmp) / "out.png"
        src.write_text(
            "<html><body style='margin:0;background:transparent'>"
            f"<img src='data:image/svg+xml;utf8,{quote(svg_text)}' width='{px}' height='{height}'>"
            "</body></html>",
            encoding="utf-8",
        )
        subprocess.run(
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--default-background-color=00000000",
                f"--window-size={px},{height}",
                f"--screenshot={out}",
                src.as_uri(),
            ],
            check=True,
            capture_output=True,
        )
        return out.read_bytes()


def quote(text: str) -> str:
    from urllib.parse import quote as q

    return q(text, safe="")


def find_chrome() -> str | None:
    for candidate in (
        shutil.which("chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def main() -> int:
    ASSETS.mkdir(exist_ok=True)
    files = {
        "logo.svg": full_logo(PAPER, NAVY, dot=True),
        "logo-light.svg": full_logo(NAVY, PAPER, dot=True),
        "logo-mark.svg": mark(PAPER, NAVY),
        "favicon.svg": favicon(32),
    }
    for name, text in files.items():
        (ASSETS / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote assets/{name}")

    chrome = find_chrome()
    if chrome is None:
        print("no Chrome found: skipped favicon.ico and the preview", file=sys.stderr)
        return 0
    from PIL import Image

    icons = []
    for size, hint in ((16, 16), (32, 32)):
        big = Image.open(io.BytesIO(render(favicon(hint), 256, chrome))).convert("RGBA")
        icons.append(big.resize((size, size), Image.Resampling.LANCZOS))
    icons[1].save(ASSETS / "favicon.ico", sizes=[(16, 16), (32, 32)], append_images=[icons[0]])
    print("wrote assets/favicon.ico (16, 32)")
    (ASSETS / "social-preview.png").write_bytes(render(social_preview(), 1280, chrome, 640))
    print("wrote assets/social-preview.png (1280 x 640, for GitHub's repo settings)")

    PREVIEW.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (PREVIEW / name.replace(".svg", ".png")).write_bytes(render(text, 512, chrome))
    for size, icon in zip((16, 32), icons, strict=True):
        icon.resize((size * 8, size * 8), Image.Resampling.NEAREST).save(
            PREVIEW / f"favicon-{size}-x8.png"
        )
    print(f"previews in {PREVIEW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
