"""MCP server end to end with OpenRocket: in-process client, then a real stdio subprocess."""

import asyncio
import os
import sys

import pytest
from mcp import Client, StdioServerParameters

from conftest import REPO
from ogive.server import tools
from ogive.server.app import server
from ogive.sim import openrocket

pytestmark = pytest.mark.openrocket

REF_SIM = "Para + Streamer Test"
FINS = "Trapezoidal Fin Set"


@pytest.fixture
def design(running_openrocket, v2_locked_ork, tmp_path):
    copy = tmp_path / "v2.ork"
    copy.write_bytes(v2_locked_ork.read_bytes())
    return copy


def mcp_call(fn, target=server):
    async def run():
        async with Client(target) as client:
            return await fn(client)

    return asyncio.run(run())


def test_judge_over_mcp_matches_the_reference(design):
    async def judge(client):
        return await client.call_tool("judge_design", {"file": str(design), "simulation": REF_SIM})

    result = mcp_call(judge)
    record = result.structured_content
    assert not result.is_error and record["passed"]
    assert record["metrics"]["apogee"] == pytest.approx(1902.2, abs=1.0)  # v2 baseline, seed 0
    assert record["provenance"]["backend"] == "OpenRocket 24.12"
    assert record["provenance"]["seed"] == 0
    flutter = next(v for v in record["verdicts"] if v["rule"] == "flutter.margin")
    assert flutter["assumptions"], "the assumed shear modulus must reach the client"


def test_edit_then_compare_proves_the_change(design):
    """Bigger fins: more stable, slower, and the flutter verdict flips to FAIL at G 3 GPa."""
    edit = tools.edit_design(str(design), [{"component": FINS, "field": "height", "value": 0.036}])
    record = tools.compare_designs(str(design), edit["output"], REF_SIM)
    assert record["design_diff"]["changed_fields"][0]["field"] == "height"
    assert record["metrics"]["max_mach"]["change"] < 0
    assert record["base_passed"] and not record["candidate_passed"]
    flutter = next(v for v in record["verdicts"] if v["rule"] == "flutter.margin")
    assert flutter["changed"] and (flutter["base"], flutter["candidate"]) == ("pass", "fail")
    stored = next(v for v in record["verdicts"] if v["rule"] == "stored.current")
    assert stored["candidate"] == "pending"  # the edit marked stored results outdated
    assert record["provenance"]["base"]["seed"] == record["provenance"]["candidate"]["seed"] == 0


def test_run_simulation_series_are_downsampled_and_consistent(design):
    record = tools.run_simulation(str(design), REF_SIM, 0, ["altitude", "mach"], max_points=50)
    series = record["series"]
    assert list(series) == ["time", "altitude", "mach"]
    assert 2 <= len(series["time"]) <= 50
    assert max(series["altitude"]) <= record["summary"]["apogee"] + 1e-6
    assert record["provenance"]["seed"] == 0


def test_static_stability_matches_the_gui(design):
    record = tools.static_stability(str(design))
    assert record["stability_cal"] == pytest.approx(2.644, abs=0.005)  # v2 baseline, seed 0
    assert record["stability_burnout_cal"] > record["stability_cal"]


def test_optimizer_over_mcp_reports_progress(design):
    progress = []

    async def optimize(client):
        async def on_progress(done, total, message):
            progress.append((done, total, message))

        return await client.call_tool(
            "optimize_fins_and_nose_weight",
            {"file": str(design), "fins_mm": "31:32:1", "nose_g": "0", "simulation": REF_SIM},
            progress_callback=on_progress,
        )

    result = mcp_call(optimize)
    record = result.structured_content
    assert not result.is_error, result.content[0].text
    assert record["variants"] == 2 and record["passed"] == 2
    assert record["lightest_meeting_goals"]["fin_height_mm"] == 32
    assert record["current_design"]["fin_height_mm"] == 32
    assert [p[0] for p in progress] == [1, 2] and progress[-1][1] == 2
    assert (design.parent / "ogive" / "opt-v2" / "pareto.svg").exists()


