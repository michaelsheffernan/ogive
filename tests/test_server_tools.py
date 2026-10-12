"""MCP server tools that need no OpenRocket: paths, provenance, edits, diffs, the MCP wiring."""

import asyncio
import hashlib
from pathlib import Path

import pytest
from mcp import Client

from ogive.judge import PASS, Report, Verdict
from ogive.ork_io import OrkDocument
from ogive.server import tools
from ogive.server.app import server

FINS = "Trapezoidal Fin Set"


def sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def design(v2_locked_ork, tmp_path):
    """A copy of the locked v2 file in a scratch folder (tools write next to their input)."""
    copy = tmp_path / "v2.ork"
    copy.write_bytes(v2_locked_ork.read_bytes())
    return copy


# --- paths --------------------------------------------------------------------------------


def test_design_path_needs_an_existing_ork(tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    with pytest.raises(tools.ToolError, match="not an .ork"):
        tools.design_path(str(tmp_path / "notes.txt"))
    with pytest.raises(tools.ToolError, match="no such file"):
        tools.design_path(str(tmp_path / "missing.ork"))


def test_ogive_root_confines_every_path(design, tmp_path, monkeypatch, v2_locked_ork):
    monkeypatch.setenv(tools.ROOT_ENV, str(tmp_path))
    assert tools.design_path(str(design)) == design.resolve()
    with pytest.raises(tools.ToolError, match="outside OGIVE_ROOT"):
        tools.design_path(str(v2_locked_ork))
    with pytest.raises(tools.ToolError, match="outside OGIVE_ROOT"):
        tools.new_output_path(str(v2_locked_ork.parent / "x.ork"), design)


def test_new_output_path_numbers_and_never_overwrites(design):
    first = tools.new_output_path(None, design)
    assert first == design.parent / "ogive" / "v2-001.ork"
    first.parent.mkdir()
    first.write_bytes(b"taken")
    assert tools.new_output_path(None, design).name == "v2-002.ork"
    with pytest.raises(tools.ToolError, match="refusing to overwrite"):
        tools.new_output_path(str(first), design)
    with pytest.raises(tools.ToolError, match="must end in .ork"):
        tools.new_output_path(str(design.parent / "out.txt"), design)


# --- reading and writing ------------------------------------------------------------------


def test_inspect_design_lists_editable_fields_with_provenance(design):
    record = tools.inspect_design(str(design))
    fins = next(c for c in record["components"] if c["name"] == FINS)
    assert fins["fields"]["height"] == pytest.approx(0.032)
    assert fins["kind"] == "trapezoidfinset" and fins["mass_kg"] > 0
    nose = next(c for c in record["components"] if c["name"] == "Nose Cone")
    assert "aftradius" in nose["auto_fields"]
    assert record["provenance"]["sha256"] == sha(design)
    assert record["motors"][0]["designation"] == "229H255-14A"


def test_edit_design_writes_a_new_file_and_leaves_the_original(design):
    before = sha(design)
    record = tools.edit_design(
        str(design), [{"component": FINS, "field": "height", "value": 0.034}]
    )
    assert sha(design) == before
    edited = OrkDocument.load(record["output"])
    assert edited.find_component(FINS).number("height") == 0.034
    assert record["changes"] == [
        {
            "component": FINS,
            "field": "height",
            "before": pytest.approx(0.032),
            "before_was_auto": False,
            "after": 0.034,
        }
    ]
    assert record["simulations_marked_outdated"] > 0
    assert record["provenance"]["sha256"] == sha(
        edited_path := design.parent / "ogive" / "v2-001.ork"
    )
    assert record["output"] == str(edited_path)


def test_edit_design_is_all_or_nothing(design):
    changes = [
        {"component": FINS, "field": "height", "value": 0.034},
        {"component": FINS, "field": "colour", "value": 1.0},
    ]
    with pytest.raises(tools.ToolError, match="colour"):
        tools.edit_design(str(design), changes)
    assert not (design.parent / "ogive").exists()
    with pytest.raises(tools.ToolError, match="missing"):
        tools.edit_design(str(design), [{"component": FINS, "value": 1.0}])
    with pytest.raises(tools.ToolError, match="no changes"):
        tools.edit_design(str(design), [])


def test_add_mass_component_and_the_diff_shows_it(design):
    record = tools.add_mass_component(str(design), "Nose Cone", "Nose weight", 0.02, 0.10)
    assert record["added"]["parent"] == "Nose Cone"
    assert record["centre_of_mass_from_parent_front_m"] == pytest.approx(0.11)
    diff = tools.design_diff(OrkDocument.load(design), OrkDocument.load(record["output"]))
    assert diff["added_components"] == ["Nose weight"]
    assert diff["changed_fields"] == [] and diff["motors"] is None


def test_design_diff_reports_field_changes(design):
    record = tools.edit_design(
        str(design), [{"component": FINS, "field": "height", "value": 0.036}]
    )
    diff = tools.design_diff(OrkDocument.load(design), OrkDocument.load(record["output"]))
    assert diff["changed_fields"] == [
        {"component": FINS, "field": "height", "base": "0.032", "candidate": "0.036"}
    ]


def test_downsample_keeps_first_and_last_samples():
    series = {"time": list(range(1001)), "altitude": list(range(1001))}
    small = tools._downsample(series, 100)
    assert len(small["time"]) <= 100
    assert small["time"][0] == 0 and small["time"][-1] == 1000
    assert small["time"] == small["altitude"]
    with pytest.raises(tools.ToolError):
        tools._downsample(series, 1)


def test_optimize_plan_validates_before_flying(design):
    plan = tools.optimize_plan(str(design), "31:32:1", "0:10:5")
    assert len(plan.variants) == 6 and plan.base_fin_height == pytest.approx(0.032)
    assert plan.out_dir == design.parent / "ogive" / "opt-v2"
    with pytest.raises(tools.ToolError, match="over the limit"):
        tools.optimize_plan(str(design), "20:60:0.1", "0:30:1")
    with pytest.raises(tools.ToolError, match="start:stop:step"):
        tools.optimize_plan(str(design), "30-45", "0")


def test_report_record_carries_provenance_and_advisory(design):
    report = Report(
        str(design),
        "sim",
        0,
        "OpenRocket 24.12",
        [Verdict("x", "X", PASS, "1", "2", "src", measured=1.0)],
        {"apogee": 1900.0},
    )
    record = tools.report_record(report, design)
    assert record["passed"] and record["counts"]["pass"] == 1
    assert record["verdicts"][0]["measured"] == 1.0
    assert record["provenance"]["backend"] == "OpenRocket 24.12"
    assert record["provenance"]["rules"].startswith("default")
    assert "sign off" in record["advisory"]


# --- MCP wiring (in-process client) -------------------------------------------------------


def mcp_call(fn):
    async def run():
        async with Client(server) as client:
            return await fn(client)

    return asyncio.run(run())


def test_server_lists_every_tool_resource_and_prompt():
    async def listing(client):
        tools_ = await client.list_tools()
        resources = await client.list_resources()
        prompts = await client.list_prompts()
        return tools_.tools, resources.resources, prompts.prompts

    listed, resources, prompts = mcp_call(listing)
    assert {t.name for t in listed} == {
        "inspect_design",
        "run_simulation",
        "static_stability",
        "judge_design",
        "edit_design",
        "add_mass_component",
        "compare_designs",
        "optimize_fins_and_nose_weight",
    }
    read_only = {t.name for t in listed if t.annotations and t.annotations.read_only_hint}
    assert "edit_design" not in read_only and "judge_design" in read_only
    assert [str(r.uri) for r in resources] == ["ogive://rules/default"]
    assert [p.name for p in prompts] == ["improve_design"]


def test_server_returns_structured_results_and_readable_errors(design):
    async def calls(client):
        ok = await client.call_tool("inspect_design", {"file": str(design)})
        bad = await client.call_tool(
            "edit_design",
            {"file": str(design), "changes": [{"component": "Rudder", "field": "x", "value": 1}]},
        )
        rules = await client.read_resource("ogive://rules/default")
        prompt = await client.get_prompt("improve_design", {"file": "a.ork", "goal": "go faster"})
        return ok, bad, rules, prompt

    ok, bad, rules, prompt = mcp_call(calls)
    assert not ok.is_error and ok.structured_content["provenance"]["sha256"] == sha(design)
    assert bad.is_error and "no component named 'Rudder'" in bad.content[0].text
    assert "min_cal" in rules.contents[0].text
    text = prompt.messages[0].content.text
    assert "go faster" in text and "Never invent a number" in text


def test_optimizer_refuses_a_folder_holding_other_files(design, tmp_path):
    busy = tmp_path / "thesis"
    busy.mkdir()
    (busy / "chapter1.docx").write_bytes(b"precious")
    with pytest.raises(tools.ToolError, match="holds other files"):
        tools.optimize_plan(str(design), "32", "0", out_dir=str(busy))
    rerun = tmp_path / "sweep"
    (rerun / "variants").mkdir(parents=True)
    (rerun / "results.csv").write_text("old")
    assert tools.optimize_plan(str(design), "32", "0", out_dir=str(rerun)).out_dir == rerun


def test_desktop_bundle_manifest_matches_the_server():
    """packaging/mcpb/manifest.json lists the server's tools and versions; keep them in sync."""
    import json
    import tomllib

    from conftest import REPO
    from ogive import __version__

    folder = REPO / "packaging" / "mcpb"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    with open(folder / "pyproject.toml", "rb") as f:
        bundle = tomllib.load(f)["project"]

    async def listing(client):
        return (await client.list_tools()).tools

    served = {t.name for t in mcp_call(listing)}
    assert {t["name"] for t in manifest["tools"]} == served
    assert manifest["version"] == bundle["version"] == __version__
    assert manifest["server"]["type"] == "uv"
    assert any(d.startswith("mcp") for d in bundle["dependencies"])
    assert any(d.startswith("jpype1") for d in bundle["dependencies"])


# --- relative paths (Claude Desktop starts the server in the bundle's folder) ---------------


def test_relative_paths_resolve_inside_ogive_root(design, tmp_path, monkeypatch):
    elsewhere = tmp_path / "server-cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv(tools.ROOT_ENV, str(design.parent))
    assert tools.design_path("v2.ork") == design.resolve()
    record = tools.edit_design(
        "v2.ork", [{"component": FINS, "field": "height", "value": 0.034}], "variants/v2-34.ork"
    )
    assert Path(record["output"]) == (design.parent / "variants" / "v2-34.ork").resolve()
    assert Path(record["output"]).is_file()
    assert not (elsewhere / "variants").exists()


def test_relative_paths_cannot_escape_ogive_root(design, monkeypatch):
    monkeypatch.setenv(tools.ROOT_ENV, str(design.parent))
    with pytest.raises(tools.ToolError, match="outside OGIVE_ROOT"):
        tools.design_path("../v2.ork")
    with pytest.raises(tools.ToolError, match="outside OGIVE_ROOT"):
        tools.new_output_path("../escaped.ork", design)


def test_relative_paths_without_ogive_root_use_the_working_directory(design, monkeypatch):
    monkeypatch.delenv(tools.ROOT_ENV, raising=False)
    monkeypatch.chdir(design.parent)
    assert tools.design_path("v2.ork") == design.resolve()


def test_relative_path_over_mcp_like_claude_desktop(design, tmp_path, monkeypatch):
    elsewhere = tmp_path / "bundle"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv(tools.ROOT_ENV, str(design.parent))

    async def inspect(client):
        return await client.call_tool("inspect_design", {"file": "v2.ork"})

    result = mcp_call(inspect)
    assert not result.is_error, result.content[0].text
    assert result.structured_content["provenance"]["sha256"] == sha(design)
