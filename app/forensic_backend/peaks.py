"""Peak detection and mathematical profile fitting.

Detection uses scipy.signal.find_peaks with a dynamic prominence threshold
derived from the measured counting-noise sigma, so weak-but-real reflections
survive on clean scans while noisy scans don't flood the matcher with spikes.

The highest-intensity peaks are then refined by least-squares fitting of a
Gaussian and a Lorentzian profile (scipy.optimize.curve_fit); the better
model by R-squared wins and yields the true Bragg angle and the true FWHM
(beta) needed by the Scherrer equation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import curve_fit
from scipy.signal import find_peaks

from .conditioning import ConditionedPattern

GAUSSIAN_FWHM_FACTOR = 2.0 * np.sqrt(2.0 * np.log(2.0))  # 2.3548


@dataclass
class DetectedPeak:
    index: int
    two_theta: float          # degrees
    height: float
    prominence: float
    fwhm_deg: float           # half-height width estimate from find_peaks
    left_base_deg: float
    right_base_deg: float
    area: float               # trapezoidal integral over the peak window


@dataclass
class FittedPeak:
    two_theta: float          # refined Bragg 2-theta (degrees)
    model: str                # "gaussian" | "lorentzian"
    amplitude: float
    fwhm_deg: float           # beta, degrees of 2-theta
    area: float               # analytic integrated intensity
    r_squared: float
    detected: DetectedPeak = field(repr=False, default=None)


def _gaussian(x, amp, center, sigma, offset):
    return amp * np.exp(-((x - center) ** 2) / (2.0 * sigma**2)) + offset


def _lorentzian(x, amp, center, gamma, offset):
    return amp * gamma**2 / ((x - center) ** 2 + gamma**2) + offset


def detect_peaks(
    pattern: ConditionedPattern,
    *,
    prominence_sigma: float = 5.0,
    min_rel_height: float = 0.02,
    min_separation_deg: float = 0.15,
) -> list[DetectedPeak]:
    x, y = pattern.two_theta, pattern.processed
    if y.max() <= 0:
        return []

    # Dynamic prominence: whichever is stricter of "N noise sigmas" and
    # "a small fraction of the strongest reflection".
    prominence = max(prominence_sigma * pattern.noise_sigma, min_rel_height * y.max())
    distance = max(1, int(round(min_separation_deg / pattern.step_deg)))

    idx, props = find_peaks(y, prominence=prominence, width=2, distance=distance)

    peaks: list[DetectedPeak] = []
    for k, i in enumerate(idx):
        fwhm_deg = float(props["widths"][k]) * pattern.step_deg
        left_ip, right_ip = props["left_ips"][k], props["right_ips"][k]
        positions = np.arange(x.size, dtype=np.float64)
        left_deg = float(np.interp(left_ip, positions, x))
        right_deg = float(np.interp(right_ip, positions, x))

        # Integrate over +/- 1.5 FWHM — captures ~96% of a Gaussian's area
        # without swallowing neighbouring reflections.
        half_win = 1.5 * fwhm_deg
        sel = (x >= x[i] - half_win) & (x <= x[i] + half_win)
        area = float(np.trapezoid(y[sel], x[sel])) if sel.sum() > 1 else 0.0

        peaks.append(DetectedPeak(
            index=int(i),
            two_theta=float(x[i]),
            height=float(y[i]),
            prominence=float(props["prominences"][k]),
            fwhm_deg=fwhm_deg,
            left_base_deg=left_deg,
            right_base_deg=right_deg,
            area=area,
        ))
    return peaks


def _fit_single(x: np.ndarray, y: np.ndarray, peak: DetectedPeak) -> FittedPeak | None:
    half_win = max(3.0 * peak.fwhm_deg, 0.4)
    sel = (x >= peak.two_theta - half_win) & (x <= peak.two_theta + half_win)
    xs, ys = x[sel], y[sel]
    if xs.size < 7:
        return None

    step = float(np.median(np.diff(xs)))
    ss_tot = float(np.sum((ys - ys.mean()) ** 2)) or 1.0

    candidates = []
    for name, func, width_p0, fwhm_of in (
        ("gaussian", _gaussian, peak.fwhm_deg / GAUSSIAN_FWHM_FACTOR,
         lambda w: w * GAUSSIAN_FWHM_FACTOR),
        ("lorentzian", _lorentzian, peak.fwhm_deg / 2.0,
         lambda w: w * 2.0),
    ):
        p0 = [peak.height, peak.two_theta, max(width_p0, step), float(ys.min())]
        bounds = (
            [0.0, xs[0], step / 2.0, -np.inf],
            [np.inf, xs[-1], (xs[-1] - xs[0]), np.inf],
        )
        try:
            popt, _ = curve_fit(func, xs, ys, p0=p0, bounds=bounds, maxfev=5000)
        except (RuntimeError, ValueError):
            continue
        residuals = ys - func(xs, *popt)
        r2 = 1.0 - float(np.sum(residuals**2)) / ss_tot
        amp, center, width, _offset = popt
        fwhm = fwhm_of(width)
        if name == "gaussian":
            area = amp * width * np.sqrt(2.0 * np.pi)
        else:
            area = amp * width * np.pi
        candidates.append(FittedPeak(
            two_theta=float(center), model=name, amplitude=float(amp),
            fwhm_deg=float(fwhm), area=float(area), r_squared=r2, detected=peak,
        ))

    if not candidates:
        return None
    return max(candidates, key=lambda f: f.r_squared)


def fit_peaks(
    pattern: ConditionedPattern,
    peaks: list[DetectedPeak],
    *,
    max_fitted_peaks: int = 8,
    min_r_squared: float = 0.5,
) -> list[FittedPeak]:
    """Profile-fit the highest-intensity peaks; discard degenerate fits."""
    strongest = sorted(peaks, key=lambda p: p.height, reverse=True)[:max_fitted_peaks]
    fitted = []
    for peak in strongest:
        fit = _fit_single(pattern.two_theta, pattern.processed, peak)
        if fit is not None and fit.r_squared >= min_r_squared:
            fitted.append(fit)
    fitted.sort(key=lambda f: f.two_theta)
    return fitted
