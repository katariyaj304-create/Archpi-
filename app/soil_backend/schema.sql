-- ============================================================================
-- ArchPi Geotechnical Diagnostic Hub — PostGIS schema (Task 1)
-- Target: Supabase (PostgreSQL 15+). Run in the Supabase SQL editor.
-- ============================================================================

-- Enable spatial support and UUID generation
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ----------------------------------------------------------------------------
-- regional_diagnostics: one row per indexed geotechnical region of India.
-- boundary is a WGS84 (SRID 4326) polygon; seismic_risk_score is a normalized
-- 0..1 baseline derived from IS 1893 seismic zoning (Zone II ~0.2 .. Zone V ~0.9).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS regional_diagnostics (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    region_name         TEXT NOT NULL UNIQUE,
    boundary            GEOMETRY(Polygon, 4326) NOT NULL,
    seismic_risk_score  DOUBLE PRECISION NOT NULL
                        CHECK (seismic_risk_score >= 0 AND seismic_risk_score <= 1),
    seismic_zone        TEXT NOT NULL DEFAULT 'III',   -- IS 1893 zone label (II..V)
    dominant_soil       TEXT,                          -- headline soil family for the region
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Spatial index so ST_Contains point-in-polygon lookups stay O(log n)
CREATE INDEX IF NOT EXISTS idx_regional_diagnostics_boundary
    ON regional_diagnostics
    USING GIST (boundary);

-- ----------------------------------------------------------------------------
-- Seed data: simplified (coarse) boundaries for India's major geotechnical
-- provinces. Coordinates are lon lat (WGS84). These deliberately trade
-- cartographic precision for fast, dependency-free seeding; replace with
-- survey-grade polygons when available.
-- ----------------------------------------------------------------------------
INSERT INTO regional_diagnostics (region_name, boundary, seismic_risk_score, seismic_zone, dominant_soil) VALUES
(
    'Himalayan Seismic Belt',
    ST_GeomFromText('POLYGON((73.0 35.5, 78.5 32.5, 84.0 29.5, 89.0 28.0, 95.0 28.5, 97.5 28.8, 97.5 30.5, 90.0 30.5, 80.0 34.0, 74.5 37.0, 73.0 35.5))', 4326),
    0.92, 'V', 'Colluvial / fractured rock'
),
(
    'Northeast Hills',
    ST_GeomFromText('POLYGON((89.5 22.0, 97.5 21.5, 97.5 28.5, 89.5 27.0, 89.5 22.0))', 4326),
    0.90, 'V', 'Residual lateritic / alluvial'
),
(
    'Indo-Gangetic Alluvial Plain',
    ST_GeomFromText('POLYGON((72.5 30.5, 75.5 28.5, 80.0 25.5, 86.0 23.8, 91.5 23.5, 92.0 25.5, 89.0 26.2, 84.0 27.5, 78.0 30.5, 73.5 32.5, 72.5 30.5))', 4326),
    0.70, 'IV', 'Deep alluvium (silty sand / clay)'
),
(
    'Thar Desert Region',
    ST_GeomFromText('POLYGON((68.5 23.8, 75.0 24.5, 75.5 29.5, 69.5 28.5, 68.5 23.8))', 4326),
    0.45, 'III', 'Aeolian sand'
),
(
    'Deccan Trap (Black Cotton Belt)',
    ST_GeomFromText('POLYGON((73.8 15.8, 80.0 15.5, 80.5 22.5, 74.5 23.5, 73.6 19.0, 73.8 15.8))', 4326),
    0.35, 'III', 'Expansive black cotton clay'
),
(
    'Western Coastal Zone',
    ST_GeomFromText('POLYGON((72.0 21.0, 73.6 21.0, 76.4 8.0, 74.6 8.0, 72.0 21.0))', 4326),
    0.60, 'III', 'Marine clay / lateritic'
),
(
    'Eastern Coastal Zone',
    ST_GeomFromText('POLYGON((79.8 7.9, 81.6 7.9, 87.5 21.5, 85.4 22.3, 80.0 13.0, 79.8 7.9))', 4326),
    0.55, 'III', 'Deltaic alluvium / marine clay'
),
(
    'South Peninsular Craton',
    ST_GeomFromText('POLYGON((74.6 8.2, 79.6 8.2, 80.2 15.0, 78.0 17.0, 74.2 14.0, 74.6 8.2))', 4326),
    0.25, 'II', 'Residual red soil over gneiss'
)
ON CONFLICT (region_name) DO NOTHING;

-- ----------------------------------------------------------------------------
-- Example point-in-polygon lookup used by the API (Mumbai: 19.076 N, 72.877 E):
--
-- SELECT region_name, seismic_risk_score, seismic_zone
-- FROM regional_diagnostics
-- WHERE ST_Contains(boundary, ST_SetSRID(ST_MakePoint(72.877, 19.076), 4326))
-- ORDER BY ST_Area(boundary) ASC   -- prefer the most specific region on overlap
-- LIMIT 1;
-- ----------------------------------------------------------------------------
