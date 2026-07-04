"""Isotopic fingerprinting — matching IRMS samples to ancient quarries.

Each reference quarry is modelled as an independent Gaussian per isotope
system (diagonal covariance), built from literature-informed centroids:

* delta 13C  [permil, VPDB]  — carbonate carbon;
* delta 18O  [permil, VPDB]  — carbonate oxygen;
* 87Sr/86Sr  [ratio]         — radiogenic strontium.

Matching is a nearest-neighbour search in per-quarry sigma space: the
sample is z-scored against each quarry's own spread, giving a Mahalanobis
distance d^2 = sum(z_i^2). Ranking uses the Gaussian log-likelihood
(-d^2/2 - sum ln sigma), and the reported confidence is the posterior
probability of each quarry given a uniform prior over the registry
(a softmax of the likelihoods). Because that confidence is *relative*,
an absolute chi-square consistency p-value (d^2 against len(axes) dof)
is reported alongside: a sample alien to every registry quarry keeps a
"best" match but is flagged implausible.

Reference values are demo-grade centroids assembled from published marble
provenance datasets (Craig & Craig 1972; Gorgoni et al. 2002) and typical
volcanic/basement Sr signatures — adequate for engine validation, not a
substitute for a laboratory reference database.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import chi2

from .stratigraphy import PalimpsestError

CONSISTENCY_ALPHA = 0.01  # chi-square p below this marks a match implausible

ISOTOPE_AXES = ("delta13c", "delta18o", "sr8786")


class ProvenanceError(PalimpsestError):
    """Base class for provenance-engine errors."""


class UnknownQuarryError(ProvenanceError):
    """A request references a quarry id absent from the baseline registry."""


@dataclass(frozen=True)
class QuarrySignature:
    """One reference quarry: location plus isotopic mean/spread per axis."""

    quarry_id: str
    name: str
    material: str          # marble | volcanic_ash | granite
    lat: float
    lon: float
    delta13c: float        # mean [permil VPDB]
    delta13c_sd: float
    delta18o: float        # mean [permil VPDB]
    delta18o_sd: float
    sr8786: float          # mean [ratio]
    sr8786_sd: float

    def mean(self, axis: str) -> float:
        return float(getattr(self, axis))

    def sd(self, axis: str) -> float:
        return float(getattr(self, axis + "_sd"))


QUARRY_REGISTRY: dict[str, QuarrySignature] = {
    q.quarry_id: q
    for q in (
        QuarrySignature("carrara", "Carrara (Luni)", "marble", 44.08, 10.10,
                        2.05, 0.25, -1.85, 0.45, 0.70780, 0.00015),
        QuarrySignature("paros", "Paros (Lychnites)", "marble", 37.05, 25.15,
                        4.60, 0.40, -2.80, 0.70, 0.70850, 0.00020),
        QuarrySignature("pentelicon", "Mount Pentelicus", "marble", 38.08, 23.90,
                        2.70, 0.25, -7.20, 0.80, 0.70810, 0.00020),
        QuarrySignature("naxos", "Naxos (Apollonas)", "marble", 37.10, 25.45,
                        2.10, 0.45, -6.20, 1.00, 0.70910, 0.00030),
        QuarrySignature("thasos", "Thasos (Aliki)", "marble", 40.62, 24.73,
                        3.40, 0.30, -0.90, 0.50, 0.70770, 0.00015),
        QuarrySignature("proconnesos", "Proconnesos (Marmara)", "marble", 40.58, 27.55,
                        2.55, 0.30, -2.40, 0.60, 0.70820, 0.00020),
        QuarrySignature("docimium", "Docimium (Iscehisar)", "marble", 38.85, 30.75,
                        2.30, 0.35, -4.30, 0.80, 0.70830, 0.00020),
        QuarrySignature("vesuvius", "Mount Vesuvius (pozzolana)", "volcanic_ash", 40.82, 14.43,
                        -5.00, 2.00, -8.00, 2.50, 0.70745, 0.00030),
        QuarrySignature("aswan", "Aswan (Syene granite)", "granite", 24.09, 32.90,
                        -3.00, 1.50, -10.00, 2.00, 0.71200, 0.00300),
    )
}


@dataclass
class QuarryMatch:
    """One ranked candidate origin for the unknown sample."""

    quarry_id: str
    name: str
    material: str
    lat: float
    lon: float
    distance_sigma: float          # sqrt(sum z^2), sigma units
    confidence_pct: float          # posterior probability across the registry
    consistency_p: float           # absolute chi-square goodness of fit
    plausible: bool                # consistency_p >= CONSISTENCY_ALPHA
    z_scores: dict[str, float]     # per-axis standardized residuals
    axes_used: list[str]


class IsotopeMatcher:
    """Nearest-neighbour matcher over the quarry baseline registry."""

    def __init__(self, registry: dict[str, QuarrySignature] | None = None) -> None:
        self.registry = registry if registry is not None else QUARRY_REGISTRY
        if not self.registry:
            raise ValueError("quarry registry is empty")

    def get(self, quarry_id: str) -> QuarrySignature:
        try:
            return self.registry[quarry_id]
        except KeyError:
            raise UnknownQuarryError(
                f"unknown quarry id '{quarry_id}'; known: {sorted(self.registry)}",
                detail={"unknown": quarry_id, "known": sorted(self.registry)},
            ) from None

    def match(
        self,
        delta13c: float | None,
        delta18o: float | None,
        sr8786: float | None,
        *,
        top_k: int = 3,
    ) -> list[QuarryMatch]:
        """Rank registry quarries against the sample, best first.

        Axes with a None measurement are dropped for every quarry alike,
        so partial IRMS panels (e.g. C/O only) remain comparable. At least
        two measured axes are required for a meaningful fingerprint.
        """
        sample = {"delta13c": delta13c, "delta18o": delta18o, "sr8786": sr8786}
        axes = [a for a in ISOTOPE_AXES if sample[a] is not None]
        if len(axes) < 2:
            raise ValueError(
                "at least two isotope systems are required for a match "
                f"(got {axes or 'none'})"
            )

        rows = []
        for q in self.registry.values():
            z = {a: (sample[a] - q.mean(a)) / q.sd(a) for a in axes}
            d2 = sum(v * v for v in z.values())
            log_like = -0.5 * d2 - sum(np.log(q.sd(a)) for a in axes)
            rows.append((q, z, d2, log_like))

        # Posterior over the registry (uniform prior), via log-sum-exp.
        likes = np.array([r[3] for r in rows])
        posterior = np.exp(likes - likes.max())
        posterior /= posterior.sum()

        matches = [
            QuarryMatch(
                quarry_id=q.quarry_id,
                name=q.name,
                material=q.material,
                lat=q.lat,
                lon=q.lon,
                distance_sigma=round(float(np.sqrt(d2)), 3),
                confidence_pct=round(float(p) * 100.0, 1),
                consistency_p=round(float(chi2.sf(d2, df=len(axes))), 4),
                plausible=bool(chi2.sf(d2, df=len(axes)) >= CONSISTENCY_ALPHA),
                z_scores={a: round(v, 3) for a, v in z.items()},
                axes_used=list(axes),
            )
            for (q, z, d2, _), p in zip(rows, posterior)
        ]
        matches.sort(key=lambda m: (-m.confidence_pct, m.distance_sigma, m.quarry_id))
        return matches[: max(1, top_k)]
