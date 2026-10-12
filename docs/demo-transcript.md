# Demo: "make it faster", with proof

A real Claude Code session using Ogive's MCP server, rendered from its log by `scripts/render_transcript.py`. Claude Code 2.1.296, model `claude-opus-5-5`, headless (`claude -p`), Ogive tools only. 6 turns, 116 s.

The prompt and the model's words are verbatim. Tool calls are shown in full. Tool results are abridged to their key fields. Paths are shortened to the repo root.

Note added after rendering: the session ran on a copy of `data/blackhawk29_v2.ork` before its `<designer>` field
was anonymised, so the sha256 below is `52f11f283b2d…`. The repo's copy differs only in that one XML element
(sha256 `a32d2d95e730…`). Re-judging it gives bit-identical numbers.

## User

> I want our Blackhawk 29 (out/demo/blackhawk29_v2.ork) to go faster. Use the Ogive tools. Judge it on the 'Para + Streamer Test' simulation, try one fin change that should raise max Mach, prove it with compare_designs, and tell me whether to take it. Every number must come from a tool result.

*(Claude Code loads the Ogive tool schemas.)*

**Tool call:** `inspect_design`

```json
{
  "file": "out/demo/blackhawk29_v2.ork"
}
```

**Result** (`inspect_design`, abridged):

```
10 components: blackhawk29_v2.ork, Sustainer, Nose Cone, Body Tube, Trapezoidal Fin Set, Av-bay + electronics, Recovery, Tracker, Parachute, Streamer
Trapezoidal Fin Set: instancecount 3, fincount 3, radiusoffset 0, angleoffset 0, rotation 0, thickness 0.0025, cant 0, filletradius 0.0025, rootchord 0.07, tipchord 0.025, sweeplength 0.045, height 0.032
motor: 229H255-14A (curve 39f13f3e)
provenance: Ogive ork_io 0.1.0, ogive 0.1.0, sha256 52f11f283b2d…, out\demo\blackhawk29_v2.ork
```

**Tool call:** `judge_design`

```json
{
  "file": "out/demo/blackhawk29_v2.ork",
  "simulation": "Para + Streamer Test"
}
```

**Result** (`judge_design`, abridged):

```
passed: True  {'pass': 12, 'warn': 2, 'fail': 0, 'pending': 0}
apogee 1902.2 m, max velocity 524.0 m/s, max Mach 1.5574, launch mass 420.4 g
  PASS  stability.static: 2.64 cal (limit ≥ 1.5 (goal 2.0))
  PASS  stability.ascent: min 2.00 cal (limit ≥ 1.5 (goal 2.0))
  PASS  launch.rod_velocity: 46.7 m/s (limit ≥ 15 m/s)
  PASS  flutter.margin: 1.66x (limit ≥ 1.5x)
  PASS  recovery.ground_hit: 9.04 m/s (limit ≤ 10 m/s)
  WARN  recovery.deployment: Recovery device deployment at high speed (28.9 m/s) (limit OpenRocket's own warning)
  PASS  motor.designation: 229.0 Ns, 257 N (limit 229 Ns, 255 N within 5%)
  PASS  stored.burn_time: 0.936 s (limit 0.898 s ± 0.1)
  PASS  stored.current: off by 0.0% (limit ≤ 2%)
  PASS  sanity.rocket_equation: 524.0 m/s (limit ≤ 736.0 m/s)
  PASS  sanity.mass: 188.4 + 232.0 g (limit = 420.4 g)
  PASS  confidence.fineness: L/D 26.0 (limit ≥ 10)
  WARN  confidence.supersonic: M1.56 (limit ≤ M1)
  PASS  sanity.mass_override: none (limit override covers subcomponents)
provenance: OpenRocket 24.12, seed 0, ogive 0.1.0, sha256 52f11f283b2d…, out\demo\blackhawk29_v2.ork
```

**Tool call:** `edit_design`

```json
{
  "file": "out/demo/blackhawk29_v2.ork",
  "changes": [
    {
      "component": "Trapezoidal Fin Set",
      "field": "sweeplength",
      "value": 0.055
    }
  ]
}
```

**Result** (`edit_design`, abridged):

```
output: out\demo\ogive\blackhawk29_v2-001.ork
stored sims marked outdated: 4
  Trapezoidal Fin Set.sweeplength: 0.045 -> 0.055
provenance: Ogive ork_io 0.1.0, ogive 0.1.0, sha256 24fb0a346f4e…, out\demo\ogive\blackhawk29_v2-001.ork
```

**Tool call:** `compare_designs`

```json
{
  "base": "out/demo/blackhawk29_v2.ork",
  "candidate": "out/demo/ogive/blackhawk29_v2-001.ork",
  "simulation": "Para + Streamer Test"
}
```

**Result** (`compare_designs`, abridged):