def test_stdio_server_speaks_clean_json_rpc(design):
    """A real `python -m ogive.server` subprocess. Any stray Java output on stdout would
    corrupt the protocol and fail this test. The full environment goes along: the SDK passes
    only a safe few variables by default, and OPENROCKET_HOME / JAVA_HOME (CI's jar) aren't
    among them."""
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "ogive.server"], cwd=REPO, env=dict(os.environ)
    )

    async def judge(client):
        listed = await client.list_tools()
        result = await client.call_tool(
            "judge_design", {"file": str(design), "simulation": REF_SIM}
        )
        return listed, result

    listed, result = mcp_call(judge, params)
    assert len(listed.tools) == 14
    assert not result.is_error and result.structured_content["passed"]


# ---------- Lake V1: side view from OpenRocket's model ----------


def test_side_view_matches_the_ork_geometry(running_openrocket, v2_locked_ork):
    from ogive.ork_io import OrkDocument

    doc = OrkDocument.load(v2_locked_ork)
    nose = doc.find_component("Nose Cone")
    tube = doc.find_component("Body Tube")
    fins = doc.find_component(FINS)
    view = openrocket.side_view(v2_locked_ork)
    by_name = {}
    for o in view.outlines:
        by_name.setdefault(o.name, []).append(o)

    def x_range(o):
        return min(x for x, _ in o.points), max(x for x, _ in o.points)

    assert x_range(by_name["Nose Cone"][0]) == pytest.approx((0, nose.number("length")))
    tube_end = nose.number("length") + tube.number("length")
    assert x_range(by_name["Body Tube"][0])[1] == pytest.approx(tube_end)
    assert view.length == pytest.approx(tube_end)
    radius = tube.number("radius")
    upper_fin = max(by_name[FINS], key=lambda o: max(y for _, y in o.points))
    assert max(y for _, y in upper_fin.points) == pytest.approx(radius + fins.number("height"))
    assert x_range(upper_fin)[1] - x_range(upper_fin)[0] == pytest.approx(fins.number("rootchord"))
    (motor,) = [o for o in view.outlines if o.role == "motor"]
    loaded_motor = openrocket.load(v2_locked_ork).motors[0]
    assert x_range(motor)[1] == pytest.approx(tube_end)  # minimum diameter: flush aft
    assert motor.mass == pytest.approx(loaded_motor.launch_mass)
    static = openrocket.static_analysis(v2_locked_ork)
    assert view.static.cg_x == pytest.approx(static.cg_x)
    assert view.static.cp_x == pytest.approx(static.cp_x)
    assert view.not_drawn == []


def test_render_design_over_mcp_returns_png_and_svg(design):
    import base64
    import io

    from PIL import Image

    async def render(client):
        return await client.call_tool(
            "render_design", {"file": str(design), "assumed": ["Recovery"]}
        )

    result = mcp_call(render)
    assert not result.is_error, result.content[0].text
    image = next(c for c in result.content if c.type == "image")
    assert Image.open(io.BytesIO(base64.b64decode(image.data))).size == (1600, 640)
    svg = next(c for c in result.content if c.type == "resource").resource
    assert svg.mime_type == "image/svg+xml" and "ASSUMED" in svg.text
    record = result.structured_content
    assert record["length_mm"] == pytest.approx(793.0) and record["assumed"] == ["Recovery"]
    assert (
        record["provenance"]["sha256"] in svg.text
        or record["provenance"]["sha256"][:12] in svg.text
    )


def test_edit_and_judge_attach_the_render(design):
    async def calls(client):
        edited = await client.call_tool(
            "edit_design",
            {
                "file": str(design),
                "changes": [{"component": FINS, "field": "height", "value": 0.034}],
            },
        )
        judged = await client.call_tool(
            "judge_design", {"file": str(design), "simulation": REF_SIM}
        )
        return edited, judged

    edited, judged = mcp_call(calls)
    for result in (edited, judged):
        assert not result.is_error
        assert [c.type for c in result.content] == ["text", "image"]
    assert judged.structured_content["passed"]


