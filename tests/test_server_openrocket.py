"""MCP server end to end with OpenRocket: in-process client, then a real stdio subprocess."""

import asyncio
import sys

import pytest
from mcp import Client, StdioServerParameters

from conftest import REPO
from ogive.server import tools
from ogive.server.app import server

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
    corrupt the protocol and fail this test."""
    params = StdioServerParameters(command=sys.executable, args=["-m", "ogive.server"], cwd=REPO)

    async def judge(client):
        listed = await client.list_tools()
        result = await client.call_tool(
            "judge_design", {"file": str(design), "simulation": REF_SIM}
        )
        return listed, result

    listed, result = mcp_call(judge, params)
    assert len(listed.tools) == 8
    assert not result.is_error and result.structured_content["passed"]
