"""Simulations stored inside an .ork file: status, launch conditions, summary results.

The status only means something if the file was saved WITH simulation data.
OpenRocket 24.12 writes status="notsimulated" for every sim when it saves without
simulation data, but still stores the summary (OpenRocketSaver.java line 324, tag
release-24.12). So a summary with that status has unknown freshness, not stale.
Stored numbers are hints. Ogive treats a number as a result only after re-running or verifying it.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

CURRENT = "current"  # OpenRocket said up to date at save time
OUTDATED = "outdated"  # design or settings changed after the run
UNKNOWN = "unknown"  # saved without sim data, or a status we don't interpret
NEVER_RUN = "never run"


@dataclass(frozen=True)
class StoredSimulation:
    name: str
    status: str
    conditions: dict[str, str]  # raw text, e.g. {"launchrodlength": "1.0", "atmosphere": "isa"}
    flight_data: dict[str, float]  # summary, e.g. {"maxvelocity": 577.8, "maxaltitude": 2238.226}

    @property
    def freshness(self) -> str:
        if self.status == "uptodate":
            return CURRENT
        if self.status == "outdated":
            return OUTDATED
        if self.status == "notsimulated":
            return UNKNOWN if self.flight_data else NEVER_RUN
        return UNKNOWN

    @classmethod
    def from_element(cls, element: ET.Element) -> StoredSimulation:
        return cls(
            name=(element.findtext("name") or "").strip(),
            status=element.get("status", ""),
            conditions=_read_conditions(element.find("conditions")),
            flight_data=_read_flight_data(element.find("flightdata")),
        )


def _read_conditions(element: ET.Element | None) -> dict[str, str]:
    """Simple <tag>text</tag> fields, plus the atmosphere model and its settings."""
    if element is None:
        return {}
    conditions: dict[str, str] = {}
    for child in element:
        if len(child) == 0 and child.text and child.text.strip():
            conditions[child.tag] = child.text.strip()
    atmosphere = element.find("atmosphere")
    if atmosphere is not None:
        conditions["atmosphere"] = atmosphere.get("model", "")
        for setting in atmosphere:
            conditions[f"atmosphere.{setting.tag}"] = (setting.text or "").strip()
    return conditions


def _read_flight_data(element: ET.Element | None) -> dict[str, float]:
    if element is None:
        return {}
    data: dict[str, float] = {}
    for key, raw in element.attrib.items():
        try:
            data[key] = float(raw)
        except ValueError:
            continue  # non-numeric attributes carry no summary result
    return data
