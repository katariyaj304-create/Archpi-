"""Dendrochronology — crossdating a ring-width series to a master chronology.

A measured sequence of annual ring widths is dated by sliding it against a
reference (master) chronology and scoring the overlap at every candidate
end-year. Three standard statistics are computed:

* correlation r — Pearson correlation of the two *detrended* index series.
  Detrending divides each ring by a centred moving average (default 7-yr
  window), removing the low-frequency age/growth trend so only the shared
  year-to-year climate signal remains (a high-pass filter, after the
  Baillie & Pilcher 1973 approach);
* t-value       — t = r * sqrt(n - 2) / sqrt(1 - r^2), the significance of
  that correlation over n overlapping rings. t >= 3.5 is the conventional
  crossdating acceptance threshold; t >= 5 is a strong match;
* Gleichlaeufigkeit (%GLK) — the percentage of year-to-year intervals in
  which both series move in the same direction (sign agreement of first
  differences); ~50 % is chance, > 65 % supports the match.

The best-scoring position fixes the calendar year of the last measured
ring. The felling (construction-relevant) date is then derived from the
sapwood: with bark edge present it is exact; with partial sapwood it is a
range from the regional oak sapwood estimate (default 9-41 rings, Hollstein
/ Sokol range); heartwood-only samples yield a terminus post quem.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.stats import t as student_t

from .stratigraphy import PalimpsestError

T_THRESHOLD = 3.5          # Baillie-Pilcher crossdating acceptance
STRONG_T = 5.0
OAK_SAPWOOD_MIN = 9        # regional oak sapwood ring estimate (min)
OAK_SAPWOOD_MAX = 41       # regional oak sapwood ring estimate (max)
MIN_RINGS = 20             # shorter series cannot be crossdated reliably


class DendroError(PalimpsestError):
    """Base class for dendrochronology errors."""


class CrossdatingError(DendroError):
    """No calendar position of the sample meets the acceptance threshold."""


@dataclass(frozen=True)
class ReferenceChronology:
    """A master chronology: contiguous ring widths anchored to a start year."""

    chronology_id: str
    species: str
    start_year: int                     # calendar year of widths[0]
    widths: np.ndarray                  # ring widths, oldest first

    @property
    def end_year(self) -> int:
        return self.start_year + self.widths.size - 1


@dataclass
class DateCandidate:
    """One scored calendar alignment of the sample against the reference."""

    end_year: int                       # calendar year of the last sample ring
    start_year: int                     # calendar year of the first sample ring
    correlation: float
    t_value: float
    glk_pct: float
    overlap_n: int


@dataclass
class FellingEstimate:
    """Reconstructed felling date from the dated last ring plus sapwood."""

    terminus: str                       # exact | range | terminus_post_quem
    earliest: int
    latest: int | None                  # None => open-ended (post quem)
    note: str


@dataclass
class DendroResult:
    sample_id: str
    species: str
    ring_count: int
    dated: bool
    significant: bool
    best: DateCandidate
    felling: FellingEstimate
    t_margin: float                     # best t-value minus runner-up t-value
    widest_ring_year: int | None        # calendar year of the widest ring
    mean_ring_width_mm: float
    candidates: list[DateCandidate]     # ranked, best first
    detrended_sample: list[float] = field(default_factory=list)
    detrended_reference: list[float] = field(default_factory=list)


def detrend(widths: np.ndarray, window: int = 7) -> np.ndarray:
    """High-pass index series: ring width / centred moving average.

    Removes the low-frequency biological growth trend, leaving the shared
    high-frequency (climate) signal that carries the crossdating match.
    Uses the available window at the series edges. Result centres on ~1.0.
    """
    if window % 2 == 0:
        window += 1
    half = window // 2
    n = widths.size
    out = np.empty(n)
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        local_mean = widths[lo:hi].mean()
        out[i] = widths[i] / local_mean if local_mean > 0 else 1.0
    return out


def gleichlaeufigkeit(a: np.ndarray, b: np.ndarray) -> float:
    """Percentage sign-agreement of the two series' year-to-year changes."""
    da, db = np.sign(np.diff(a)), np.sign(np.diff(b))
    if da.size == 0:
        return 0.0
    return float(np.mean(da == db) * 100.0)