def test_render_v4_raw_if_present(running_openrocket):

    v4 = REPO / "data" / "ogive" / "blackhawk29_v4_raw.ork"
    if not v4.exists():
        pytest.skip("private design file not present")
    drawing = tools.render_design(str(v4), ["Trapezoidal Fin Set.sweeplength"])
    assert drawing.record["length_mm"] == pytest.approx(1072.0)
    assert drawing.record["assumed"] == ["Trapezoidal Fin Set"]
    assert drawing.png[:8] == b"\x89PNG\r\n\x1a\n"


# ---------- set_motor ----------


def test_set_motor_swaps_by_impulse_and_verifies(design):
    record = tools.set_motor(str(design), "348I204")
    (loaded,) = openrocket.load(record["output"]).motors
    assert loaded.designation == "348I204-13A" and loaded.total_impulse == pytest.approx(
        347.9, abs=0.1
    )
    assert record["delay"].startswith("13 s") or "longest, 13 s" in record["delay"]
    assert "verified" in record and record["mount"] == "mount unchanged"


def test_set_motor_lengthens_a_short_mount(design, tmp_path):
    short = tools.edit_design(
        str(design),
        [{"component": "Body Tube", "field": "length", "value": 0.30}],
        str(tmp_path / "short.ork"),
    )
    record = tools.set_motor(short["output"], "381I224")
    assert "lengthened 300.0 -> 365.0 mm" in record["mount"]
    from ogive.ork_io import OrkDocument

    tube = OrkDocument.load(record["output"]).find_component("Body Tube")
    assert tube.number("length") == pytest.approx(0.365)


def test_set_motor_refuses_unknown_motors(design):
    with pytest.raises(tools.ToolError, match="not in OpenRocket's motor database"):
        tools.set_motor(str(design), "999Z1")


# ---------- Lake V2: the 3D viewer's data ----------


def test_view_design_sends_openrocket_geometry_to_the_app(design):
    import hashlib
    import json
    import math

    from mcp.client.extension import advertise

    from ogive.ork_io import OrkDocument

    apps = advertise("io.modelcontextprotocol/ui", {"mimeTypes": ["text/html;profile=mcp-app"]})
    args = {"file": str(design), "assumed": ["Recovery"]}

    async def run():
        async with Client(server, extensions=[apps]) as client:
            with_apps = await client.call_tool("view_design", args)
        async with Client(server) as client:
            without = await client.call_tool("view_design", args)
        return with_apps, without

    with_apps, without = asyncio.run(run())
    assert not with_apps.is_error and not without.is_error
    # The side view rides along either way: hosts without apps show it, the page falls back to it.
    assert [c.type for c in with_apps.content] == ["text", "image"]
    assert [c.type for c in without.content] == ["text", "image"]
    data = with_apps.structured_content
    assert data["kind"] == "design" and data["assumed"] == ["Recovery"]
    assert len(json.dumps(data)) < 50_000
    assert data["provenance"]["sha256"] == hashlib.sha256(design.read_bytes()).hexdigest()

    doc = OrkDocument.load(design)
    tube = doc.find_component("Body Tube")
    fins = doc.find_component(FINS)
    model = data["model"]
    bodies = {b["name"]: b for b in model["bodies"]}
    assert bodies["Nose Cone"]["profile"][0] == pytest.approx([0, 0], abs=1e-9)
    tail = [data["length"], tube.number("radius")]
    assert bodies["Body Tube"]["profile"][-1] == pytest.approx(tail)
    (fin_set,) = model["fin_sets"]
    assert len(fin_set["instances"]) == int(fins.number("fincount"))
    assert sorted(round(math.degrees(i[2])) for i in fin_set["instances"]) == [-120, 0, 120]
    assert fin_set["thickness"] == pytest.approx(fins.number("thickness"))
    assert max(y for _, y in fin_set["points"]) == pytest.approx(fins.number("height"))
    (motor,) = model["motors"]
    assert motor["x0"] + motor["length"] == pytest.approx(data["length"])  # flush aft
    static = openrocket.static_analysis(design)
    assert data["cg"] == pytest.approx(static.cg_x) and data["cp"] == pytest.approx(static.cp_x)


