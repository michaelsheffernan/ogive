"""Lake V1: the to-scale side view. Layout and drawing on synthetic geometry (no OpenRocket)."""

import io
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PIL import Image

from ogive.sim.openrocket import FinGeometry, Outline, SideView, StaticAnalysis
from ogive.viewer import side_view as sv

SHA = "ab" * 32


def box(x0, x1, r):
    return [(x0, r), (x1, r), (x1, -r), (x0, -r)]


def synthetic(comment: str = "") -> SideView:
    """A 1 m tube, 50 mm diameter, one fin, one internal mass, one motor."""
    static = StaticAnalysis(
        backend="test",
        mach=0.3,
        cg_x=0.5,
        cg_burnout_x=0.45,
        cp_x=0.7,
        length=1.0,
        max_diameter=0.05,
        mass_with_motors=0.5,
        mass_without_motors=0.3,
        components=[],
        thrust_curve=[],
        warnings=[],
    )
    outlines = [
        Outline("body", "BodyTube", "Tube", "", 0.1, box(0.0, 1.0, 0.025)),
        Outline(
            "fin",
            "TrapezoidFinSet",
            "Fins",
            "",
            0.02,
            [(0.9, 0.025), (0.93, 0.065), (0.97, 0.065), (1.0, 0.025)],
        ),
        Outline("internal", "MassComponent", "Avionics", comment, 0.03, box(0.2, 0.3, 0.02)),
        Outline("motor", "Motor", "TEST-1", "", 0.2, box(0.7, 1.0, 0.0145)),
    ]
    fins = [FinGeometry("Fins", 3, 0.1, 0.04, 0.04, 0.03, 0.9)]
    return SideView("test", 1.0, 0.025, outlines, fins, ["TEST-1"], static, [])


def polygons(scene):
    return [s for s in scene.shapes if s.kind == "polygon"][1:]  # [0] is the background


def test_drawing_is_to_scale_on_both_axes():
    scene = sv.build(synthetic(), "t.ork", SHA, "0")
    tube = polygons(scene)[0]
    xs, ys = [p[0] for p in tube.points], [p[1] for p in tube.points]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    assert width == pytest.approx(1.0 * scene.scale)
    assert height == pytest.approx(0.05 * scene.scale)  # same px per metre: to scale
    assert width / height == pytest.approx(20.0)


def test_svg_is_valid_and_carries_provenance():
    svg = sv.to_svg(sv.build(synthetic(), "t.ork", SHA, "9.9"))
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    assert SHA[:12] in svg and "Ogive 9.9" in svg and "OpenRocket computes them" in svg
    assert "CG 500 mm" in svg and "CP 700 mm" in svg and "Stability 4.00 cal" in svg


def test_png_keeps_size_and_marker_colours():
    view = synthetic()
    scene = sv.build(view, "t.ork", SHA, "0")
    for palette in (None, 256):
        image = Image.open(io.BytesIO(sv.to_png(scene, palette))).convert("RGB")
        assert image.size == (sv.WIDTH, sv.HEIGHT)
        cp = (round(sv.MARGIN + view.static.cp_x * scene.scale), sv.AXIS_Y)
        assert all(abs(a - b) <= 8 for a, b in zip(image.getpixel(cp), (192, 57, 43), strict=True))


def test_assumed_by_name_field_or_comment():
    scene = sv.build(synthetic(), "t.ork", SHA, "0", {"Fins.sweeplength"})
    assert scene.assumed == ["Fins"]
    assert any(s.hatch for s in polygons(scene))
    assert "ASSUMED (sweeplength)" in sv.to_svg(scene)
    by_comment = sv.build(synthetic(comment="mass ASSUMED, not weighed"), "t.ork", SHA, "0")
    assert by_comment.assumed == ["Avionics"]
    with pytest.raises(ValueError, match="no component named 'Rudder'"):
        sv.build(synthetic(), "t.ork", SHA, "0", {"Rudder"})


def test_labels_stay_on_the_canvas():
    view = synthetic()
    long_name = Outline(
        "fin", "TrapezoidFinSet", "Fins " + "x" * 60, "", 0.02, view.outlines[1].points
    )
    view = SideView(
        view.backend,
        view.length,
        view.max_radius,
        [*view.outlines, long_name],
        view.fins,
        view.motors,
        view.static,
        [],
    )
    scene = sv.build(view, "t.ork", SHA, "0")
    for s in scene.shapes:
        if s.kind == "text" and s.anchor == "middle":
            half = sv._text_width(s.text, s.size) / 2
            assert s.points[0][0] - half >= 0 and s.points[0][0] + half <= sv.WIDTH


# ---------- Lake V2: the in-chat 3D viewer page ----------


def test_viewer_page_is_self_contained():
    """Hosts sandbox the page with a CSP that blocks the network: everything must be inline."""
    from ogive import __version__
    from ogive.viewer.page import viewer_html

    html = viewer_html()
    lower = html.lower()
    for external in ("<script src", "<link", "@import", "<iframe", 'src="http', 'href="http'):
        assert external not in lower, external
    assert "/*__three__*/" not in lower and "/*__app__*/" not in lower
    assert "__OGIVE_VERSION__" not in html and f'"{__version__}"' in html
    assert "Three.js Authors" in html  # three.js keeps its MIT notice
    assert lower.count("</script>") == 2


def _node() -> str | None:
    default = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "nodejs" / "node.exe"
    return shutil.which("node") or (str(default) if default.exists() else None)


def test_viewer_and_server_agree_on_modes():
    """Every kind a tool sends is one the page draws, and the page draws nothing else.

    A host once paired a cached older page with a newer server ("unknown viewer mode versions");
    the hashed URI prevents that, this keeps the two sides of the contract in step.
    """
    import re

    from ogive.server import tools
    from ogive.viewer.page import HERE, MODES

    app_js = (HERE / "app" / "app.js").read_text(encoding="utf-8")
    table = re.search(r"var MODES = \{([^}]*)\}", app_js)
    assert table, "app.js must keep its MODES table"
    page_modes = set(re.findall(r"(\w+):", table.group(1)))
    sent = set(re.findall(r'"kind": "(\w+)"', Path(tools.__file__).read_text(encoding="utf-8")))
    assert sent == set(MODES) == page_modes


def test_viewer_uri_changes_with_the_page():
    import hashlib

    from ogive.viewer.page import viewer_html, viewer_uri

    digest = hashlib.sha256(viewer_html().encode("utf-8")).hexdigest()
    assert viewer_uri() == f"ui://ogive/viewer-{digest[:12]}.html"


def test_viewer_falls_back_instead_of_failing():
    """Unknown modes and drawing errors show the tool's numbers and side view, never just text."""
    from ogive.viewer.page import HERE

    app_js = (HERE / "app" / "app.js").read_text(encoding="utf-8")
    assert "unknown viewer mode" not in app_js
    assert app_js.count("renderFallback(result,") >= 3  # unknown mode, no data, drawing error


@pytest.mark.skipif(_node() is None, reason="needs Node.js")
def test_viewer_script_parses():
    from ogive.viewer.page import HERE

    subprocess.run([_node(), "--check", str(HERE / "app" / "app.js")], check=True)
