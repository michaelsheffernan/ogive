<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo.svg">
    <img src="assets/logo-light.svg" width="160" alt="Ogive logo: a tangent-ogive nose cone with the circle its curve comes from">
  </picture>
</p>

<h1 align="center">Ogive</h1>

<p align="center">AI tools for OpenRocket that show their working.</p>

Our OpenRocket sim said Mach 1.70. It was wrong.

The thrust curve OpenRocket picked for our motor, a Cesaroni 229H255, was really the data for a bigger motor,
the 315H255. Same name in the dropdown, 38 % more impulse. The simulator was fine. The input was wrong.

I only caught it because the numbers broke the rocket equation. 229 Ns can't push 403 g of burnt-out rocket past
about 569 m/s, even with zero drag. The sim said 577.8 m/s. Then Show Details said 316 Ns.

On the right curve it does Mach 1.55 and 1,924 m. Worse, the bigger fins and nose weight we'd added to keep it
stable were fixing a problem caused by a motor we were never going to fly. We lost a day to that.

I emailed ThrustCurve. The maintainer wrote back that evening, and by the time I checked, the bad file was gone.
OpenRocket 24.12 still ships it, so if you fly a 229H255, check which curve you've got.
Full write-up: [the 229H255 case study](docs/case-study-229H255.md).

So I built Ogive.

## What it is

A set of tools that let an AI work on an OpenRocket design and show its working.

You talk to Claude (or any MCP client). Ogive reads and edits your `.ork`, flies it in your own copy of
OpenRocket, and checks the flight against 14 rules. One of them is the check that would have saved us that day:
integrate the thrust curve and compare it with the motor's name.

Every number comes back with a receipt: which backend made it, which wind seed, and the sha256 of the exact file.
Edits always go to a new file. It never touches your original.

Here it is trying to make our rocket faster: [demo transcript](docs/demo-transcript.md). It tried more fin sweep,
flew both versions, found +0.007 Mach and a stability drop below our goal, and told me not to bother.
That's the whole idea.

It doesn't sign off on flights. OpenRocket, RASAero II, your club and your RSO do that.

## It's early

v0.1. I use it on our rocket. Nobody else has yet.

- Windows only.
- OpenRocket 24.12 only.
- The flutter check is a rough screen for a flat plate. Carbon fins need a real shear modulus; until you give it
  one, it assumes 3 GPa, on the safe side.
- One wind seed per run. No Monte Carlo yet.
- Above Mach 1, OpenRocket's aero is approximate. Ogive warns you. Check in RASAero II.
- It can change numbers and add masses. It can't swap motors or add parts yet.
- The optimizer only sweeps fin height and nose weight.
- The Claude Desktop bundle hasn't been tried in Claude Desktop yet.
- The first call takes about 10 s while Java starts. Then about 1 s each.

If it breaks on your rocket, [open an issue](https://github.com/michaelsheffernan/ogive/issues), with the `.ork` if you can share it.
Found a security problem? See [SECURITY.md](SECURITY.md).

## Setup

You need Windows 10 or 11 and [OpenRocket 24.12](https://openrocket.info/). Ogive uses OpenRocket's own engine and
the Java that comes with it.

**Claude Code** (needs Python 3.11+ and git):

    git clone https://github.com/michaelsheffernan/ogive
    cd ogive
    .\tasks.ps1 setup

Run `claude` in that folder and approve the `ogive` server. To use it from anywhere:

    claude mcp add --scope user ogive -- C:\path\to\ogive\.venv\Scripts\python.exe -m ogive.server

Add `-e OPENROCKET_HOME=...` if OpenRocket isn't in `C:\Program Files\OpenRocket`, and `-e OGIVE_ROOT=C:\Rockets`
to keep Ogive inside one folder.

**Claude Desktop:** run `.\tasks.ps1 mcpb` (needs Node.js), open `out\ogive-0.1.0.mcpb` with Claude Desktop, and
pick your rockets folder. First launch takes about 20 s while it installs Python packages.

If PowerShell won't run the scripts: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## Things to try

- "Judge `C:\Rockets\my_rocket.ork` on its first sim. What fails, and what's assumed?"
- "Make it faster without failing the judge. One change at a time, prove each one."
- "Sweep fin height 28 to 40 mm and nose weight 0 to 20 g. Lightest design that passes?"

## The tools

- `inspect_design`: every part and every number you can edit, plus motors and stored sims.
- `run_simulation`: fly a sim. Same file and seed, same flight.
- `static_stability`: CG, CP and calibers, same as OpenRocket's design view.
- `judge_design`: fly it and run the 14 rules. Every verdict says where its number came from.
- `edit_design`: change numbers, save a new file.
- `add_mass_component`: nose weight or ballast, new file.
- `compare_designs`: diff two designs, fly both on the same sim and seed, show what changed.
- `optimize_fins_and_nose_weight`: try a grid of fin heights and nose weights, judge every one.

The rules: stability at Mach 0.3 and all the way up, rod exit speed, fin flutter, ground hit speed, recovery
warnings, thrust curve vs its name, stale stored results, stored burn time vs the motor, the rocket-equation
ceiling, masses adding up, mass-override traps, length-to-diameter, and a warning above Mach 1.
Thresholds live in [`rules.toml`](src/ogive/judge/rules.toml). Change them for your team.

## Command line

No AI needed for this part. Activate the venv first (`.venv\Scripts\Activate.ps1`).

    ogive tree data\blackhawk29_v2.ork
    ogive sim data\blackhawk29_v2.ork --sim "Para + Streamer Test" --seed 42 --csv out\v2.csv
    ogive judge data\blackhawk29_v2.ork --sim "Para + Streamer Test"
    ogive optimize data\blackhawk29_v2.ork --sim "Para + Streamer Test" --fins 30:45:1 --nose 0:30:5
    ogive set data\blackhawk29_v2.ork --component "Trapezoidal Fin Set" --field height --value 0.030 -o out\v2_fin30.ork

`judge` exits with 1 if anything fails, so it works in scripts. `--seed` pins the wind. OpenRocket 24.12 doesn't
pass its own seed to the wind model; Ogive patches around that. The optimizer guesses where nose weight sits
(100 mm behind the tip); move it with `--nose-at`.

## Hacking on it

    .\tasks.ps1 check    # lint + all tests
    .\tasks.ps1 mcpb     # build the Claude Desktop bundle

Tests marked `openrocket` run against real OpenRocket and skip if it isn't installed.
Code is in `src/ogive/`: `ork_io` (files), `sim` (OpenRocket via JPype), `physics`, `judge`, `opt`, `server` (MCP).

[CHANGELOG.md](CHANGELOG.md) is what changed.

## Licence

GPL-3.0-or-later. Ogive runs inside OpenRocket's process, and OpenRocket is GPL, so this is too.
Details: [LICENSING.md](docs/LICENSING.md). Not affiliated with OpenRocket.

## Thanks

- [OpenRocket](https://openrocket.info/), which does the real work.
- [TheCheng2005/OpenRocket_MCP](https://github.com/TheCheng2005/OpenRocket_MCP), for the corrected flutter
  formula and its tests.
- [ThrustCurve.org](https://www.thrustcurve.org/), for the data that let me find the bad curve, and for the fast reply.
