# Case study: the 229H255 that was really a 315H255

**Short version.** Our Blackhawk 29 simulation promised Mach 1.70 and 2,238 m on a Cesaroni 229H255.
Physics said that was impossible. The thrust curve OpenRocket used was another motor's data, filed under the
229H255's name. The correct curve cut the prediction to Mach 1.55 and 1,924 m, and changed the design: the
40 mm fins and 10 g nose weight had been compensating for a motor we were never going to fly.
The bad file was gone from ThrustCurve the same day I reported it. OpenRocket 24.12 still ships it.
Ogive's judge now catches this class of error on every run.

All numbers below come from OpenRocket 24.12 run headless through Ogive, seed 0, re-run on 2026-10-11, unless
labelled otherwise. The files are in this repo, frozen and hash-pinned (see [Reproduce](#reproduce)).

## 1. The red flag

The v1 design file stored a flight with 577.8 m/s at 506.8 g liftoff on a "229H255-14A".

I checked it against the rocket equation, using the published motor data (229.3 Ns, 104 g propellant).
Burnout mass is 506.8 − 104 = 402.8 g. Even with no drag and no assumption about Isp, a rocket can't gain more
speed than total impulse over burnout mass:

    v_max ≤ I / m_burnout = 229 Ns / 0.4028 kg ≈ 569 m/s

An RK4 integrator cannot beat that with its own inputs. So the inputs OpenRocket used were not the ones the
label promised.

## 2. Two hypotheses, and the test that split them

Predictions, written down before the test:

| Observation | H1 (mine): the motor data is wrong | H2 (the other theory): stale results from another motor |
|---|---|---|
| Design view, mass with motor | 507 g | about 421 g (189 + 232) |
| Show Details for the selected curve | launch mass 318 g, or propellant not about 104 g | about 229 Ns, 232 g, 0.9 s burn |

I opened OpenRocket's Show Details: **316 Ns** under the 229H255 name. H1 held. H2 was wrong.
We had also misread one piece of evidence. OpenRocket writes the status
`notsimulated` for every sim when a file is saved without simulation data, so that status says nothing about
freshness.

Lesson: evidence that fits both hypotheses doesn't rank them. Design the test that tells them apart first.

## 3. Root cause: a file under the wrong motor

OpenRocket's motor database comes from ThrustCurve.org. Every data file attached to both H255 motors, pulled from
the ThrustCurve API and integrated (trapezoid rule), checked 2026-10-11 (morning):

| ThrustCurve simfile | Attached to | Header says | Integrated impulse | Burn |
|---|---|---|---|---|
| `…0627` RASP | 229H255-14A | H255-WT, 231 mm, 232 g | 229.0 Ns | 0.936 s |
| `…05ee` RockSim | 229H255-14A | **H255-BS**, 320 mm, 318 g | **316.1 Ns** | **1.252 s** |
| `…0628` RASP | 315H255-14A | H255-BS, 320 mm, 318 g | 316.1 Ns | 1.252 s |
| `…060b` RockSim | 315H255-14A | H255-BS | 316.1 Ns | 1.252 s |

`…05ee` was **byte-identical** to `…060b` (SHA-256 `ea82afe07b6bab94…`): the 315H255's file, attached to the
229H255. ThrustCurve's own page for it already said "File is usable, but has some problems" (+38 % impulse vs
cert), but the file stayed attached. OpenRocket issue
[#2348](https://github.com/openrocket/openrocket/issues/2348) (2023) reported the symptom. It was closed as
upstream data, with the guess that the two curves were two test firings.

In OpenRocket, the motor chooser offers both curves for "229H255-14A". v1 had picked the bad one (curve digest
`6e3ee79b…`). Whatever Show Details says, the designation in the file still reads "229H255-14A".

## 4. What it did to the design

Both files flown in the same simulation, OpenRocket's defaults ("Simulation 1": 1.0 m rod, ISA, Cape Canaveral):

| | v1 (bad curve) | v2 (correct curve) |
|---|---|---|
| Curve (digest) | `6e3ee79b…`: 316.1 Ns, 318 g motor, 1.20 s burn | `39f13f3e…`: 229.0 Ns, 232 g motor, 0.89 s burn |
| Design | 40 mm fins + 10 g nose weight | 32 mm fins, no nose weight |
| Liftoff mass | 506.8 g | 420.4 g |
| Max velocity | 577.8 m/s (M1.704) | 524.6 m/s (M1.546) |
| Apogee | 2,237.9 m | 1,923.5 m |
| Stability at M0.30 (design view) | 2.00 cal | 2.64 cal |

The bad curve's motor was 86 g heavier and 89 mm longer, all at the tail. That moved the CG aft, so the team had
added fin area and nose weight to hold 2 calibers. On the correct curve, the original build-plan geometry
(32 mm fins, no nose weight) gives 2.64 cal. The extra fin and nose mass was solving a problem that didn't exist.

It also hid in places nobody looked. The v2 file holds the correct curve, but two of its stored simulations
("Streamer SIM", "Parachute SIM") still carried numbers from the bad one: implied burn 1.25 s, 576 m/s.
Nothing in the file or the status shows this. Only a physics cross-check does.

## 5. How Ogive catches it now

`ogive judge` flies the simulation in OpenRocket and applies 14 rules. Two of them were written for this case:

- **Thrust curve matches its designation.** "229H255" promises 229 Ns total and 255 N average. Integrate the curve
  the backend actually loaded, and fail beyond 5 %.
- **Stored sim was run on this motor.** Implied burn time = time to apogee − optimum delay (stored by OpenRocket)
  must match I/F from the designation within 0.1 s.

On v1 (`data/regression/heloria_blackhawk29_v1.ork`, "Simulation 1"):

```
  FAIL   Thrust curve matches its designation
         316.1 Ns, 264 N   (limit 229 Ns, 255 N within 5%)   [OpenRocket 24.12 motor database]
         impulse +38.1%, average thrust +3.4%. This curve belongs to a different motor
  FAIL   Stored sim was run on this motor (burn time check)
         1.252 s   (limit 0.898 s ± 0.1)   [stored summary in the .ork]
```

Same run, three more FAILs that nobody had looked for:

```
  FAIL   Stability through the ascent (rod clearance to ejection)
         min 1.28 cal   (limit ≥ 1.5 (goal 2.0))   [OpenRocket 24.12 flight]
         lowest at t = 0.07 s (M0.12); highest 4.52 cal
  FAIL   Fin flutter margin (Martin, NACA TN 4197, corrected)
         1.13x   (limit ≥ 1.5x)   [OpenRocket 24.12 flight + Ogive flutter]
         worst at t = 1.07 s, 578 m/s. G 3 GPa: 1.13x; G 5 GPa: 1.45x; G 17 GPa: 2.68x
         ASSUMPTION: fin shear modulus ASSUMED 3 GPa (range 3-17); replace with a datasheet or measured value
  FAIL   Ground hit speed (total, as OpenRocket 24.12 reports it)
         18.80 m/s   (limit ≤ 10 m/s)   [OpenRocket 24.12 flight]

  FAILED: 7 pass, 2 warn, 5 fail, 0 pending
```

The 2.00 cal in the design view hid 1.28 cal just after the rod. The 40 mm fins at 578 m/s fail the flutter
screen even at 5 GPa. v2 on its reference simulation fails none of the 14 rules: 12 pass, 2 warnings (the chute deploys
at 28.9 m/s; the flight is supersonic, so cross-check in RASAero II).

Note what the rocket-equation rule says about v1: PASS. With the curve OpenRocket actually loaded (316 Ns, 318 g),
577.8 m/s is consistent. The inconsistency was never inside the simulation. It sat between the label and the
data, and that is why the designation check exists.

## 6. Upstream

- **ThrustCurve:** I emailed the maintainer on 2026-10-11 at 17:08 UTC. The maintainer replied at 19:09 UTC, thanking
  us. By 20:17 UTC, simfile `…05ee` returned 404, and the 229H255-14A's page and API listed only the RASP
  curve `…0627`. The 315H255-14A keeps both its files.
- **OpenRocket 24.12** ships its own copy of the motor database. It still offers the 316 Ns curve under
  "229H255-14A" (Ogive's tests pin that digest). On 24.12, pick the RASP curve (229 Ns, 0.94 s burn) in the
  motor dialog's thrust-curve dropdown.
- **Proposed for OpenRocket:** integrate each curve on import and warn when it misses its designation, the same
  rule as Ogive's judge.

## 7. Lessons

1. Check simulator output against hard physical bounds. The rocket equation needs four numbers and catches
   impossible results.
2. A thrust curve's name is a claim. Integrate the curve and compare.
3. Stored results don't record what produced them. Re-run before trusting.
4. Design compensations can hide data errors. Here the wrong motor set the fin size and the nose weight.
5. Rank hypotheses with tests that tell them apart, not with evidence that fits both.

## Reproduce

Needs Windows, OpenRocket 24.12 and Ogive (see the README).

    ogive judge data\regression\heloria_blackhawk29_v1.ork --sim "Simulation 1"
    ogive judge data\regression\blackhawk29_v2_locked.ork --sim "Para + Streamer Test"
    ogive sim data\regression\heloria_blackhawk29_v1.ork --sim "Simulation 1" --seed 0

| File | SHA-256 |
|---|---|
| `data/regression/heloria_blackhawk29_v1.ork` | `c5aec7642f3fed385b258e3288de68325bd686897d54e4d49f02d25ea0c8c0cc` |
| `data/regression/blackhawk29_v2_locked.ork` | `a32d2d95e730fb957d5d18b91231acdc0dddea5b8003d840de5e00e0b707c307` |

ThrustCurve checks: `POST https://www.thrustcurve.org/api/v1/search.json` with
`{"commonName": "H255", "manufacturer": "Cesaroni"}`, then `POST /api/v1/download.json` with the motor id and
`"data": "file"`. The test suite asserts both curves' digests and impulses
(`tests/test_openrocket_crosscheck.py`).

Advisory only: OpenRocket and RASAero II sign off on real flights.