# ---------- Lake V3: versions on a timeline ----------


def test_view_versions_diffs_and_flies_every_version(design, tmp_path):
    from mcp.client.extension import advertise

    from ogive.judge import judge_file

    taller = tools.edit_design(
        str(design),
        [{"component": FINS, "field": "height", "value": 0.040}],
        str(tmp_path / "v2-fins40.ork"),
    )["output"]
    weighted = tools.add_mass_component(
        taller, "Nose Cone", "Nose weight", 0.015, 0.10, output=str(tmp_path / "v2-nose15.ork")
    )["output"]
    files = [str(design), taller, weighted]
    apps = advertise("io.modelcontextprotocol/ui", {"mimeTypes": ["text/html;profile=mcp-app"]})

    async def run():
        async with Client(server, extensions=[apps]) as client:
            return await client.call_tool("view_versions", {"files": files, "simulation": REF_SIM})

    result = asyncio.run(run())
    assert not result.is_error, result.content[0].text
    assert [c.type for c in result.content] == ["text", "image"]
    data = result.structured_content
    assert data["kind"] == "versions" and data["simulation"] == REF_SIM and data["seed"] == 0
    assert data["different_simulation"] == []
    first, second, third = data["versions"]
    assert first["diff_previous"] is None and first["diff_first"] is None
    assert [(c["component"], c["field"]) for c in second["diff_previous"]["changed_fields"]] == [
        (FINS, "height")
    ]
    assert third["diff_previous"]["added_components"] == ["Nose weight"]
    assert third["diff_previous"]["changed_fields"] == []
    assert third["diff_first"]["added_components"] == ["Nose weight"]
    assert [c["field"] for c in third["diff_first"]["changed_fields"]] == ["height"]
    for version, file in zip(data["versions"], files, strict=True):
        report = judge_file(file, REF_SIM, 0)
        assert version["flight"]["apogee"] == pytest.approx(report.metrics["apogee"])
        assert version["passed"] == report.passed
    assert third["launch_mass"] - second["launch_mass"] == pytest.approx(0.015, abs=1e-6)
    assert third["cg"] < second["cg"]  # nose weight moves the CG forward


def test_view_versions_needs_two_to_twelve_designs(design):
    with pytest.raises(tools.ToolError, match="give 2 to 12 designs"):
        tools.view_versions([str(design)])


def test_reference_simulation_is_the_default_when_the_file_has_it(design, monkeypatch):
    from ogive.sim.openrocket import list_simulations

    names = list_simulations(design)
    assert names[0] != REF_SIM  # otherwise this test proves nothing
    monkeypatch.setenv("OGIVE_SIMULATION", REF_SIM)
    assert tools.judge_design(str(design))["simulation"] == REF_SIM
    assert tools.run_simulation(str(design))["simulation"] == REF_SIM
    monkeypatch.setenv("OGIVE_SIMULATION", "No such sim")
    assert tools.judge_design(str(design))["simulation"] == names[0]  # falls back to the first
    monkeypatch.setenv("OGIVE_SIMULATION", REF_SIM)
    assert tools.judge_design(str(design), names[0])["simulation"] == names[0]  # a named sim wins


# ---------- Lake V4: flight replay ----------


