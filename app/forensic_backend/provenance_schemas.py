"""Pydantic request/response schemas + GeoJSON assembly for the
Provenance Map Engine.

The GeoJSON payload is a standard FeatureCollection (RFC 7946) with three
features: the matched origin quarry (Point, carrying the isotopic match
statistics), the construction site (Point), and the least-cost trade
route (LineString, carrying the transport economics). Coordinates are
[lon, lat] per the specification.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .isotopes import QuarryMatch
from .routing import TradeRoute


# ── Requests ─────────────────────────────────────────────────────────────

class IsotopeSampleIn(BaseModel):
    """One unknown sample's IRMS panel; at least two systems required."""

    sample_id: str = Field("UNLABELLED", max_length=64)
    delta13c: float | None = Field(
        None, ge=-50, le=50, description="carbonate delta 13C [permil VPDB]"
    )
    delta18o: float | None = Field(
        None, ge=-50, le=50, description="carbonate delta 18O [permil VPDB]"
    )
    sr8786: float | None = Field(
        None, ge=0.68, le=0.80, description="87Sr/86Sr ratio [-]"
    )

    @model_validator(mode="after")
    def two_axes_minimum(self) -> "IsotopeSampleIn":
        measured = [v for v in (self.delta13c, self.delta18o, self.sr8786) if v is not None]
        if len(measured) < 2:
            raise ValueError("at least two isotope systems must be measured")
        return self


class MatchRequest(IsotopeSampleIn):
    top_k: int = Field(3, ge=1, le=10, description="ranked candidates to return")


class SitePointIn(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    name: str = Field("CONSTRUCTION SITE", max_length=96)


class RouteRequest(BaseModel):
    """Standalone routing: from a registry quarry or arbitrary coordinates."""

    source_quarry_id: str | None = Field(None, max_length=64)
    source: SitePointIn | None = None
    destination: SitePointIn

    @model_validator(mode="after")
    def one_source(self) -> "RouteRequest":
        if (self.source_quarry_id is None) == (self.source is None):
            raise ValueError("provide exactly one of source_quarry_id or source")
        return self


class AnalyzeRequest(IsotopeSampleIn):
    """Full pipeline: isotopic match -> least-cost route -> GeoJSON."""

    destination: SitePointIn
    top_k: int = Field(3, ge=1, le=10)


# ── Responses ────────────────────────────────────────────────────────────

class QuarryOut(BaseModel):
    quarry_id: str
    name: str
    material: str
    lat: float
    lon: float
    delta13c: float
    delta13c_sd: float
    delta18o: float
    delta18o_sd: float
    sr8786: float
    sr8786_sd: float


class QuarryMatchOut(BaseModel):
    quarry_id: str
    name: str
    material: str
    lat: float
    lon: float
    distance_sigma: float
    confidence_pct: float = Field(..., description="posterior across the registry [%]")
    consistency_p: float = Field(..., description="absolute chi-square goodness of fit")
    plausible: bool
    z_scores: dict[str, float]
    axes_used: list[str]


class RouteLegOut(BaseModel):
    mode: Literal["sea", "river", "plain", "mountain"]
    distance_km: float
    start: tuple[float, float]   # (lon, lat)
    end: tuple[float, float]


class TradeRouteOut(BaseModel):
    total_km: float
    total_cost: float = Field(..., description="friction-weighted cost units")
    km_by_mode: dict[str, float]
    legs: list[RouteLegOut]
    transshipments: int
    source_snap_km: float
    destination_snap_km: float


class GeometryOut(BaseModel):
    type: Literal["Point", "LineString"]
    coordinates: Any  # [lon, lat] or [[lon, lat], ...]


class FeatureOut(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: GeometryOut
    properties: dict[str, Any]


class FeatureCollectionOut(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[FeatureOut]


class ProvenanceMetaOut(BaseModel):
    sample_id: str
    destination_name: str
    best_quarry_id: str
    confidence_pct: float
    plausible: bool
    grid_resolution_deg: float
    processing_ms: float


class MatchResponse(BaseModel):
    sample_id: str
    matches: list[QuarryMatchOut]


class RouteResponse(BaseModel):
    route: TradeRouteOut
    geojson: FeatureCollectionOut


class AnalyzeResponse(BaseModel):
    meta: ProvenanceMetaOut
    matches: list[QuarryMatchOut]
    route: TradeRouteOut
    geojson: FeatureCollectionOut


# ── GeoJSON assembly ─────────────────────────────────────────────────────

def match_out(m: QuarryMatch) -> QuarryMatchOut:
    return QuarryMatchOut(**m.__dict__)


def route_out(r: TradeRoute) -> TradeRouteOut:
    return TradeRouteOut(
        total_km=r.total_km,
        total_cost=r.total_cost,
        km_by_mode=r.km_by_mode,
        legs=[RouteLegOut(**l.__dict__) for l in r.legs],
        transshipments=r.transshipments,
        source_snap_km=r.source_snap_km,
        destination_snap_km=r.destination_snap_km,
    )


def origin_feature(match: QuarryMatch) -> FeatureOut:
    return FeatureOut(
        geometry=GeometryOut(type="Point", coordinates=[match.lon, match.lat]),
        properties={
            "role": "origin_quarry",
            "quarry_id": match.quarry_id,
            "name": match.name,
            "material": match.material,
            "confidence_pct": match.confidence_pct,
            "distance_sigma": match.distance_sigma,
            "consistency_p": match.consistency_p,
            "plausible": match.plausible,
            "z_scores": match.z_scores,
            "axes_used": match.axes_used,
        },
    )


def destination_feature(site: SitePointIn) -> FeatureOut:
    return FeatureOut(
        geometry=GeometryOut(type="Point", coordinates=[site.lon, site.lat]),
        properties={"role": "destination", "name": site.name},
    )


def route_feature(route: TradeRoute) -> FeatureOut:
    return FeatureOut(
        geometry=GeometryOut(
            type="LineString",
            coordinates=[[lon, lat] for lon, lat in route.coordinates],
        ),
        properties={
            "role": "trade_route",
            "total_km": route.total_km,
            "total_cost": route.total_cost,
            "km_by_mode": route.km_by_mode,
            "transshipments": route.transshipments,
            "legs": [
                {"mode": l.mode, "distance_km": l.distance_km}
                for l in route.legs
            ],
        },
    )


def feature_collection(
    match: QuarryMatch, site: SitePointIn, route: TradeRoute
) -> FeatureCollectionOut:
    """Origin point + destination point + trade-route LineString."""
    return FeatureCollectionOut(features=[
        origin_feature(match),
        destination_feature(site),
        route_feature(route),
    ])
