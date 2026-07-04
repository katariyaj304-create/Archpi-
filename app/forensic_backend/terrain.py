"""Historical travel-friction cost surface for the Mediterranean basin.

A regular lat/lon grid (default 0.5 degree) spanning the Roman-era trade
world (10W-40E, 22N-48N) is classified into four terrain modes by
point-in-polygon tests against a coarse, hand-digitized geography held in
a GeoPandas GeoDataFrame:

* sea       — anything outside the land polygons;
* river     — the Nile corridor (navigable bulk transport);
* plain     — land;
* mountain  — land inside a mountain-range polygon (Alps, Pyrenees,
              Apennines, Dinarides/Pindus, Taurus, Atlas).

Friction weights follow the relative freight costs of Diocletian's Price
Edict (sea : river : land roughly 1 : 5 : 25-60): moving one kilometre
across a cell costs distance * friction, so maritime legs dominate any
optimal route while mountain crossings are strongly penalized. Switching
between water (sea/river) and land additionally pays a fixed transshipment
penalty representing port handling.

The coastline is deliberately coarse (~0.5 degree fidelity, matching the
grid): it is a routing cost model, not a cartographic product.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import geopandas as gpd
import numpy as np
from shapely.geometry import Point, Polygon

BBOX = (-10.0, 22.0, 40.0, 48.0)  # lon_min, lat_min, lon_max, lat_max
RESOLUTION_DEG = 0.5
EARTH_RADIUS_KM = 6371.0

#: cost multiplier per kilometre travelled, by terrain mode
FRICTION = {"sea": 1.0, "river": 5.0, "plain": 25.0, "mountain": 60.0}
#: fixed cost for a water<->land mode change (port handling), in cost units
TRANSSHIPMENT_PENALTY = 40.0
WATER_MODES = frozenset({"sea", "river"})


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS84 points [km]."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = np.radians(lat2 - lat1), np.radians(lon2 - lon1)
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return float(2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a)))


def _poly(*lonlat: tuple[float, float]) -> Polygon:
    # make_valid guards against hand-digitization slips (self-intersections)
    from shapely.validation import make_valid
    return make_valid(Polygon(list(lonlat)))


# ── Coarse hand-digitized geography (lon, lat vertices) ─────────────────

_LAND = {
    # Southern Europe: one landmass traced along the north Mediterranean
    # coast from Iberia through Italy and the Balkans to the Bosphorus,
    # closed far north of the grid so all non-coastal cells are land.
    "europe": _poly(
        (-9.9, 48.0), (-9.9, 38.7), (-8.9, 37.0), (-6.3, 36.5), (-5.5, 36.0),
        (-4.4, 36.7), (-2.1, 36.7), (-0.3, 37.6), (0.2, 38.7), (1.0, 41.1),
        (3.2, 41.9), (3.0, 43.0), (4.8, 43.3), (6.6, 43.1), (8.8, 44.4),
        (10.2, 43.9), (10.5, 42.9), (11.7, 42.2), (12.2, 41.7), (13.6, 41.2),
        (14.0, 40.8), (15.0, 40.2), (15.9, 39.0), (15.65, 38.25),
        (16.1, 37.95), (17.1, 38.9), (16.55, 39.8), (17.2, 40.45),
        (18.5, 40.1), (18.0, 40.65), (16.9, 41.1), (16.1, 41.9), (15.4, 41.9),
        (14.0, 42.6), (13.6, 43.6), (12.5, 44.2), (12.3, 45.1), (13.6, 45.6),
        (13.9, 44.8), (15.2, 44.2), (16.4, 43.5), (18.1, 42.6), (19.1, 41.9),
        (19.4, 41.3), (19.5, 40.2), (20.0, 39.7), (20.7, 38.8), (21.1, 38.35),
        (21.3, 37.6), (21.9, 36.7), (22.4, 36.5), (22.6, 36.9), (23.2, 36.4),
        (23.5, 37.5), (23.0, 37.9), (23.7, 37.9), (24.05, 37.65),
        (23.8, 38.4), (23.0, 39.05), (22.6, 39.5), (23.3, 40.2), (24.0, 40.4),
        (24.7, 40.85), (25.9, 40.85), (26.6, 40.4), (27.5, 40.97),
        (29.0, 41.0), (29.0, 41.3), (28.0, 41.6), (28.0, 48.0),
    ),
    # Africa + Levant + Anatolia: one landmass traced along the south and
    # east Mediterranean coast, closed far south/east of interest.
    "africa_levant_anatolia": _poly(
        (-10.0, 22.0), (-10.0, 35.7), (-5.9, 35.8), (-2.2, 35.1), (0.0, 35.8),
        (2.9, 36.8), (6.9, 37.08), (9.8, 37.3), (10.3, 36.8), (11.05, 37.05),
        (10.6, 35.8), (10.7, 34.7), (10.1, 33.9), (11.5, 33.5), (13.2, 32.9),
        (15.3, 32.4), (17.3, 31.1), (19.0, 30.3), (20.05, 32.1), (21.7, 32.8),
        (23.1, 32.6), (25.0, 31.6), (29.9, 31.2), (31.1, 31.55), (32.3, 31.3),
        (33.5, 31.1), (34.3, 31.5), (34.75, 32.1), (35.0, 32.8), (35.5, 33.9),
        (35.8, 35.5), (36.0, 36.2), (36.2, 36.6), (35.6, 36.55),
        (34.6, 36.75), (32.8, 36.1), (30.7, 36.35), (30.6, 36.9),
        (29.1, 36.35), (28.2, 36.7), (27.3, 37.0), (27.2, 37.9), (26.7, 38.4),
        (26.6, 39.1), (26.9, 39.5), (26.2, 40.0), (27.9, 40.35), (29.1, 40.65),
        (29.15, 41.0), (30.0, 41.2), (32.0, 41.6), (36.0, 41.2), (40.0, 41.0),
        (40.0, 22.0),
    ),
    # Major islands (quarry-bearing islets get small explicit footprints
    # so their nodes classify as land and pay a real port transfer).
    "sicily": _poly((12.4, 38.0), (13.4, 38.3), (14.9, 38.15), (15.6, 38.27),
                    (15.3, 37.6), (15.1, 36.7), (14.5, 36.7), (12.8, 37.55)),
    "sardinia": _poly((8.2, 40.9), (9.2, 41.25), (9.8, 40.5), (9.6, 39.1),
                      (9.0, 38.9), (8.4, 38.9), (8.1, 39.9)),
    "corsica": _poly((8.6, 42.35), (9.4, 43.0), (9.55, 42.1), (9.2, 41.4),
                     (8.8, 41.5)),
    "crete": _poly((23.5, 35.3), (24.7, 35.4), (26.3, 35.3), (26.1, 35.0),
                   (24.8, 34.9), (23.6, 35.0)),
    "cyprus": _poly((32.3, 35.1), (33.6, 35.4), (34.5, 35.6), (34.0, 34.9),
                    (32.4, 34.7)),
    "mallorca": _poly((2.3, 39.3), (3.5, 39.95), (3.5, 39.3)),
    "cyclades": _poly((24.8, 36.9), (24.8, 37.3), (25.7, 37.3), (25.7, 36.9)),
    "thasos_isle": _poly((24.45, 40.55), (24.45, 40.85), (24.85, 40.85),
                         (24.85, 40.55)),
    "marmara_isle": _poly((27.3, 40.45), (27.3, 40.75), (27.75, 40.75),
                          (27.75, 40.45)),
}

_MOUNTAIN = {
    "alps": _poly((5.8, 44.0), (7.5, 45.3), (10.0, 46.9), (13.5, 47.4),
                  (15.8, 47.3), (15.0, 46.2), (11.5, 46.0), (8.7, 45.6),
                  (7.4, 44.7), (6.4, 43.95)),
    "pyrenees": _poly((-1.8, 42.7), (2.8, 42.3), (3.2, 42.5), (0.5, 43.1),
                      (-1.8, 43.2)),
    "apennines": _poly((8.5, 44.3), (10.5, 44.3), (12.5, 43.2), (14.0, 42.0),
                       (15.9, 40.7), (16.2, 39.3), (15.9, 38.6), (15.5, 38.7),
                       (14.8, 40.2), (13.2, 41.7), (11.5, 43.0), (9.0, 44.0)),
    "dinarides_pindus": _poly((14.0, 45.3), (16.5, 44.5), (19.0, 43.2),
                              (20.8, 41.8), (21.6, 40.0), (21.9, 38.9),
                              (21.0, 38.9), (20.1, 40.3), (18.5, 42.5),
                              (15.5, 44.6)),
    "taurus": _poly((29.5, 37.2), (32.0, 37.4), (35.0, 37.6), (36.5, 37.9),
                    (36.3, 36.9), (34.0, 36.9), (30.2, 36.6)),
    "atlas": _poly((-9.5, 29.8), (-5.5, 32.5), (-2.0, 34.0), (1.0, 35.0),
                   (6.0, 35.4), (9.5, 34.2), (5.0, 33.8), (0.0, 33.4),
                   (-6.0, 31.0), (-9.8, 28.8)),
}

_RIVER = {
    # Nile corridor, Aswan to the delta: navigable bulk-freight artery.
    "nile": _poly((32.6, 23.8), (33.3, 23.8), (33.0, 26.5), (31.9, 29.5),
                  (31.7, 31.6), (30.7, 31.6), (30.9, 29.4), (32.2, 26.3)),
}


def terrain_frame() -> gpd.GeoDataFrame:
    """The digitized geography as a GeoDataFrame (name, kind, geometry)."""
    records = (
        [{"name": n, "kind": "land"} for n in _LAND]
        + [{"name": n, "kind": "mountain"} for n in _MOUNTAIN]
        + [{"name": n, "kind": "river"} for n in _RIVER]
    )
    geoms = list(_LAND.values()) + list(_MOUNTAIN.values()) + list(_RIVER.values())
    return gpd.GeoDataFrame(records, geometry=geoms, crs="EPSG:4326")


@dataclass(frozen=True)
class GridCell:
    """One cost-surface node."""

    i: int          # column (longitude index)
    j: int          # row (latitude index)
    lon: float      # cell centre
    lat: float
    mode: str       # sea | river | plain | mountain
    friction: float


class CostSurface:
    """Classified lat/lon grid plus point-snapping helpers."""

    def __init__(self, resolution_deg: float = RESOLUTION_DEG) -> None:
        if not 0.1 <= resolution_deg <= 2.0:
            raise ValueError("resolution must be within 0.1-2.0 degrees")
        lon_min, lat_min, lon_max, lat_max = BBOX
        self.resolution = resolution_deg
        self.lons = np.arange(lon_min, lon_max + resolution_deg / 2, resolution_deg)
        self.lats = np.arange(lat_min, lat_max + resolution_deg / 2, resolution_deg)

        frame = terrain_frame()
        by_kind = {
            kind: gpd.GeoSeries(frame[frame.kind == kind].geometry).union_all()
            for kind in ("land", "mountain", "river")
        }
        from shapely.prepared import prep
        land, mountain, river = (prep(by_kind[k]) for k in ("land", "mountain", "river"))

        self.cells: dict[tuple[int, int], GridCell] = {}
        for i, lon in enumerate(self.lons):
            for j, lat in enumerate(self.lats):
                p = Point(float(lon), float(lat))
                if not land.contains(p):
                    mode = "sea"
                elif river.contains(p):
                    mode = "river"
                elif mountain.contains(p):
                    mode = "mountain"
                else:
                    mode = "plain"
                self.cells[(i, j)] = GridCell(
                    i=i, j=j, lon=float(lon), lat=float(lat),
                    mode=mode, friction=FRICTION[mode],
                )

    def contains(self, lat: float, lon: float) -> bool:
        lon_min, lat_min, lon_max, lat_max = BBOX
        return lon_min <= lon <= lon_max and lat_min <= lat <= lat_max

    def snap(self, lat: float, lon: float) -> GridCell:
        """Nearest grid cell to a WGS84 point (must fall inside BBOX)."""
        if not self.contains(lat, lon):
            raise ValueError(
                f"point (lat {lat}, lon {lon}) lies outside the modelled "
                f"Mediterranean grid {BBOX} (lon_min, lat_min, lon_max, lat_max)"
            )
        i = int(np.argmin(np.abs(self.lons - lon)))
        j = int(np.argmin(np.abs(self.lats - lat)))
        return self.cells[(i, j)]


@lru_cache(maxsize=2)
def default_surface(resolution_deg: float = RESOLUTION_DEG) -> CostSurface:
    """Process-wide singleton; classification runs once per resolution."""
    return CostSurface(resolution_deg)
