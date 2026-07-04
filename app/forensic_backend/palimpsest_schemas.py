"""Pydantic request/response schemas for the Chronological Palimpsest API."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

RelationKind = Literal["built_after", "replaces", "cuts"]
ConstraintKind = Literal[
    "inscription", "radiocarbon_2sigma", "dendrochronology", "archival", "other"
]


# ── Requests ─────────────────────────────────────────────────────────────

class RelationIn(BaseModel):
    earlier: str = Field(..., min_length=1, max_length=64)
    later: str = Field(..., min_length=1, max_length=64)
    kind: RelationKind = "built_after"

    @model_validator(mode="after")
    def not_self(self) -> "RelationIn":
        if self.earlier == self.later:
            raise ValueError(f"component '{self.earlier}' cannot predate itself")
        return self


class DateIntervalIn(BaseModel):
    start_year: float = Field(..., ge=-10000, le=3000, description="calendar year (negative = BC)")
    end_year: float = Field(..., ge=-10000, le=3000)
    weight: float = Field(1.0, gt=0, le=1.0, description="relative mass, e.g. 2-sigma segment probability")

    @model_validator(mode="after")
    def ordered(self) -> "DateIntervalIn":
        if self.end_year < self.start_year:
            raise ValueError(
                f"interval end {self.end_year} precedes start {self.start_year}"
            )
        return self


class DateConstraintIn(BaseModel):
    node_id: str = Field(..., min_length=1, max_length=64)
    kind: ConstraintKind = "inscription"
    intervals: list[DateIntervalIn] = Field(
        ..., min_length=1, max_length=16,
        description="union of possible calendar ranges (e.g. calibrated 2-sigma segments)",
    )


class ChemicalSignatureIn(BaseModel):
    node_id: str = Field(..., min_length=1, max_length=64)
    silica_ratio_pct: float = Field(..., ge=0, le=100, description="SiO2 fraction [%]")
    calcium_ratio_pct: float = Field(..., ge=0, le=100, description="CaO/CaCO3 fraction [%]")


class ClusteringOptionsIn(BaseModel):
    variance_threshold: float = Field(
        0.15, gt=0, le=2.0,
        description="max relative signature spread within one campaign [-]",
    )


class ValidateRequest(BaseModel):
    """Graph-only payload: components + relations, cycle detection."""

    components: list[str] = Field(..., min_length=1, max_length=500)
    relations: list[RelationIn] = Field(default_factory=list, max_length=2000)
    site_id: str = Field("UNLABELLED", max_length=64)

    @field_validator("components")
    @classmethod
    def clean_ids(cls, v: list[str]) -> list[str]:
        if any(not c or not c.strip() for c in v):
            raise ValueError("component ids must be non-empty")
        return [c.strip() for c in v]


class SequenceRequest(ValidateRequest):
    """Full pipeline: validation + Bayesian dating + campaign clustering."""

    constraints: list[DateConstraintIn] = Field(
        ..., min_length=1, max_length=500,
        description="absolute dating evidence; at least one node must be anchored",
    )
    signatures: list[ChemicalSignatureIn] = Field(default_factory=list, max_length=500)
    clustering: ClusteringOptionsIn = ClusteringOptionsIn()
    padding_years: float = Field(
        50.0, ge=0, le=2000, description="modelled span extension beyond the constraints [years]"
    )

    @field_validator("constraints")
    @classmethod
    def finite_intervals(cls, v: list[DateConstraintIn]) -> list[DateConstraintIn]:
        for c in v:
            for iv in c.intervals:
                if not (math.isfinite(iv.start_year) and math.isfinite(iv.end_year)):
                    raise ValueError(f"constraint on '{c.node_id}' has non-finite years")
        return v


# ── Responses ────────────────────────────────────────────────────────────

class GraphSummaryOut(BaseModel):
    node_count: int
    edge_count: int
    topological_order: list[str]
    depth_by_node: dict[str, int]
    roots: list[str]
    terminals: list[str]
    isolated: list[str]


class ValidateResponse(BaseModel):
    site_id: str
    valid: bool
    graph: GraphSummaryOut


class PhaseOut(BaseModel):
    node_id: str
    phase_index: int
    depth: int
    earliest_possible: float = Field(..., description="EPD, reconciled calendar year")
    latest_possible: float = Field(..., description="LPD, reconciled calendar year")
    hpd_start: float = Field(..., description="95.4% highest-density interval start")
    hpd_end: float
    median_year: float
    constrained: bool
    constraint_kinds: list[str]


class CampaignOut(BaseModel):
    campaign_id: str
    members: list[str]
    mean_silica_ratio_pct: float
    mean_calcium_ratio_pct: float
    max_signature_distance: float
    window_start: float | None = Field(None, description="latest member EPD")
    window_end: float | None = Field(None, description="earliest member LPD")


class PalimpsestMetaOut(BaseModel):
    site_id: str
    node_count: int
    edge_count: int
    constraint_count: int
    signature_count: int
    grid_start_year: float
    grid_end_year: float
    grid_step_years: float
    variance_threshold: float
    processing_ms: float


class SequenceResponse(BaseModel):
    meta: PalimpsestMetaOut
    graph: GraphSummaryOut
    phases: list[PhaseOut]
    campaigns: list[CampaignOut]
    unclustered: list[str] = Field(
        default_factory=list, description="components without a chemical signature"
    )
