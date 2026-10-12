<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo.svg">
    <img src="assets/logo-light.svg" width="160" alt="Ogive logo: a tangent-ogive nose cone with the circle its curve comes from">
  </picture>
</p>

<h1 align="center">Ogive</h1>

<p align="center">AI tools for OpenRocket that show their working.</p>

A thrust curve can lie. OpenRocket 24.12 ships a curve labelled Cesaroni 229H255 that is really the 315H255's
data. Same name in the dropdown, 38 % more impulse. The sim runs fine. The numbers are wrong, and so is every
design decision you tune around them.

I found that one the hard way: the predicted speed broke the rocket equation. ThrustCurve fixed their copy the
same day I reported it. OpenRocket 24.12 still ships it, so if you fly a 229H255, check which curve you've got.
Full story: [the 229H255 case study](docs/case-study-229H255.md).

So I built Ogive.

## What it is

A set of tools that let an AI work on an OpenRocket design and show its working.

You talk to Claude (or any MCP client). Ogive reads and edits your `.ork`, flies it in your own copy of
OpenRocket, and checks the flight against 14 rules. One of them catches the bad-curve problem above: integrate
the thrust curve and compare it with the motor's name.

Every number comes back with a receipt: which backend made it, which wind seed, and the sha256 of the exact file.
Edits always go to a new file. It never touches your original.

Here it is trying to make a rocket faster: [demo transcript](docs/demo-transcript.md). It tried more fin sweep,
flew both versions, found +0.007 Mach and a stability drop below the goal, and said not to bother.
That's the whole idea.

It doesn't sign off on flights. OpenRocket, RASAero II, your club and your RSO do that.

## Tested

- Installs in Claude Desktop from the `.mcpb` bundle on Windows.
- `inspect_design`, `edit_design` and `judge_design` used for real from a Claude Desktop chat.
- On the reference design, the judge's numbers match OpenRocket 24.12 exactly. OpenRocket does the flying; Ogive
  only reads the result, and the tests pin it.
- 189 automated tests, run on every push with Python 3.11 and 3.14.

## An example

I had a model with a guessed tube length. I found the real length, had Ogive edit it, then built best and worst
case variants for a wall thickness I didn't know yet. The judge passed one and failed the other on ground-hit
speed. Now I know what to measure before I trust either.

## What it can't do yet

- Windows only.
- Needs OpenRocket 24.12. I haven't tried other versions.
- The flutter check is a rough screen for a flat plate. It assumes a fin shear modulus of 3 GPa, on the safe side,
  until you give it a real one from a datasheet or a test.
- Above Mach 1, OpenRocket's aerodynamics are approximate. Ogive warns you; cross-check in RASAero II.
- One wind seed per run. No Monte Carlo yet.
- It can change numbers and add masses. It can't swap motors or add parts yet.
- The optimizer only sweeps fin height and nose weight.
- The first call takes about 10 s while Java starts. Then about 1 s each.

If it breaks on your rocket, [open an issue](https://github.com/michaelsheffernan/ogive/issues), with the `.ork` if you can share it.
Found a security problem? See [SECURITY.md](SECURITY.md).

## Setup

You need Windows 10 or 11 and [OpenRocket 24.12](https://openrocket.info/). Ogive uses OpenRocket's own engine and
the Java that comes with it.

**Claude Desktop:** run `.\tasks.ps1 mcpb` (needs Node.js), open `out\ogive-0.1.1.mcpb` with Claude Desktop, and
pick your rockets folder. Ogive only reads and writes inside it, and relative paths mean that folder.
The first launch takes a minute or so while it installs Python packages.

**Claude Code** (needs Python 3.11+ and git):

    git clone https://github.com/michaelsheffernan/ogive
    cd ogive
    .\tasks.ps1 setup

Run `claude` in that folder and approve the `ogive` server. To use it from anywhere:

    claude mcp add --scope user ogive -- C:\path\to\ogive\.venv\Scripts\python.exe -m ogive.server

Add `-e OPENROCKET_HOME=...` if OpenRocket isn't in `C:\Program Files\OpenRocket`, and `-e OGIVE_ROOT=C:\Rockets`
to keep Ogive inside one folder.

If PowerShell won't run the scripts: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## Troubleshooting

**The first install in Claude Desktop times out.** It downloads a few Python packages, and the biggest
(`pydantic-core`) can hit uv's default 30 s timeout on a slow connection. Retry. If it keeps failing, run

    setx UV_HTTP_TIMEOUT 300

then quit Claude Desktop completely (not just the window) and open it again. The 0.1.1 bundle sets this timeout
itself, so you should only need it with older bundles.

**"No OpenRocket install"**: Ogive looks in `C:\Program Files\OpenRocket`. Point it at another folder in the
bundle's settings (Claude Desktop) or with `OPENROCKET_HOME` (Claude Code).

## Things to try

- "Judge `my_rocket.ork` on its first sim. What fails, and what's assumed?"
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

    ogive tree my_rocket.ork
    ogive sim my_rocket.ork --sim "Simulation 1" --seed 42 --csv out\flight.csv
    ogive judge my_rocket.ork --sim "Simulation 1"
    ogive optimize my_rocket.ork --sim "Simulation 1" --fins 30:45:1 --nose 0:30:5
    ogive set my_rocket.ork --component "Trapezoidal Fin Set" --field height --value 0.030 -o out\fin30.ork

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
