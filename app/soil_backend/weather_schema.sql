-- ============================================================================
-- ArchPi Environmental Dynamics — weather time-series ledger
-- Target: Supabase (PostgreSQL 15+). Applied via:
--     python -m soil_backend.apply_schema weather_schema.sql
-- ============================================================================

-- One row per /environment/stress query: the raw meteorology and computed
-- structural metrics, stored chronologically so trends (24h thermal loading,
-- carbonation exposure-hours, wind pressure history) can be queried later.
CREATE TABLE IF NOT EXISTS weather_events (
    id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    observed_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    lat                       DOUBLE PRECISION NOT NULL,
    lon                       DOUBLE PRECISION NOT NULL,
    temperature_c             DOUBLE PRECISION,
    relative_humidity_pct     DOUBLE PRECISION,
    wind_speed_kmh            DOUBLE PRECISION,
    wind_shear_kn_m2          DOUBLE PRECISION,
    carbonation_label         TEXT,
    carbonation_multiplier    DOUBLE PRECISION,
    thermal_swing_c           DOUBLE PRECISION
);

-- Time-series access pattern: latest first, filtered by location
CREATE INDEX IF NOT EXISTS idx_weather_events_observed_at
    ON weather_events (observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_weather_events_location
    ON weather_events (lat, lon, observed_at DESC);
