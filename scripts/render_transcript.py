"""Render a Claude Code session log (stream-json) using Ogive's MCP tools as a markdown transcript.

    claude -p "..." --output-format stream-json --verbose > session.jsonl
    python scripts/render_transcript.py session.jsonl docs/demo-transcript.md --root C:/repo

The user prompt and the model's text are copied verbatim. Tool calls are shown in full; tool
results are abridged to their key fields (the full JSON is in the log). Absolute paths under
--root are shortened.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--root", required=True, help="absolute path to strip from file paths")
    parser.add_argument("--prompt", required=True, help="the prompt given to claude -p, verbatim")
    args = parser.parse_args()

    root = str(Path(args.root).resolve())
    roots = [root + "\\\\", root + "\\", root.replace("\\", "/") + "/"]

    def short(text: str) -> str:
        for r in roots:
            text = text.replace(r, "")
        return text

    events = [
        json.loads(line) for line in args.log.read_text(encoding="utf-8").splitlines() if line
    ]
    init = next(e for e in events if e.get("type") == "system" and e.get("subtype") == "init")
    result = next(e for e in events if e.get("type") == "result")
    calls: dict[str, str] = {}
    out = [
        '# Demo: "make it faster", with proof',
        "",
        f"A real Claude Code session using Ogive's MCP server, rendered from its log by "
        f"`scripts/render_transcript.py`. Claude Code {init.get('claude_code_version')}, model "
        f"`{init.get('model')}`, headless (`claude -p`), Ogive tools only. "
        f"{result.get('num_turns')} turns, {result.get('duration_ms', 0) / 1000:.0f} s.",
        "",
        "The prompt and the model's words are verbatim. Tool calls are shown in full. "
        "Tool results are abridged to their key fields. Paths are shortened to the repo root.",
        "",
        "## User",
        "",
        f"> {args.prompt}",
        "",
    ]
    for event in events:
        kind = event.get("type")
        if kind == "assistant":
            for block in event["message"]["content"]:
                if block["type"] == "text" and block["text"].strip():
                    out += ["## Claude", "", short(block["text"]).strip(), ""]
                elif block["type"] == "tool_use":
                    calls[block["id"]] = block["name"]
                    if block["name"] == "ToolSearch":
                        out += ["*(Claude Code loads the Ogive tool schemas.)*", ""]
                        continue
                    name = block["name"].removeprefix("mcp__ogive__")
                    arguments = short(json.dumps(block["input"], indent=2))
                    out += [f"**Tool call:** `{name}`", "", "```json", arguments, "```", ""]
        elif kind == "user" and isinstance(event["message"]["content"], list):
            for block in event["message"]["content"]:
                if block.get("type") != "tool_result":
                    continue
                name = calls.get(block["tool_use_id"], "").removeprefix("mcp__ogive__")
                if name == "ToolSearch":
                    continue
                content = block["content"]
                text = (
                    content
                    if isinstance(content, str)
                    else "".join(c.get("text", "") for c in content)
                )
                out += [
                    f"**Result** (`{name}`, abridged):",
                    "",
                    "```",
                    short(abridge(name, text)),
                    "```",
                    "",
                ]
    args.output.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {args.output}")
    return 0


def abridge(tool: str, text: str) -> str:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text[:600]
    if tool == "inspect_design":
        lines = [
            f"{len(data['components'])} components: "
            + ", ".join(c["name"] for c in data["components"])
        ]
        for c in data["components"]:
            if c["kind"] == "trapezoidfinset":
                fields = ", ".join(
                    f"{k} {v:g}"
                    for k, v in c["fields"].items()
                    if k not in ("position", "axialoffset")
                )
                lines.append(f"{c['name']}: {fields}")
        lines.append(
            "motor: "
            + ", ".join(f"{m['designation']} (curve {m['digest'][:8]})" for m in data["motors"])
        )
        lines.append(_provenance(data["provenance"]))
        return "\n".join(lines)
    if tool == "judge_design":
        m = data["metrics"]
        lines = [
            f"passed: {data['passed']}  {data['counts']}",
            f"apogee {m['apogee']:.1f} m, max velocity {m['max_velocity']:.1f} m/s, "
            f"max Mach {m['max_mach']:.4f}, launch mass {m['launch_mass'] * 1000:.1f} g",
        ]
        lines += [
            f"  {v['state'].upper():<5} {v['rule']}: {v['value']} (limit {v['limit']})"
            for v in data["verdicts"]
        ]
        lines.append(_provenance(data["provenance"]))
        return "\n".join(lines)
    if tool in ("edit_design", "add_mass_component"):
        lines = [
            f"output: {data['output']}",
            f"stored sims marked outdated: {data['simulations_marked_outdated']}",
        ]
        lines += [
            f"  {c['component']}.{c['field']}: {c['before']:g} -> {c['after']:g}"
            for c in data.get("changes", [])
        ]
        lines.append(_provenance(data["provenance"]))
        return "\n".join(lines)
    if tool == "compare_designs":
        lines = ["design diff:"]
        lines += [
            f"  {c['component']}.{c['field']}: {c['base']} -> {c['candidate']}"
            for c in data["design_diff"]["changed_fields"]
        ]
        lines.append("metrics (base -> candidate, change):")
        for key, v in data["metrics"].items():
            lines.append(f"  {key}: {v['base']:.4f} -> {v['candidate']:.4f} ({v['change']:+.4f})")
        lines.append("verdicts:")
        for v in data["verdicts"]:
            flag = "CHANGED " if v["changed"] else ""
            lines.append(
                f"  {flag}{v['rule']}: {v['base']} {v['base_value']} -> "
                f"{v['candidate']} {v['candidate_value']}"
            )
        lines.append(
            f"base passed {data['base_passed']}, candidate passed {data['candidate_passed']}"
        )
        for side in ("base", "candidate"):
            lines.append(f"{side}: " + _provenance(data["provenance"][side]))
        return "\n".join(lines)
    return json.dumps(data, indent=1)[:800]


def _provenance(p: dict) -> str:
    seed = f", seed {p['seed']}" if "seed" in p else ""
    return (
        f"provenance: {p['backend']}{seed}, ogive {p['ogive']}, "
        f"sha256 {p['sha256'][:12]}…, {p['file']}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
