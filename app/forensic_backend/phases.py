"""Phase matching against the local COD reference matrix.

For every reference phase, each theoretical line is cross-correlated with
the detected peak list within an angular tolerance. Confidence blends:

  * coverage   — how much of the phase's total reference intensity is
                 accounted for by matched lines, with each match discounted
                 by its angular error (linear taper to 0 at the tolerance);
  * similarity — cosine similarity between observed heights of matched
                 lines and their theoretical relative intensities.

A phase whose primary (100%) line is missing is heavily penalised: a real
phase can lose minor lines in the noise floor, but never its strongest one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from .peaks import DetectedPeak, FittedPeak

_DB_PATH = Path(__file__).with_name("cod_reference.json")

COVERAGE_WEIGHT = 0.7
SIMILARITY_WEIGHT = 0.3
MISSING_PRIMARY_PENALTY = 0.25


@dataclass
class LineMatch:
    ref_two_theta: float
    ref_rel_intensity: float
    hkl: str
    observed_two_theta: float
    delta_deg: float
    observed_height: float
    observed_area: float


@dataclass
class PhaseMatch:
    phase_id: str
    name: str
    formula: str
    rir: float
    ca_mass_fraction: float
    is_silica: bool
    confidence_pct: float
    matched_lines: list[LineMatch]

    @property
    def strongest_area(self) -> float:
        return max((m.observed_area for m in self.matched_lines), default=0.0)


@lru_cache(maxsize=1)
def load_reference_db() -> dict:
    with _DB_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def _refined_positions(
    peaks: list[DetectedPeak], fitted: list[FittedPeak]
) -> list[tuple[float, float, float]]:
    """(two_theta, height, area) per peak, preferring fitted centres/areas."""
    by_index = {f.detected.index: f for f in fitted if f.detected is not None}
    rows = []
    for p in peaks:
        fit = by_index.get(p.index)
        if fit is not None:
            rows.append((fit.two_theta, p.height, fit.area))
        else:
            rows.append((p.two_theta, p.height, p.area))
    return rows


def match_phases(
    peaks: list[DetectedPeak],
    fitted: list[FittedPeak],
    *,
    tolerance_deg: float = 0.25,
    min_confidence_pct: float = 0.0,
) -> list[PhaseMatch]:
    db = load_reference_db()
    observed = _refined_positions(peaks, fitted)
    results: list[PhaseMatch] = []

    for phase in db["phases"]:
        lines = phase["lines"]
        total_ref_intensity = sum(l["rel_intensity"] for l in lines)
        primary_intensity = max(l["rel_intensity"] for l in lines)

        matches: list[LineMatch] = []
        weighted_coverage = 0.0
        primary_matched = False
        claimed: set[int] = set()  # one observed peak can satisfy one line

        for line in sorted(lines, key=lambda l: -l["rel_intensity"]):
            best_i, best_delta = -1, tolerance_deg
            for i, (pos, _h, _a) in enumerate(observed):
                if i in claimed:
                    continue
                delta = abs(pos - line["two_theta"])
                if delta <= best_delta:
                    best_i, best_delta = i, delta
            if best_i < 0:
                continue
            claimed.add(best_i)
            pos, height, area = observed[best_i]
            position_weight = 1.0 - best_delta / tolerance_deg
            weighted_coverage += line["rel_intensity"] * position_weight
            if line["rel_intensity"] == primary_intensity:
                primary_matched = True
            matches.append(LineMatch(
                ref_two_theta=line["two_theta"],
                ref_rel_intensity=line["rel_intensity"],
                hkl=line.get("hkl", ""),
                observed_two_theta=pos,
                delta_deg=round(pos - line["two_theta"], 4),
                observed_height=height,
                observed_area=area,
            ))

        coverage = weighted_coverage / total_ref_intensity

        if len(matches) >= 2:
            obs = np.array([m.observed_height for m in matches])
            ref = np.array([m.ref_rel_intensity for m in matches])
            similarity = float(obs @ ref / (np.linalg.norm(obs) * np.linalg.norm(ref)))
        elif len(matches) == 1:
            similarity = 0.5  # a single line carries no intensity-pattern evidence
        else:
            similarity = 0.0

        confidence = 100.0 * (COVERAGE_WEIGHT * coverage + SIMILARITY_WEIGHT * similarity)
        if matches and not primary_matched:
            confidence *= MISSING_PRIMARY_PENALTY
        if not matches:
            confidence = 0.0

        match = PhaseMatch(
            phase_id=phase["id"],
            name=phase["name"],
            formula=phase["formula"],
            rir=phase["rir"],
            ca_mass_fraction=phase["ca_mass_fraction"],
            is_silica=phase["is_silica"],
            confidence_pct=round(min(confidence, 100.0), 1),
            matched_lines=sorted(matches, key=lambda m: m.ref_two_theta),
        )
        if match.confidence_pct >= min_confidence_pct:
            results.append(match)

    results.sort(key=lambda m: -m.confidence_pct)
    return results
