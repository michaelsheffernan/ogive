"""Judge results: one Verdict per rule, collected in a Report."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

PASS, WARN, FAIL, PENDING = "pass", "warn", "fail", "pending"
DEFAULT_RULES = Path(__file__).with_name("rules.toml")


@dataclass(frozen=True)
class Verdict:
    rule: str  # id, e.g. "stability.ascent"
    title: str  # plain English, e.g. "Stability through the ascent"
    state: str  # PASS / WARN / FAIL / PENDING
    value: str  # what was measured, formatted with units
    limit: str  # the threshold it was judged against
    source: str  # the backend that produced the value
    detail: str = ""
    assumptions: tuple[str, ...] = field(default_factory=tuple)
    measured: float | None = None  # the raw number behind `value`, for tools (SI or calibers)


@dataclass(frozen=True)
class Report:
    file: str
    simulation: str | None
    seed: int | None
    backend: str
    verdicts: list[Verdict]
    # Key numbers of the judged flight, SI units, all from `backend`: apogee, max_velocity,
    # max_mach, launch_mass, dry_mass.
    metrics: dict[str, float] = field(default_factory=dict)

    def count(self, state: str) -> int:
        return sum(1 for v in self.verdicts if v.state == state)

    @property
    def passed(self) -> bool:
        """True when no rule failed. Warnings and pending rules don't block."""
        return self.count(FAIL) == 0

    def verdict(self, rule: str) -> Verdict:
        matches = [v for v in self.verdicts if v.rule == rule]
        if len(matches) != 1:
            raise KeyError(f"{len(matches)} verdicts for rule {rule!r}")
        return matches[0]


def load_rules(path: str | Path | None = None) -> dict:
    """The default rules, with any values from `path` overriding them section by section."""
    with open(DEFAULT_RULES, "rb") as f:
        rules = tomllib.load(f)
    if path is not None:
        with open(path, "rb") as f:
            for section, values in tomllib.load(f).items():
                rules.setdefault(section, {}).update(values)
    return rules
