"""ArchPi Forensic Materials Lab — XRD analysis FastAPI service.

Run from the app/ directory:

    python -m uvicorn forensic_backend.main:app --port 8010 --reload

POST /api/v1/xrd/analyze   raw (2-theta, intensity) arrays -> full payload
GET  /api/v1/xrd/demo      synthetic mortar diffractogram for the UI/tests
GET  /api/v1/xrd/reference the local COD phase matrix

POST /api/v1/hazards/evaluate                hourly climate + material -> risk matrix
POST /api/v1/hazards/crystallization-pressure  Correns pressure calculator
POST /api/v1/hazards/rust-jacking            Faraday/Lame sleeve stress calculator
GET  /api/v1/hazards/demo                    synthetic year of hourly microclimate

POST /api/v1/palimpsest/validate  components + relations -> DAG summary (409 on paradox)
POST /api/v1/palimpsest/sequence  full pipeline: validation + Bayesian dating + campaigns
GET  /api/v1/palimpsest/demo      worked example mirroring the dossier timeline

POST /api/v1/provenance/match    IRMS isotope panel -> ranked quarry matches
POST /api/v1/provenance/route    quarry/point -> site least-cost trade route + GeoJSON
POST /api/v1/provenance/analyze  full pipeline: match -> route -> GeoJSON FeatureCollection
GET  /api/v1/provenance/quarries the isotopic baseline registry
GET  /api/v1/provenance/demo     worked analyze payload (Carrara-like sample -> Rome)

POST /api/v1/dendro/analyze      ring-width series -> crossdate + felling estimate
GET  /api/v1/dendro/reference    the master chronology series
GET  /api/v1/dendro/demo         synthetic core sample #012 (crossdates to 1242 AD)

POST /api/v1/radiocarbon/calibrate  14C age BP -> calendar HPD ranges (1s, 2s)
GET  /api/v1/radiocarbon/curve       the bundled IntCal-style calibration curve
GET  /api/v1/radiocarbon/demo        worked payload (760+/-25 BP -> ~1240-1280 AD)
"""

from __future__ import annotations

import logging
import time

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .conditioning import condition
from .peaks import detect_peaks, fit_peaks
from .phases import load_reference_db, match_phases
from .quant import (
    CU_K_ALPHA_NM,
    SCHERRER_K,
    average_crystallite_size_nm,
    calcium_content_pct,
    crystallinity_pct,
    crystallite_sizes,
    rir_weight_fractions,
    silica_ratio_pct,
)
from .hazard_engine import HazardMatrixEngine, MaterialProfile
from .hazard_schemas import (
    CrystallizationPressureOut,
    CrystallizationPressureRequest,
    FreezeThawAssessmentOut,
    HazardEvaluationRequest,
    HazardEvaluationResponse,
    HazardMatrixRowOut,
    HazardMetaOut,
    RustAssessmentOut,
    RustJackingOut,
    RustJackingRequest,
    SaltAssessmentOut,
    SaltCycleStatsOut,
)
from .campaigns import CampaignClusterer, ChemicalSignature
from .chronology import (
    ChronologicalConflictError,
    ChronologyOptimizer,
    DateConstraint,
    DateInterval,
)
from .dendro import (
    analyze_sample,
    build_reference_chronology,
    generate_core_sample,
)
from .dendro_schemas import (
    DateCandidateOut,
    DendroMetaOut,
    DendroRequest,
    DendroResponse,
    FellingEstimateOut,
)
from .isotopes import QUARRY_REGISTRY, IsotopeMatcher, UnknownQuarryError
from .microclimate import MicroclimateSeries
from .palimpsest_schemas import (
    CampaignOut,
    GraphSummaryOut,
    PalimpsestMetaOut,
    PhaseOut,
    SequenceRequest,
    SequenceResponse,
    ValidateRequest,
    ValidateResponse,
)
from .radiocarbon import (
    CURVE_END_AD,
    CURVE_START_AD,
    CalibrationOutOfRangeError,
    RadiocarbonCalibrator,
    calibration_curve,
)
from .radiocarbon_schemas import (
    CalibrationMetaOut,
    CalibrationRequest,
    CalibrationResponse,
    YearRangeOut,
)
from .provenance_schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    FeatureCollectionOut,
    FeatureOut,
    GeometryOut,
    MatchRequest,
    MatchResponse,
    ProvenanceMetaOut,
    QuarryOut,
    RouteRequest,
    RouteResponse,
    destination_feature,
    feature_collection,
    match_out,
    route_feature,
    route_out,
)
from .routing import RouteNotFoundError, default_engine
from .rust_jacking import RustJackingModel
from .sites import list_structures, site_bundle
from .terrain import BBOX, FRICTION, terrain_frame
from .stratigraphy import (
    StratigraphicGraph,
    StratigraphicParadoxException,
    StratigraphicRelation,
    UnknownComponentError,
)
from .salt_stress import SALT_REGISTRY, CrystallizationStressModel
from .schemas import (
    AnalysisMeta,
    ConditionedArrays,
    CrystalliteOut,
    FittedPeakOut,
    LineMatchOut,
    PeakOut,
    PhaseMatchOut,
    PhaseWeightOut,
    QuantitativeOut,
    XRDAnalysisRequest,
    XRDAnalysisResponse,
)
from .synthetic import generate_climate, generate_pattern

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("forensic_backend")

