"""Data ingestion & conditioning for raw XRD diffractograms.

Pipeline: validate/sort the (2-theta, intensity) array, estimate the
background with Asymmetric Least Squares (Eilers & Boelens 2005), subtract
it, then Savitzky-Golay smooth the counting noise. Savitzky-Golay is used
precisely because it preserves peak heights and widths far better than a
moving average.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import sparse
from scipy.signal import savgol_filter
from scipy.sparse.linalg import spsolve


@dataclass
class ConditionedPattern:
    two_theta: np.ndarray      # sorted, degrees
    raw: np.ndarray            # raw intensity, reordered to match
    baseline: np.ndarray       # estimated background radiation
    corrected: np.ndarray      # raw - baseline, floored at 0
    processed: np.ndarray      # corrected + Savitzky-Golay smoothed
    noise_sigma: float         # robust counting-noise estimate on `processed`
    step_deg: float            # median angular step


def ingest(two_theta: list[float] | np.ndarray, intensity: list[float] | np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Validate the two-column raw array and return sorted float64 arrays."""
    x = np.asarray(two_theta, dtype=np.float64)
    y = np.asarray(intensity, dtype=np.float64)
    if x.ndim != 1 or y.ndim != 1:
        raise ValueError("two_theta and intensity must be one-dimensional arrays")
    if x.size != y.size:
        raise ValueError(f"length mismatch: {x.size} angles vs {y.size} intensities")
    if x.size < 50:
        raise ValueError("at least 50 data points are required for a meaningful scan")
    if not (np.isfinite(x).all() and np.isfinite(y).all()):
        raise ValueError("two_theta and intensity must contain only finite values")
    if np.unique(x).size != x.size:
        raise ValueError("two_theta contains duplicate angles")
    order = np.argsort(x)
    return x[order], y[order]


def als_baseline(y: np.ndarray, lam: float = 1e5, p: float = 0.01, niter: int = 10) -> np.ndarray:
    """Asymmetric Least Squares background estimation.

    Minimises  sum(w * (y - z)^2) + lam * sum(d2(z)^2)  where points above
    the current estimate (peaks) get the small weight `p` and points below
    (background) get `1 - p`, so the fit hugs the valleys between peaks.
    """
    n = y.size
    d2 = sparse.diags([1.0, -2.0, 1.0], [0, -1, -2], shape=(n, n - 2))
    penalty = lam * (d2 @ d2.T)
    w = np.ones(n)
    z = y
    for _ in range(max(1, niter)):
        weights = sparse.spdiags(w, 0, n, n)
        z = spsolve((weights + penalty).tocsc(), w * y)
        w = np.where(y > z, p, 1.0 - p)
    return np.asarray(z)


def _default_savgol_window(step_deg: float, n_points: int) -> int:
    """Window spanning ~0.2 deg of 2-theta: wide enough to kill shot noise,
    narrow enough not to erode a ~0.2-0.4 deg FWHM diffraction peak."""
    window = int(round(0.2 / max(step_deg, 1e-6)))
    window = max(7, min(window, 31, n_points - (n_points + 1) % 2))
    if window % 2 == 0:
        window += 1
    return window


def estimate_noise_sigma(y: np.ndarray) -> float:
    """Robust sigma from the median absolute successive difference.

    diff() of i.i.d. noise has variance 2*sigma^2 and is insensitive to the
    slowly varying signal underneath, so MAD(diff)/sqrt(2) * 1.4826 recovers
    the counting-noise sigma even on a peak-rich pattern.
    """
    diffs = np.abs(np.diff(y))
    mad = np.median(diffs)
    return float(1.4826 * mad / np.sqrt(2.0)) or 1.0


def condition(
    two_theta: list[float] | np.ndarray,
    intensity: list[float] | np.ndarray,
    *,
    als_lam: float = 1e5,
    als_p: float = 0.01,
    als_niter: int = 10,
    savgol_window: int | None = None,
    savgol_polyorder: int = 3,
) -> ConditionedPattern:
    x, y = ingest(two_theta, intensity)
    step = float(np.median(np.diff(x)))

    baseline = als_baseline(y, lam=als_lam, p=als_p, niter=als_niter)
    corrected = np.clip(y - baseline, 0.0, None)

    window = savgol_window or _default_savgol_window(step, x.size)
    if window % 2 == 0:
        window += 1
    window = min(window, x.size if x.size % 2 else x.size - 1)
    polyorder = min(savgol_polyorder, window - 2)
    processed = np.clip(savgol_filter(corrected, window, polyorder), 0.0, None)

    return ConditionedPattern(
        two_theta=x,
        raw=y,
        baseline=baseline,
        corrected=corrected,
        processed=processed,
        noise_sigma=estimate_noise_sigma(processed),
        step_deg=step,
    )