def _t_value(r: float, n: int) -> float:
    if n <= 2:
        return 0.0
    r = min(max(r, -0.999999), 0.999999)
    denom = np.sqrt(1.0 - r * r)
    return float(r * np.sqrt(n - 2) / denom) if denom > 0 else 0.0


class Crossdater:
    """Slides a sample over a reference chronology and scores each position."""

    def __init__(self, reference: ReferenceChronology, *, detrend_window: int = 7) -> None:
        if reference.widths.size < MIN_RINGS:
            raise ValueError("reference chronology is too short to crossdate against")
        self.reference = reference
        self.window = detrend_window
        self._ref_detrended = detrend(reference.widths, detrend_window)

    def date_series(
        self,
        widths: np.ndarray,
        *,
        top_k: int = 5,
    ) -> tuple[list[DateCandidate], np.ndarray, np.ndarray]:
        """Rank every full-overlap calendar alignment of the sample, best first.

        Returns (candidates, detrended_sample, detrended_reference_at_best).
        """
        n = widths.size
        if n < MIN_RINGS:
            raise ValueError(
                f"sample has {n} rings; at least {MIN_RINGS} are required to crossdate"
            )
        ref = self.reference
        ref_idx = self._ref_detrended
        sample_idx = detrend(widths, self.window)
        sample_c = sample_idx - sample_idx.mean()
        sample_ss = float(np.sqrt(np.sum(sample_c * sample_c)))

        candidates: list[DateCandidate] = []
        for offset in range(0, ref.widths.size - n + 1):
            window_idx = ref_idx[offset:offset + n]
            wc = window_idx - window_idx.mean()
            denom = sample_ss * float(np.sqrt(np.sum(wc * wc)))
            r = float(np.sum(sample_c * wc) / denom) if denom > 0 else 0.0
            start_year = ref.start_year + offset
            candidates.append(DateCandidate(
                end_year=start_year + n - 1,
                start_year=start_year,
                correlation=round(r, 4),
                t_value=round(_t_value(r, n), 2),
                glk_pct=round(gleichlaeufigkeit(widths, ref.widths[offset:offset + n]), 1),
                overlap_n=n,
            ))

        candidates.sort(key=lambda c: (-c.t_value, -c.glk_pct, c.end_year))
        best = candidates[0]
        best_offset = best.start_year - ref.start_year
        return (
            candidates[:max(1, top_k)],
            sample_idx,
            ref_idx[best_offset:best_offset + n],
        )


def estimate_felling(
    last_ring_year: int,
    *,
    bark_edge: bool,
    sapwood_rings: int | None,
    sapwood_complete: bool,
    sapwood_min: int = OAK_SAPWOOD_MIN,
    sapwood_max: int = OAK_SAPWOOD_MAX,
) -> FellingEstimate:
    """Derive the felling date from the dated last ring and sapwood evidence.

    * bark edge present            -> exact felling year (last ring);
    * complete sapwood, no bark    -> felling within a few years of last ring;
    * partial sapwood (s measured) -> last ring + (sapwood range minus s);
    * heartwood only               -> terminus post quem (>= last + min).
    """
    if bark_edge:
        return FellingEstimate(
            terminus="exact",
            earliest=last_ring_year,
            latest=last_ring_year,
            note="Bark edge (waney edge) present: felling in the year of the last ring.",
        )
    if sapwood_complete:
        return FellingEstimate(
            terminus="range",
            earliest=last_ring_year,
            latest=last_ring_year + 5,
            note="Complete sapwood to the heartwood/sapwood boundary but no bark; "
                 "felling within a few years of the last measured ring.",
        )
    measured = sapwood_rings or 0
    missing_min = max(0, sapwood_min - measured)
    missing_max = sapwood_max - measured
    if measured > 0:
        return FellingEstimate(
            terminus="range",
            earliest=last_ring_year + missing_min,
            latest=last_ring_year + max(missing_min, missing_max),
            note=f"{measured} sapwood rings measured; estimated total oak sapwood "
                 f"{sapwood_min}-{sapwood_max} rings gives a felling range.",
        )
    return FellingEstimate(
        terminus="terminus_post_quem",
        earliest=last_ring_year + sapwood_min,
        latest=None,
        note="Heartwood only (sapwood not preserved): felling no earlier than the "
             f"last ring + minimum {sapwood_min} sapwood rings; true date open-ended.",
    )