app = FastAPI(title="ArchPi Forensic Materials Lab — XRD Engine", version="1.0.0")

# Dev CORS: the UI is served by the Node server on :3000
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "forensic-xrd-engine"}


# ═══════════════════════════════════════════════════════════════════════
# Structure selector — one coherent specimen drives every engine at once
# ═══════════════════════════════════════════════════════════════════════

@app.get("/api/v1/sites")
async def sites() -> dict:
    """Selector metadata for every registered structure."""
    return {"sites": list_structures()}


@app.get("/api/v1/sites/{site_id}")
async def site_detail(site_id: str) -> dict:
    """Full per-engine request bodies for one structure (POST them unchanged)."""
    try:
        return site_bundle(site_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"unknown structure id: {site_id!r}"
        ) from exc


def run_analysis(req: XRDAnalysisRequest) -> XRDAnalysisResponse:
    started = time.perf_counter()

    pattern = condition(
        req.two_theta,
        req.intensity,
        als_lam=req.conditioning.als_lam,
        als_p=req.conditioning.als_p,
        als_niter=req.conditioning.als_niter,
        savgol_window=req.conditioning.savgol_window,
        savgol_polyorder=req.conditioning.savgol_polyorder,
    )

    detected = detect_peaks(
        pattern,
        prominence_sigma=req.peaks.prominence_sigma,
        min_rel_height=req.peaks.min_rel_height,
        min_separation_deg=req.peaks.min_separation_deg,
    )
    fitted = fit_peaks(pattern, detected, max_fitted_peaks=req.peaks.max_fitted_peaks)

    matches = match_phases(detected, fitted, tolerance_deg=req.matching.tolerance_deg)

    estimates = crystallite_sizes(
        fitted, instrumental_fwhm_deg=req.matching.instrumental_fwhm_deg
    )
    avg_nm = average_crystallite_size_nm(estimates)
    weights = rir_weight_fractions(
        matches, min_confidence_pct=req.matching.min_confidence_pct
    )

    elapsed_ms = (time.perf_counter() - started) * 1000.0

    return XRDAnalysisResponse(
        meta=AnalysisMeta(
            sample_id=req.sample_id,
            n_points=pattern.two_theta.size,
            two_theta_min=float(pattern.two_theta[0]),
            two_theta_max=float(pattern.two_theta[-1]),
            n_peaks_detected=len(detected),
            n_peaks_fitted=len(fitted),
            processing_ms=round(elapsed_ms, 1),
            scherrer_k=SCHERRER_K,
            wavelength_nm=CU_K_ALPHA_NM,
        ),
        conditioned=ConditionedArrays(
            two_theta=pattern.two_theta.tolist(),
            raw=pattern.raw.tolist(),
            baseline=pattern.baseline.tolist(),
            processed=pattern.processed.tolist(),
            noise_sigma=round(pattern.noise_sigma, 3),
            step_deg=round(pattern.step_deg, 5),
        ),
        detected_peaks=[
            PeakOut(
                two_theta=round(p.two_theta, 4),
                height=round(p.height, 2),
                prominence=round(p.prominence, 2),
                fwhm_deg=round(p.fwhm_deg, 4),
                left_base_deg=round(p.left_base_deg, 4),
                right_base_deg=round(p.right_base_deg, 4),
                area=round(p.area, 2),
            )
            for p in detected
        ],
        fitted_peaks=[
            FittedPeakOut(
                two_theta=round(f.two_theta, 4),
                model=f.model,
                amplitude=round(f.amplitude, 2),
                fwhm_deg=round(f.fwhm_deg, 4),
                area=round(f.area, 2),
                r_squared=round(f.r_squared, 4),
            )
            for f in fitted
        ],
        phase_matches=[
            PhaseMatchOut(
                phase_id=m.phase_id,
                name=m.name,
                formula=m.formula,
                rir=m.rir,
                confidence_pct=m.confidence_pct,
                matched_lines=[
                    LineMatchOut(
                        ref_two_theta=l.ref_two_theta,
                        ref_rel_intensity=l.ref_rel_intensity,
                        hkl=l.hkl,
                        observed_two_theta=round(l.observed_two_theta, 4),
                        delta_deg=l.delta_deg,
                        observed_height=round(l.observed_height, 2),
                    )
                    for l in m.matched_lines
                ],
            )
            for m in matches
        ],
        quantitative=QuantitativeOut(
            crystallite_estimates=[
                CrystalliteOut(
                    two_theta_deg=round(e.two_theta_deg, 4),
                    theta_deg=round(e.theta_deg, 4),
                    beta_obs_deg=round(e.beta_obs_deg, 4),
                    beta_sample_deg=round(e.beta_sample_deg, 4),
                    size_nm=round(e.size_nm, 2),
                )
                for e in estimates
            ],
            avg_crystallite_size_nm=round(avg_nm, 2) if avg_nm else None,
            avg_crystallite_size_um=round(avg_nm / 1000.0, 4) if avg_nm else None,
            phase_weights=[
                PhaseWeightOut(
                    phase_id=w.phase_id,
                    name=w.name,
                    formula=w.formula,
                    rir=w.rir,
                    integrated_intensity=w.integrated_intensity,
                    weight_pct=w.weight_pct,
                )
                for w in weights
            ],
            silica_ratio_pct=silica_ratio_pct(weights, matches),
            calcium_content_pct=calcium_content_pct(weights, matches),
            crystallinity_pct=crystallinity_pct(pattern),
        ),
    )


