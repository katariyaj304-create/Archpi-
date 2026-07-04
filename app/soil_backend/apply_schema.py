"""One-command PostGIS activation for the Diagnostic Hub.

Usage (from the app/ directory, after adding DATABASE_URL to .env):

    python -m soil_backend.apply_schema

Applies schema.sql (extensions, regional_diagnostics table, GIST index,
seed polygons) to the database in DATABASE_URL, then verifies the spatial
lookup with a known coordinate. Safe to re-run: everything in the schema
is IF NOT EXISTS / ON CONFLICT DO NOTHING.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Optional argv[1] selects which schema file to apply (default: schema.sql)
SCHEMA_PATH = Path(__file__).with_name(sys.argv[1] if len(sys.argv) > 1 else "schema.sql")


async def main() -> int:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set. Add it to app/.env, e.g.:")
        print("  DATABASE_URL=postgresql+asyncpg://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres")
        print("(Supabase dashboard -> Connect -> Session pooler; swap postgresql:// for postgresql+asyncpg://)")
        return 1
    # Plain asyncpg here (not SQLAlchemy): its simple-query protocol runs the
    # whole multi-statement schema.sql in one call, which prepared statements
    # cannot.
    url = url.replace("postgresql+asyncpg://", "postgresql://", 1)

    import asyncpg

    conn = await asyncpg.connect(dsn=url)
    sql = SCHEMA_PATH.read_text(encoding="utf-8")

    try:
        await conn.execute(sql)
        print(f"Applied {SCHEMA_PATH.name}")

        if SCHEMA_PATH.name != "schema.sql":
            return 0  # the Mumbai verification below is for the soil schema only

        count = await conn.fetchval("SELECT count(*) FROM regional_diagnostics")
        row = await conn.fetchrow(
            "SELECT region_name, seismic_zone FROM regional_diagnostics "
            "WHERE ST_Contains(boundary, ST_SetSRID(ST_MakePoint(72.877, 19.076), 4326)) "
            "ORDER BY ST_Area(boundary) ASC LIMIT 1"
        )
        print(f"Regions seeded: {count}")
        if row:
            print(f"Spatial lookup OK — Mumbai (19.076, 72.877) -> {row['region_name']} (zone {row['seismic_zone']})")
            print("PostGIS path is active. Restart the API; region source will show 'postgis'.")
            return 0
        print("Schema applied but the Mumbai test lookup matched no region — check the seed data.")
        return 1
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