```
design diff:
  Trapezoidal Fin Set.sweeplength: 0.045 -> 0.055
metrics (base -> candidate, change):
  apogee: 1902.2151 -> 1932.4577 (+30.2426)
  max_velocity: 524.0031 -> 526.3356 (+2.3324)
  max_mach: 1.5574 -> 1.5642 (+0.0068)
  launch_mass: 0.4204 -> 0.4204 (+0.0000)
  dry_mass: 0.1884 -> 0.1884 (-0.0000)
verdicts:
  stability.static: pass 2.64 cal -> pass 2.62 cal
  CHANGED stability.ascent: pass min 2.00 cal -> warn min 1.95 cal
  launch.rod_velocity: pass 46.7 m/s -> pass 46.7 m/s
  flutter.margin: pass 1.66x -> pass 1.65x
  recovery.ground_hit: pass 9.04 m/s -> pass 9.02 m/s
  recovery.deployment: warn Recovery device deployment at high speed (28.9 m/s) -> warn Recovery device deployment at high speed (28.9 m/s)
  motor.designation: pass 229.0 Ns, 257 N -> pass 229.0 Ns, 257 N
  CHANGED stored.burn_time: pass 0.936 s -> pending —
  CHANGED stored.current: pass off by 0.0% -> pending —
  sanity.rocket_equation: pass 524.0 m/s -> pass 526.3 m/s
  sanity.mass: pass 188.4 + 232.0 g -> pass 188.4 + 232.0 g
  confidence.fineness: pass L/D 26.0 -> pass L/D 26.3
  confidence.supersonic: warn M1.56 -> warn M1.56
  sanity.mass_override: pass none -> pass none
base passed True, candidate passed True
base: provenance: OpenRocket 24.12, seed 0, ogive 0.1.0, sha256 52f11f283b2d…, out\demo\blackhawk29_v2.ork
candidate: provenance: OpenRocket 24.12, seed 0, ogive 0.1.0, sha256 24fb0a346f4e…, out\demo\ogive\blackhawk29_v2-001.ork
```

## Claude

Don't take it. The fin change made the rocket very slightly faster, but it made it less stable and the gain is too small to trust.

**What I changed:** fin sweep from 45 mm to 55 mm, nothing else. My reasoning was that a more swept leading edge cuts drag at supersonic speed. The new file is `out/demo/ogive/blackhawk29_v2-001.ork`; the original is untouched.

**Results** from `compare_designs` on "Para + Streamer Test" (OpenRocket 24.12, seed 0):

| | Original | 55 mm sweep | Change |
|---|---|---|---|
| Max Mach | 1.5574 | 1.5642 | +0.0068 |
| Max velocity | 524.0 m/s | 526.3 m/s | +2.3 |
| Apogee | 1902.2 m | 1932.5 m | +30.2 |
| Lowest stability during ascent | 2.00 cal (pass) | 1.95 cal (**warn**) | −0.05 |
| Stability at Mach 0.3 | 2.64 cal | 2.62 cal | −0.02 |
| Flutter margin | 1.66x | 1.65x | −0.01 |
| Length / diameter | 26.0 | 26.3 | +0.3 |
| Launch mass | 420.4 g | 420.4 g | 0 |

Neither design has a FAIL.

**Why not:**
1. **The gain is about 0.4% in Mach.** The judge still warns that OpenRocket is less reliable above Mach 1 and drag should be cross-checked in RASAero II. A 0.007 Mach difference is smaller than that uncertainty.
2. **It costs a verdict.** Lowest ascent stability fell from 2.00 to 1.95 cal, below the 2.0 goal. That turns a pass into a warn.
3. **The fin tips now stick out behind the trailing edge of the fin root.** 55 mm sweep plus a 25 mm tip chord is 80 mm, against a 70 mm root, so the tips sit 10 mm further back. The length/diameter rise from 26.0 to 26.3 fits this. If the fins are flush with the aft end of the tube, the tips now hang past it and take the landing hits.

**My prediction was wrong.** I expected more sweep to raise stability, but it dropped. My best explanation, which no tool tested: the extra sweep makes the fins produce less lift for a given angle, and moves the fins' mass slightly toward the tail. Together these outweigh the centre of pressure moving back.

**Things the judge reported that still need you:**
- Flutter assumes a fin shear modulus of 3 GPa (the real value could be 3–17 GPa). Replace it with a datasheet or measured value.
- Both designs warn that the recovery device deploys at 28.9 m/s. Whether that's acceptable is the team's call.
- The two checks on stored results show "pending" for the new file. That's expected, because editing marks the stored sims as outdated.

**Next test, if you want more speed:** run `optimize_fins_and_nose_weight`. It sweeps fin size and nose weight together and judges every variant, which is better than guessing one change at a time. Don't make the fins thinner: at 1.65x the flutter margin is close to the 1.5x limit.

Ogive is advisory only. OpenRocket and RASAero II sign off on real flights.