@app.post("/api/v1/xrd/analyze", response_model=XRDAnalysisResponse)
async def analyze(req: XRDAnalysisRequest) -> XRDAnalysisResponse:
    try:
        result = run_analysis(req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info(
        "sample=%s peaks=%d fitted=%d top_phase=%s (%.1f%%) in %.0fms",
        req.sample_id,
        result.meta.n_peaks_detected,
        result.meta.n_peaks_fitted,
        result.phase_matches[0].phase_id if result.phase_matches else "none",
        result.phase_matches[0].confidence_pct if result.phase_matches else 0.0,
        result.meta.processing_ms,
    )
    return result


@app.get("/api/v1/xrd/reference")
async def reference() -> dict:
    return load_reference_db()


@app.get("/api/v1/xrd/demo")
async def demo(
    quartz: float = Query(0.5, ge=0, le=1),
    calcite: float = Query(0.4, ge=0, le=1),
    gypsum: float = Query(0.1, ge=0, le=1),
    crystallite_nm: float = Query(40.0, gt=1, le=500),
    seed: int = Query(42),
) -> dict:
    """Synthetic historic-mortar diffractogram (raw, unconditioned)."""
    two_theta, intensity = generate_pattern(
        {"quartz": quartz, "calcite": calcite, "gypsum": gypsum},
        crystallite_nm=crystallite_nm,
        seed=seed,
    )
    return {
        "sample_id": "SYNTHETIC-MORTAR",
        "two_theta": two_theta,
        "intensity": intensity,
        "truth": {
            "quartz": quartz,
            "calcite": calcite,
            "gypsum": gypsum,
            "crystallite_nm": crystallite_nm,
        },
    }


# ═══════════════════════════════════════════════════════════════════════
# Feature 2 — Material degradation hazard engine
# ═══════════════════════════════════════════════════════════════════════

def run_hazard_evaluation(req: HazardEvaluationRequest) -> HazardEvaluationResponse:
    started = time.perf_counter()

    series = MicroclimateSeries(req.relative_humidity_pct, req.temperature_c)
    engine = HazardMatrixEngine(
        MaterialProfile(
            porosity_pct=req.material.porosity_pct,
            tensile_strength_mpa=req.material.tensile_strength_mpa,
        )
    )

    salt_assessments = engine.assess_all_salts(series)
    governing_id, governing = engine.governing_salt(salt_assessments)
    rust = engine.assess_rust(
        series,
        diameter_mm=req.rust_tie.diameter_mm,
        exposure_years=req.rust_tie.exposure_years,
        i_corr_ua_cm2=req.rust_tie.i_corr_ua_cm2,
        oxide=req.rust_tie.oxide,
        cover_mm=req.rust_tie.cover_mm,
    )
    freeze = engine.assess_freeze_thaw(series)
    matrix = engine.matrix(governing, rust, freeze)

    elapsed_ms = (time.perf_counter() - started) * 1000.0

    return HazardEvaluationResponse(
        meta=HazardMetaOut(
            site_id=req.site_id,
            samples=series.rh.size,
            duration_years=round(series.duration_years, 3),
            mean_rh_pct=round(series.mean_rh_pct, 1),
            mean_temperature_c=round(series.mean_temperature_c, 1),
            time_of_wetness=rust.time_of_wetness,
            governing_salt=governing_id,
            processing_ms=round(elapsed_ms, 1),
        ),
        salt_assessments=[
            SaltAssessmentOut(
                salt_id=salt_id,
                name=SALT_REGISTRY[salt_id].name,
                formula=SALT_REGISTRY[salt_id].formula,
                equilibrium_rh_pct=SALT_REGISTRY[salt_id].equilibrium_rh_pct,
                stats=SaltCycleStatsOut(
                    crystallization_events=a.stats.crystallization_events,
                    cycles_per_year=a.stats.cycles_per_year,
                    supersaturation_estimate=a.stats.supersaturation_estimate,
                    mean_event_temperature_c=a.stats.mean_event_temperature_c,
                ),
                pressure_mpa=a.pressure_mpa,
                stress_ratio=a.stress_ratio,
                score=a.score,
                state=a.state,
            )
            for salt_id, a in salt_assessments.items()
        ],
        rust_jacking=RustAssessmentOut(
            detail=RustJackingOut(**rust.result.__dict__),
            i_corr_source=rust.i_corr_source,
            time_of_wetness=rust.time_of_wetness,
            score=rust.score,
            state=rust.state,
        ),
        freeze_thaw=FreezeThawAssessmentOut(
            total_cycles=freeze.stats.total_cycles,
            wet_cycles=freeze.stats.wet_cycles,
            wet_cycles_per_year=freeze.stats.wet_cycles_per_year,
            mean_freeze_minimum_c=freeze.stats.mean_freeze_minimum_c,
            score=freeze.score,
            state=freeze.state,
        ),
        hazard_matrix=[HazardMatrixRowOut(**row.__dict__) for row in matrix],
    )


@app.post("/api/v1/hazards/evaluate", response_model=HazardEvaluationResponse)
async def evaluate_hazards(req: HazardEvaluationRequest) -> HazardEvaluationResponse:
    try:
        result = run_hazard_evaluation(req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info(
        "site=%s samples=%d matrix=%s in %.0fms",
        req.site_id,
        result.meta.samples,
        [(r.hazard, r.score, r.state.value) for r in result.hazard_matrix],
        result.meta.processing_ms,
    )
    return result


@app.post("/api/v1/hazards/crystallization-pressure", response_model=CrystallizationPressureOut)
async def crystallization_pressure(req: CrystallizationPressureRequest) -> CrystallizationPressureOut:
    try:
        result = CrystallizationStressModel.for_salt(req.salt_id).evaluate(
            req.temperature_c, req.supersaturation, req.tensile_threshold_mpa
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return CrystallizationPressureOut(**result.__dict__)


@app.post("/api/v1/hazards/rust-jacking", response_model=RustJackingOut)
async def rust_jacking(req: RustJackingRequest) -> RustJackingOut:
    try:
        result = RustJackingModel().evaluate(
            diameter_mm=req.diameter_mm,
            i_corr_ua_cm2=req.i_corr_ua_cm2,
            exposure_years=req.exposure_years,
            oxide=req.oxide,
            cover_mm=req.cover_mm,
            tensile_threshold_mpa=req.tensile_threshold_mpa,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RustJackingOut(**result.__dict__)


@app.get("/api/v1/hazards/demo")
async def hazards_demo(
    days: int = Query(365, ge=2, le=3650),
    mean_rh_pct: float = Query(74.0, ge=10, le=98),
    mean_temperature_c: float = Query(11.0, ge=-30, le=45),
    seed: int = Query(42),
) -> dict:
    """Synthetic hourly facade microclimate + a default material profile."""
    rh, temp = generate_climate(
        days=days, mean_rh_pct=mean_rh_pct,
        mean_temperature_c=mean_temperature_c, seed=seed,
    )
    return {
        "site_id": "SYNTHETIC-FACADE",
        "relative_humidity_pct": rh,
        "temperature_c": temp,
        "material": {"porosity_pct": 22.0, "tensile_strength_mpa": 3.0},
        "rust_tie": {
            "diameter_mm": 20.0,
            "exposure_years": 150.0,
            "oxide": "Fe(OH)3",
            "cover_mm": 50.0,
        },
    }


# ═══════════════════════════════════════════════════════════════════════
# Feature 3 — Chronological Palimpsest (stratigraphy + Bayesian dating)
# ═══════════════════════════════════════════════════════════════════════

def _build_graph(req: ValidateRequest) -> StratigraphicGraph:
    relations = [
        StratigraphicRelation(earlier=r.earlier, later=r.later, kind=r.kind)
        for r in req.relations
    ]
    return StratigraphicGraph(req.components, relations)


def run_sequencing(req: SequenceRequest) -> SequenceResponse:
    started = time.perf_counter()

    graph = _build_graph(req)

    constraints = [
        DateConstraint(
            node_id=c.node_id,
            kind=c.kind,
            intervals=tuple(
                DateInterval(iv.start_year, iv.end_year, iv.weight)
                for iv in c.intervals
            ),
        )
        for c in req.constraints
    ]
    optimizer = ChronologyOptimizer(
        graph, constraints, padding_years=req.padding_years
    )
    phases = optimizer.phases()

    signatures = [
        ChemicalSignature(
            node_id=s.node_id,
            silica_ratio_pct=s.silica_ratio_pct,
            calcium_ratio_pct=s.calcium_ratio_pct,
        )
        for s in req.signatures
    ]
    if signatures:
        clusterer = CampaignClusterer(
            graph, signatures, variance_threshold=req.clustering.variance_threshold
        )
        campaigns, unclustered = clusterer.cluster(phases)
    else:
        campaigns, unclustered = [], sorted(graph.graph.nodes)

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    summary = graph.summary()

    return SequenceResponse(
        meta=PalimpsestMetaOut(
            site_id=req.site_id,
            node_count=summary.node_count,
            edge_count=summary.edge_count,
            constraint_count=len(constraints),
            signature_count=len(signatures),
            grid_start_year=round(float(optimizer.years[0]), 1),
            grid_end_year=round(float(optimizer.years[-1]), 1),
            grid_step_years=round(optimizer.step, 3),
            variance_threshold=req.clustering.variance_threshold,
            processing_ms=round(elapsed_ms, 1),
        ),
        graph=GraphSummaryOut(**summary.__dict__),
        phases=[PhaseOut(**p.__dict__) for p in phases],
        campaigns=[CampaignOut(**c.__dict__) for c in campaigns],
        unclustered=unclustered,
    )


@app.post("/api/v1/palimpsest/validate", response_model=ValidateResponse)
async def validate_stratigraphy(req: ValidateRequest) -> ValidateResponse:
    """Cycle-detection gate: 200 with the DAG summary, or 409 on paradox."""
    try:
        graph = _build_graph(req)
    except StratigraphicParadoxException as exc:
        raise HTTPException(
            status_code=409, detail={"message": str(exc), **exc.detail}
        ) from exc
    except (UnknownComponentError, ValueError) as exc:
        detail = getattr(exc, "detail", None)
        raise HTTPException(
            status_code=422,
            detail={"message": str(exc), **detail} if detail else str(exc),
        ) from exc
    return ValidateResponse(
        site_id=req.site_id,
        valid=True,
        graph=GraphSummaryOut(**graph.summary().__dict__),
    )


@app.post("/api/v1/palimpsest/sequence", response_model=SequenceResponse)
async def sequence_palimpsest(req: SequenceRequest) -> SequenceResponse:
    try:
        result = run_sequencing(req)
    except (StratigraphicParadoxException, ChronologicalConflictError) as exc:
        raise HTTPException(
            status_code=409, detail={"message": str(exc), **exc.detail}
        ) from exc
    except (UnknownComponentError, ValueError) as exc:
        detail = getattr(exc, "detail", None)
        raise HTTPException(
            status_code=422,
            detail={"message": str(exc), **detail} if detail else str(exc),
        ) from exc
    logger.info(
        "site=%s nodes=%d phases=%d campaigns=%d in %.0fms",
        req.site_id,
        result.meta.node_count,
        len(result.phases),
        len(result.campaigns),
        result.meta.processing_ms,
    )
    return result


@app.get("/api/v1/palimpsest/demo")
async def palimpsest_demo() -> dict:
    """Worked sequencing payload mirroring the dossier timeline.

    Roman foundation (inscription 120-150 AD), Gothic expansion
    (radiocarbon 2-sigma 1235-1280 with the 1-sigma 1240-1265 core),
    modern restoration (archival 1982-1998). POST it unchanged to
    /api/v1/palimpsest/sequence.
    """
    return {
        "site_id": "AETHEL-DOSSIER",
        "components": [
            "Foundation_01", "Roman_Floor", "Hypocaust_Column",
            "West_Arch", "Nave_Vault", "Chapel_Apse", "West_Arch_Repair",
        ],
        "relations": [
            {"earlier": "Foundation_01", "later": "Roman_Floor", "kind": "built_after"},
            {"earlier": "Foundation_01", "later": "Hypocaust_Column", "kind": "built_after"},
            {"earlier": "Roman_Floor", "later": "West_Arch", "kind": "cuts"},
            {"earlier": "West_Arch", "later": "Nave_Vault", "kind": "built_after"},
            {"earlier": "West_Arch", "later": "Chapel_Apse", "kind": "built_after"},
            {"earlier": "West_Arch", "later": "West_Arch_Repair", "kind": "replaces"},
        ],
        "constraints": [
            {
                "node_id": "Foundation_01",
                "kind": "inscription",
                "intervals": [{"start_year": 120, "end_year": 150, "weight": 1.0}],
            },
            {
                "node_id": "West_Arch",
                "kind": "radiocarbon_2sigma",
                "intervals": [
                    {"start_year": 1235, "end_year": 1240, "weight": 0.136},
                    {"start_year": 1240, "end_year": 1265, "weight": 0.682},
                    {"start_year": 1265, "end_year": 1280, "weight": 0.136},
                ],
            },
            {
                "node_id": "West_Arch_Repair",
                "kind": "archival",
                "intervals": [{"start_year": 1982, "end_year": 1998, "weight": 1.0}],
            },
        ],
        "signatures": [
            {"node_id": "Foundation_01", "silica_ratio_pct": 67.0, "calcium_ratio_pct": 23.0},
            {"node_id": "Roman_Floor", "silica_ratio_pct": 66.0, "calcium_ratio_pct": 24.0},
            {"node_id": "Hypocaust_Column", "silica_ratio_pct": 65.0, "calcium_ratio_pct": 25.0},
            {"node_id": "West_Arch", "silica_ratio_pct": 28.0, "calcium_ratio_pct": 60.0},
            {"node_id": "Nave_Vault", "silica_ratio_pct": 30.0, "calcium_ratio_pct": 58.0},
            {"node_id": "Chapel_Apse", "silica_ratio_pct": 29.0, "calcium_ratio_pct": 59.0},
            {"node_id": "West_Arch_Repair", "silica_ratio_pct": 45.0, "calcium_ratio_pct": 40.0},
        ],
        "clustering": {"variance_threshold": 0.15},
        "padding_years": 50.0,
    }


# ═══════════════════════════════════════════════════════════════════════
# Feature 4 — Provenance Map Engine (isotopic matching + LCP routing)
# ═══════════════════════════════════════════════════════════════════════

def _route_http_error(exc: RouteNotFoundError) -> HTTPException:
    status = 422 if exc.detail.get("reason") == "outside_grid" else 409
    return HTTPException(status_code=status, detail={"message": str(exc), **exc.detail})


def run_provenance_analysis(req: AnalyzeRequest) -> AnalyzeResponse:
    started = time.perf_counter()

    matches = IsotopeMatcher().match(
        req.delta13c, req.delta18o, req.sr8786, top_k=req.top_k
    )
    best = matches[0]
    engine = default_engine()
    route = engine.route(best.lat, best.lon, req.destination.lat, req.destination.lon)

    elapsed_ms = (time.perf_counter() - started) * 1000.0

    return AnalyzeResponse(
        meta=ProvenanceMetaOut(
            sample_id=req.sample_id,
            destination_name=req.destination.name,
            best_quarry_id=best.quarry_id,
            confidence_pct=best.confidence_pct,
            plausible=best.plausible,
            grid_resolution_deg=engine.surface.resolution,
            processing_ms=round(elapsed_ms, 1),
        ),
        matches=[match_out(m) for m in matches],
        route=route_out(route),
        geojson=feature_collection(best, req.destination, route),
    )


@app.post("/api/v1/provenance/match", response_model=MatchResponse)
async def match_isotopes(req: MatchRequest) -> MatchResponse:
    try:
        matches = IsotopeMatcher().match(
            req.delta13c, req.delta18o, req.sr8786, top_k=req.top_k
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return MatchResponse(sample_id=req.sample_id, matches=[match_out(m) for m in matches])


@app.post("/api/v1/provenance/route", response_model=RouteResponse)
async def trade_route(req: RouteRequest) -> RouteResponse:
    matcher = IsotopeMatcher()
    try:
        if req.source_quarry_id is not None:
            q = matcher.get(req.source_quarry_id)
            src_lat, src_lon = q.lat, q.lon
        else:
            src_lat, src_lon = req.source.lat, req.source.lon
        route = default_engine().route(
            src_lat, src_lon, req.destination.lat, req.destination.lon
        )
    except UnknownQuarryError as exc:
        raise HTTPException(
            status_code=422, detail={"message": str(exc), **exc.detail}
        ) from exc
    except RouteNotFoundError as exc:
        raise _route_http_error(exc) from exc

    # Standalone routing has no isotopic match; origin is a bare point.
    origin = FeatureOut(
        geometry=GeometryOut(type="Point", coordinates=[src_lon, src_lat]),
        properties={
            "role": "origin_quarry",
            "quarry_id": req.source_quarry_id,
            "name": q.name if req.source_quarry_id else req.source.name,
        },
    )
    return RouteResponse(
        route=route_out(route),
        geojson=FeatureCollectionOut(features=[
            origin, destination_feature(req.destination), route_feature(route),
        ]),
    )


@app.post("/api/v1/provenance/analyze", response_model=AnalyzeResponse)
async def analyze_provenance(req: AnalyzeRequest) -> AnalyzeResponse:
    try:
        result = run_provenance_analysis(req)
    except RouteNotFoundError as exc:
        raise _route_http_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info(
        "sample=%s quarry=%s (%.1f%%, plausible=%s) route=%.0fkm/%d transshipments in %.0fms",
        req.sample_id,
        result.meta.best_quarry_id,
        result.meta.confidence_pct,
        result.meta.plausible,
        result.route.total_km,
        result.route.transshipments,
        result.meta.processing_ms,
    )
    return result


@app.get("/api/v1/provenance/terrain")
async def provenance_terrain() -> dict:
    """The routing engine's own geography as GeoJSON, for map display.

    Coarse hand-digitized polygons (kind: land | mountain | river) plus the
    grid bbox and friction table — exactly what the cost surface is built
    from, so any rendering of it is faithful to the router's world model.
    """
    features = []
    for rec in terrain_frame().itertuples():
        geom = rec.geometry
        polys = [geom] if geom.geom_type == "Polygon" else [
            g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon"
        ]
        for p in polys:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[round(x, 3), round(y, 3)] for x, y in p.exterior.coords]
                    ],
                },
                "properties": {"name": rec.name, "kind": rec.kind},
            })
    return {
        "type": "FeatureCollection",
        "features": features,
        "bbox": list(BBOX),
        "friction": FRICTION,
    }


