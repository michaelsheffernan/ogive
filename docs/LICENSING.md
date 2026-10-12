# Licensing

**Ogive is licensed GPL-3.0-or-later.** Full text: [LICENSE](../LICENSE). Package metadata: `pyproject.toml`.

This is our reading of the GPL and of the Free Software Foundation's GPL FAQ, checked 2026-10-11.
It is not legal advice. Before any commercial use or relicensing, ask a lawyer.

## Why GPL, and not MIT or Apache

Any one of these three reasons is enough on its own.

**1. Ogive runs OpenRocket inside its own process.** `ogive.sim.openrocket` starts a Java VM inside the Python
process (JPype), loads OpenRocket's jar and calls its classes directly. Rocket, simulation and flight-data objects
pass back and forth. OpenRocket is "GPL version 3, or (at your option) any later version" (OpenRocket `LICENSE.TXT`).
The FSF FAQ:

> If the main program dynamically links plug-ins, and they make function calls to each other and share data
> structures, we believe they form a single combined program, which must be treated as an extension of both the
> main program and the plug-ins. ([#GPLPlugins](https://www.gnu.org/licenses/gpl-faq.html#GPLPlugins))

> If a library is released under the GPL (not the LGPL), does that mean that any software which uses it has to be
> under the GPL or a GPL-compatible license? Yes, because the program actually links to the library.
> ([#IfLibraryIsGPL](https://www.gnu.org/licenses/gpl-faq.html#IfLibraryIsGPL))

JPype calls are function calls within a shared address space, so Ogive plus OpenRocket is one combined program.
A GPL-compatible licence is the minimum. GPL itself is the natural choice.

**2. Ogive contains ported GPL code.** `src/ogive/physics/flutter.py` ports the corrected Martin flutter formula
and its test cases from [TheCheng2005/OpenRocket_MCP](https://github.com/TheCheng2005/OpenRocket_MCP)
(`calc/Flutter.java`, GPL-3.0-or-later), with credit in the module docstring. A port is a derivative work.

**3. The face (Lake 6) is a fork of OpenRocket.** A fork of GPL code is GPL.

## Why "or later"

It matches OpenRocket's own grant, so code can move between Ogive and OpenRocket in both directions. It also lets
Ogive follow a future GPL version if OpenRocket does.

## What the GPL does and doesn't cover here

- **Your rocket designs are yours.** The FAQ:

  > If the user uses your program to enter or convert her own data, the copyright on the output belongs to her,
  > not you. ([#GPLOutput](https://www.gnu.org/licenses/gpl-faq.html#GPLOutput))

  `.ork` files, simulation results and judge reports made with Ogive carry no GPL obligation.
- **MCP clients are separate programs.** Claude Desktop, Claude Code or any other client talks to Ogive over MCP:
  JSON messages over stdin/stdout pipes, in a separate process. The FAQ puts the line between "two separate
  programs" and "one program" at the mechanism ("exec, pipes, rpc, function calls within a shared address space")
  and the semantics of the communication ([#MereAggregation](https://www.gnu.org/licenses/gpl-faq.html#MereAggregation)).
  A client that only exchanges tool calls and JSON results is not a derivative of Ogive.
- **Running Ogive privately has no obligations.** The GPL's conditions apply when you convey (distribute) copies.
- **Hosting is not distribution.** GPL-3.0 (unlike AGPL-3.0) does not require sharing source when Ogive runs as a
  network service. If we want that requirement, AGPL-3.0-or-later is the alternative. It is compatible with
  GPLv3 code. Decision: keep GPL (simpler, matches OpenRocket).
- **What Ogive distributes.** The source repo and the Claude Desktop bundle (`.mcpb`) contain only Ogive's code
  and its licence. OpenRocket is not bundled: users install it themselves. If a future installer bundles
  OpenRocket, it must carry OpenRocket's licence and source offer. OpenRocket's extra section 7 permission
  ("package this Program ... along with any non-compilable data files (such as thrust curves or component
  databases)") then also applies.
- One OpenRocket source file (`ScriptEngineManagerRedux.java`) is OpenJDK code under GPLv2 with the Classpath
  Exception. That exception allows linking with code under other licences. No effect on Ogive.

## Third-party components

All runtime dependencies use licences compatible with GPLv3.

| Component | Use | Licence | Shipped by Ogive? |
|---|---|---|---|
| OpenRocket 24.12 | Engine: loader, aerodynamics, simulation (in-process via JPype) | GPL-3.0-or-later (+ data-file permission) | No, user installs it |
| Java runtime bundled with OpenRocket | Runs OpenRocket | GPLv2 with Classpath Exception (OpenJDK) | No |
| JPype1 1.7 | Python-Java bridge | Apache-2.0 (GPLv3-compatible) | No, installed by pip/uv |
| mcp (Python SDK) 2.3 | MCP server | MIT | No, installed by pip/uv |
| pydantic, anyio, starlette, uvicorn (via mcp) | MCP SDK dependencies | MIT, MIT, BSD-3-Clause, BSD-3-Clause | No, installed by pip/uv |
| pytest, ruff | Development only | MIT | No |
| TheCheng2005/OpenRocket_MCP | Flutter formula and test cases, ported | GPL-3.0-or-later | Yes, as a port, credited |
| NACA TN 4197 (Martin, 1958) | Flutter relation | US government work | Formula only |
| ThrustCurve.org data | Used to verify motor curves; not redistributed | ThrustCurve terms | No |

## Open choices

- Per-file SPDX headers (`# SPDX-License-Identifier: GPL-3.0-or-later`). The FSF recommends a notice in each file.
  It is optional: the root LICENSE plus `pyproject.toml` already state the licence.
- Docs under a separate licence (e.g. CC BY 4.0 for the case study). Default: everything in the repo is
  GPL-3.0-or-later.