def test_replay_attitude_convention_matches_the_flight_path(design):
    """theta = elevation above the horizon, phi = azimuth from north toward east: coasting, the
    axis points along the flight path (small angle of attack). The viewer relies on this."""
    import math

    data, _ = tools.replay_flight(str(design), REF_SIM)
    s = data["series"]
    when = {e["type"]: e["time"] for e in data["events"]}
    angles = []
    for i in range(1, len(s["time"]) - 1):
        if not when["Launch rod clearance"] + 0.5 < s["time"][i] < when["Apogee"] - 1:
            continue
        dt = s["time"][i + 1] - s["time"][i - 1]
        v = [(s[k][i + 1] - s[k][i - 1]) / dt for k in ("east", "north", "altitude")]
        theta, phi = s["theta"][i], s["phi"][i]
        axis = [math.cos(theta) * math.sin(phi), math.cos(theta) * math.cos(phi), math.sin(theta)]
        cos = sum(a * b for a, b in zip(axis, v, strict=True)) / math.hypot(*v)
        angles.append(math.degrees(math.acos(max(-1.0, min(1.0, cos)))))
    angles.sort()
    assert len(angles) > 50
    assert angles[len(angles) // 2] < 3  # median angle between axis and flight path, degrees
    assert angles[-1] < 15


def test_replay_flight_over_mcp(design):
    import json

    async def call(client):
        return await client.call_tool("replay_flight", {"file": str(design), "simulation": REF_SIM})

    result = mcp_call(call)
    assert not result.is_error, result.content[0].text
    assert [c.type for c in result.content] == ["text", "image"]
    data = result.structured_content
    assert data["kind"] == "flight" and data["label"] == "Replay of simulation data, not footage."
    json.dumps(data, allow_nan=False)  # no NaN for the page's JSON
    flight = openrocket.simulate(design, REF_SIM, 0)
    deployments = [e.time for e in flight.events if e.type == "Recovery device deployment"]
    assert data["attitude_until"] == pytest.approx(deployments[0])
    deployed = [e for e in data["events"] if e["type"] == "Recovery device deployment"]
    assert [(e["source"], e["source_kind"]) for e in deployed] == [
        ("Streamer", "Streamer"),
        ("Parachute", "Parachute"),
    ]
    lengths = {len(v) for v in data["series"].values()}
    assert len(lengths) == 1
    up, down = data["mach_crossings"]
    burnout = next(e["time"] for e in data["events"] if e["type"] == "Motor burnout")
    assert up["direction"] == "up" and down["direction"] == "down"
    assert up["time"] < burnout < down["time"]  # supersonic through burnout, as the summary says
    assert data["summary"]["max_mach"] == pytest.approx(flight.summary.max_mach)
    text = json.loads(result.content[0].text)
    assert text["attitude_simulated_until_s"] == pytest.approx(deployments[0])


# --- landing dispersion -------------------------------------------------------------------


@pytest.mark.parametrize(("wind_from", "drift"), [(0, "south"), (90, "west"), (180, "north")])
def test_wind_direction_is_where_the_wind_blows_from(design, wind_from, drift):
    """OpenRocket's wind direction is the bearing the wind comes FROM, clockwise from north.
    Measured, not assumed: 10 m/s drifts this rocket ~670 m downwind."""
    import math

    flight = openrocket.simulate(
        design, REF_SIM, 0, wind_speed=10.0, wind_direction=math.radians(wind_from)
    )
    east, north = flight.series["east"][-1], flight.series["north"][-1]
    expected = {"south": (0, -1), "west": (-1, 0), "north": (0, 1)}[drift]
    assert east * expected[0] + north * expected[1] > 400  # well downwind
    assert abs(east * expected[1]) + abs(north * expected[0]) < 50  # and not sideways
    assert flight.conditions.wind_direction == pytest.approx(math.radians(wind_from))


def test_wind_override_leaves_the_file_alone(design):
    before = design.read_bytes()
    stored = openrocket.stored_wind(design, REF_SIM)
    openrocket.simulate(design, REF_SIM, 0, wind_speed=9.0, wind_direction=1.0)
    assert design.read_bytes() == before
    assert openrocket.stored_wind(design, REF_SIM) == stored


def test_dispersion_is_reproducible_and_honest(design):
    from ogive.sim.dispersion import landing_dispersion

    a = landing_dispersion(design, REF_SIM, runs=5, seed=3, field_radius=100.0)
    b = landing_dispersion(design, REF_SIM, runs=5, seed=3, field_radius=100.0)
    assert a.landings == b.landings  # same call, same flights
    assert [lg.seed for lg in a.landings] == [3, 4, 5, 6, 7]
    assert a.backend.startswith("OpenRocket") and a.simulation == REF_SIM
    # Each landing is the real last point of that flight.
    first = a.landings[0]
    flight = openrocket.simulate(
        design, REF_SIM, 3, wind_speed=first.wind_speed, wind_direction=_rad(first.wind_from)
    )
    assert (first.east, first.north) == (flight.series["east"][-1], flight.series["north"][-1])
    assert a.outside == sum(lg.distance > 100 for lg in a.landings)


def test_zero_spread_flies_the_stored_wind_every_time(design):
    """With no spread, every flight gets the sim's own wind; only the turbulence seed differs."""
    from ogive.sim.dispersion import landing_dispersion

    d = landing_dispersion(design, REF_SIM, runs=4, seed=0, speed_sd=0.0, direction_sd=0.0)
    _, average, _ = openrocket.stored_wind(design, REF_SIM)
    assert all(lg.wind_speed == pytest.approx(average) for lg in d.landings)


def test_landing_dispersion_over_mcp(design):
    progress = []

    async def disperse(client):
        async def on_progress(done, total, message):
            progress.append((done, total))

        return await client.call_tool(
            "landing_dispersion",
            {"file": str(design), "simulation": REF_SIM, "runs": 4, "field_radius_m": 300},
            progress_callback=on_progress,
        )

    result = mcp_call(disperse)
    assert not result.is_error, result.content[0].text
    record = result.structured_content
    assert result.content[0].type == "image" and result.content[0].mime_type == "image/png"
    assert record["runs"] == 4 and len(record["flights"]) == 4
    assert record["field"] == {"radius_m": 300, "outside": record["field"]["outside"], "of": 4}
    assert record["landing"]["farthest_m"] == max(f["distance_m"] for f in record["flights"])
    assert record["provenance"]["simulation"] == REF_SIM and record["provenance"]["seed"] == 0
    assert progress[-1] == (4, 4)


def test_landing_dispersion_refuses_bad_input(design):
    async def disperse(client):
        return await client.call_tool("landing_dispersion", {"file": str(design), "runs": 2})

    result = mcp_call(disperse)
    assert result.is_error and "3 to 200" in result.content[0].text


def _rad(deg: float) -> float:
    import math

    return math.radians(deg)


def test_landing_cli_writes_map_and_csv(design, tmp_path, capsys):
    import csv

    from ogive.cli import main

    stem = tmp_path / "land" / "v2"
    code = main(
        ["landing", str(design), "--sim", REF_SIM, "--runs", "3", "--field", "100", "-o", str(stem)]
    )
    assert code == 0
    assert stem.with_suffix(".png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    with stem.with_suffix(".csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [int(r["seed"]) for r in rows] == [0, 1, 2]
    out = capsys.readouterr().out
    assert "3 flights" in out and "outside the 100" in out


def test_run_simulation_flies_a_what_if_wind(design):
    async def fly(client):
        return await client.call_tool(
            "run_simulation",
            {
                "file": str(design),
                "simulation": REF_SIM,
                "wind_speed": 10.0,
                "wind_from_deg": 0,
                "series": ["east", "north"],
            },
        )

    result = mcp_call(fly)
    assert not result.is_error, result.content[0].text
    record = result.structured_content
    assert record["conditions"]["wind_average"] == pytest.approx(10.0)
    assert record["conditions"]["wind_direction"] == pytest.approx(0.0)
    assert record["series"]["north"][-1] < -400  # wind from the north: lands well south