@app.get("/api/v1/provenance/quarries")
async def quarries() -> dict:
    return {
        "quarries": [QuarryOut(**q.__dict__).model_dump() for q in QUARRY_REGISTRY.values()],
        "axes": ["delta13c", "delta18o", "sr8786"],
    }


@app.get("/api/v1/provenance/demo")
async def provenance_demo() -> dict:
    """Worked analyze payload: Carrara-like marble sample bound for Rome.

    POST it unchanged to /api/v1/provenance/analyze.
    """
    return {
        "sample_id": "AETHEL-COLUMN-03",
        "delta13c": 2.1,
        "delta18o": -2.0,
        "sr8786": 0.70785,
        "destination": {"lat": 41.89, "lon": 12.49, "name": "Basilica Site, Rome"},
        "top_k": 3,
    }


# ═══════════════════════════════════════════════════════════════════════
# Feature 5 — Dendrochronology (ring-width crossdating + felling date)
# ═══════════════════════════════════════════════════════════════════════

def run_dendro_analysis(req: DendroRequest) -> DendroResponse:
    started = time.perf_counter()

    reference = build_reference_chronology()
    result = analyze_sample(
        np.asarray(req.ring_widths, dtype=float),
        reference,
        sample_id=req.sample_id,
        species=req.species,
        bark_edge=req.bark_edge,
        sapwood_rings=req.sapwood_rings,
        sapwood_complete=req.sapwood_complete,
        sapwood_min=req.sapwood_min,
        sapwood_max=req.sapwood_max,
        detrend_window=req.detrend_window,
        top_k=req.top_k,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    return DendroResponse(
        meta=DendroMetaOut(
            sample_id=result.sample_id,
            species=result.species,
            ring_count=result.ring_count,
            reference_id=reference.chronology_id,
            reference_span=f"{reference.start_year}-{reference.end_year} AD",
            processing_ms=round(elapsed_ms, 1),
        ),
        dated=result.dated,
        significant=result.significant,
        best=DateCandidateOut(**result.best.__dict__),
        felling=FellingEstimateOut(**result.felling.__dict__),
        t_margin=result.t_margin,
        widest_ring_year=result.widest_ring_year,
        mean_ring_width_mm=result.mean_ring_width_mm,
        candidates=[DateCandidateOut(**c.__dict__) for c in result.candidates],
        detrended_sample=result.detrended_sample,
        detrended_reference=result.detrended_reference,
    )


@app.post("/api/v1/dendro/analyze", response_model=DendroResponse)
async def analyze_dendro(req: DendroRequest) -> DendroResponse:
    try:
        result = run_dendro_analysis(req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info(
        "sample=%s rings=%d dated=%s end_year=%d t=%.1f felling>=%d in %.0fms",
        req.sample_id,
        result.meta.ring_count,
        result.dated,
        result.best.end_year,
        result.best.t_value,
        result.felling.earliest,
        result.meta.processing_ms,
    )
    return result


@app.get("/api/v1/dendro/reference")
async def dendro_reference() -> dict:
    ref = build_reference_chronology()
    return {
        "chronology_id": ref.chronology_id,
        "species": ref.species,
        "start_year": ref.start_year,
        "end_year": ref.end_year,
        "widths": ref.widths.tolist(),
    }


@app.get("/api/v1/dendro/demo")
async def dendro_demo() -> dict:
    """Synthetic oak core sample #012 (crossdates to a last ring of 1242 AD).

    Heartwood only (no bark), so the reconstructed felling is a terminus
    post quem — consistent with the Gothic-phase radiocarbon window.
    POST it unchanged to /api/v1/dendro/analyze.
    """
    return {
        "sample_id": "CORE-012",
        "species": "Quercus robur",
        "ring_widths": generate_core_sample().tolist(),
        "bark_edge": False,
        "sapwood_rings": None,
        "sapwood_complete": False,
    }


# ═══════════════════════════════════════════════════════════════════════
# Feature 6 — Radiocarbon calibration (conventional 14C age -> calendar)
# ═══════════════════════════════════════════════════════════════════════

@app.post("/api/v1/radiocarbon/calibrate", response_model=CalibrationResponse)
async def calibrate_radiocarbon(req: CalibrationRequest) -> CalibrationResponse:
    started = time.perf_counter()
    try:
        result = RadiocarbonCalibrator().calibrate(
            req.radiocarbon_age_bp, req.uncertainty_bp
        )
    except CalibrationOutOfRangeError as exc:
        raise HTTPException(
            status_code=422, detail={"message": str(exc), **exc.detail}
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    logger.info(
        "sample=%s %.0f+/-%.0f BP -> median %d cal AD, %d 1s-ranges in %.0fms",
        req.sample_id, req.radiocarbon_age_bp, req.uncertainty_bp,
        result.median_year, len(result.hpd_68), elapsed_ms,
    )
    return CalibrationResponse(
        meta=CalibrationMetaOut(
            sample_id=req.sample_id,
            lab_code=req.lab_code,
            radiocarbon_age_bp=req.radiocarbon_age_bp,
            uncertainty_bp=req.uncertainty_bp,
            curve_id="AETHEL-INTCAL-STYLE-700BC-1950AD",
            processing_ms=round(elapsed_ms, 1),
        ),
        median_year=result.median_year,
        hpd_68=[YearRangeOut(**r.__dict__) for r in result.hpd_68],
        hpd_95=[YearRangeOut(**r.__dict__) for r in result.hpd_95],
        intercepts=result.intercepts,
        cal_years=result.cal_years,
        density=result.density,
    )


@app.get("/api/v1/radiocarbon/curve")
async def radiocarbon_curve() -> dict:
    curve = calibration_curve()
    return {
        "curve_id": "AETHEL-INTCAL-STYLE-700BC-1950AD",
        "cal_start_ad": CURVE_START_AD,
        "cal_end_ad": CURVE_END_AD,
        "cal_years": curve.cal_years.tolist(),
        "c14_bp": [round(v, 1) for v in curve.c14_bp.tolist()],
        "sigma_bp": [round(v, 1) for v in curve.sigma_bp.tolist()],
    }


@app.get("/api/v1/radiocarbon/demo")
async def radiocarbon_demo() -> dict:
    """Worked payload: Gothic-vault mortar charcoal, 760 +/- 25 BP.

    Calibrates to ~1240-1280 cal AD, consistent with the dossier's
    dendro felling (>= 1251 AD) and stratigraphic Gothic phase.
    POST it unchanged to /api/v1/radiocarbon/calibrate.
    """
    return {
        "sample_id": "VAULT-CHARCOAL-07",
        "lab_code": "AeL-2207",
        "radiocarbon_age_bp": 760.0,
        "uncertainty_bp": 25.0,
    }
