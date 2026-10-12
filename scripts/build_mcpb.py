"""Build the Claude Desktop bundle: out/ogive-VERSION.mcpb.

Stages packaging/mcpb/ plus Ogive's source in out/mcpb/ogive/, validates the manifest and packs
it with the official mcpb CLI (Node, via npx). Run from the repo root:
    .venv\\Scripts\\python.exe scripts\\build_mcpb.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGING = REPO / "packaging" / "mcpb"
STAGE = REPO / "out" / "mcpb" / "ogive"
MCPB_CLI = "@anthropic-ai/mcpb@2.1.2"  # pinned: the manifest targets spec 0.4


def main() -> int:
    sys.path.insert(0, str(REPO / "src"))
    from ogive import __version__

    manifest = json.loads((PACKAGING / "manifest.json").read_text(encoding="utf-8"))
    with open(PACKAGING / "pyproject.toml", "rb") as f:
        bundle_version = tomllib.load(f)["project"]["version"]
    if not manifest["version"] == bundle_version == __version__:
        print(
            f"version mismatch: manifest {manifest['version']}, bundle pyproject {bundle_version}, "
            f"ogive {__version__}. Make them equal.",
            file=sys.stderr,
        )
        return 1

    if STAGE.exists():
        shutil.rmtree(STAGE)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".venv", "uv.lock")
    shutil.copytree(PACKAGING, STAGE, ignore=ignore)
    shutil.copytree(REPO / "src" / "ogive", STAGE / "src" / "ogive", ignore=ignore)
    shutil.copy2(REPO / "LICENSE", STAGE / "LICENSE")
    for icon in ("icon.png", "icon-256.png", "icon-64.png"):  # manifest "icon" / "icons"
        shutil.copy2(REPO / "assets" / icon, STAGE / icon)

    npx = shutil.which("npx") or _installed_npx()
    if npx is None:
        print("npx not found: install Node.js to pack the bundle", file=sys.stderr)
        return 1
    # npx.cmd runs `node`, so Node's folder must be on PATH too.
    env = dict(os.environ, PATH=os.pathsep.join([str(Path(npx).parent), os.environ["PATH"]]))
    bundle = REPO / "out" / f"ogive-{__version__}.mcpb"
    for args in (["validate", str(STAGE / "manifest.json")], ["pack", str(STAGE), str(bundle)]):
        subprocess.run([npx, "-y", MCPB_CLI, *args], check=True, env=env)
    print(f"bundle: {bundle} ({bundle.stat().st_size / 1024:.0f} KB)")
    return 0


def _installed_npx() -> str | None:
    """Node's default Windows location, for shells whose PATH lacks it."""
    candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "npx.cmd"
    return str(candidate) if candidate.exists() else None


if __name__ == "__main__":
    raise SystemExit(main())