def analyze_sample(
    widths: np.ndarray,
    reference: ReferenceChronology,
    *,
    sample_id: str = "UNLABELLED",
    species: str = "Quercus robur",
    bark_edge: bool = False,
    sapwood_rings: int | None = None,
    sapwood_complete: bool = False,
    sapwood_min: int = OAK_SAPWOOD_MIN,
    sapwood_max: int = OAK_SAPWOOD_MAX,
    detrend_window: int = 7,
    top_k: int = 5,
) -> DendroResult:
    """Crossdate a ring-width series and reconstruct its felling date."""
    if np.any(widths <= 0):
        raise ValueError("ring widths must all be positive")

    crossdater = Crossdater(reference, detrend_window=detrend_window)
    candidates, sample_idx, ref_idx = crossdater.date_series(widths, top_k=top_k)
    best = candidates[0]
    runner_up_t = candidates[1].t_value if len(candidates) > 1 else 0.0
    t_margin = round(best.t_value - runner_up_t, 2)

    felling = estimate_felling(
        best.end_year,
        bark_edge=bark_edge,
        sapwood_rings=sapwood_rings,
        sapwood_complete=sapwood_complete,
        sapwood_min=sapwood_min,
        sapwood_max=sapwood_max,
    )
    # "Growth peak" = best climate year (strongest detrended index), not the
    # juvenile fast-growth years that dominate raw width.
    widest_year = best.start_year + int(np.argmax(sample_idx))

    return DendroResult(
        sample_id=sample_id,
        species=species,
        ring_count=widths.size,
        dated=best.t_value >= T_THRESHOLD,
        significant=best.t_value >= T_THRESHOLD,
        best=best,
        felling=felling,
        t_margin=t_margin,
        widest_ring_year=widest_year,
        mean_ring_width_mm=round(float(widths.mean()), 3),
        candidates=candidates,
        detrended_sample=[round(v, 4) for v in sample_idx.tolist()],
        detrended_reference=[round(v, 4) for v in ref_idx.tolist()],
    )


# ── Deterministic master chronology + demo core sample ──────────────────

REFERENCE_START = 850
REFERENCE_END = 1300


def build_reference_chronology(seed: int = 1251) -> ReferenceChronology:
    """A deterministic European-oak master chronology, 850-1300 AD.

    An AR(1) climate signal on a gentle biological trend, seeded so the
    demo sample (a slice of it) crossdates back to a known calendar year.
    """
    rng = np.random.default_rng(seed)
    n = REFERENCE_END - REFERENCE_START + 1
    signal = np.zeros(n)
    for i in range(1, n):
        signal[i] = 0.62 * signal[i - 1] + rng.normal(0, 1.0)
    # Baseline ~2 mm with mild long-term undulation, climate signal on top.
    baseline = 2.0 + 0.4 * np.sin(np.linspace(0, 6 * np.pi, n))
    widths = baseline * (1.0 + 0.16 * signal)
    widths = np.clip(widths, 0.15, None)
    return ReferenceChronology(
        chronology_id="EUROAK-MASTER-0850-1300",
        species="Quercus robur",
        start_year=REFERENCE_START,
        widths=np.round(widths, 3),
    )


def generate_core_sample(
    end_year: int = 1242,
    ring_count: int = 142,
    seed: int = 77,
) -> np.ndarray:
    """A synthetic core-sample series ending at `end_year` (default #012).

    Shares the master's climate signal (so it crossdates) but carries its
    own biological trend and measurement noise (so the match isn't trivial).
    """
    master = build_reference_chronology()
    start = end_year - ring_count + 1
    lo = start - master.start_year
    slice_widths = master.widths[lo:lo + ring_count].copy()

    rng = np.random.default_rng(seed)
    # Young-tree negative-exponential growth trend, then observation noise.
    age = np.arange(ring_count)
    trend = 0.7 + 1.3 * np.exp(-age / 55.0)
    noisy = slice_widths * trend * (1.0 + rng.normal(0, 0.08, ring_count))
    return np.round(np.clip(noisy, 0.15, None), 3)
