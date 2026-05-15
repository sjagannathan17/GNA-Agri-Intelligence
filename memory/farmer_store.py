"""
Farmer Store
------------
CRUD operations for farmer profiles and season stats.
Uses SQLite for local dev; swap DATABASE_URL for PostgreSQL in production.
"""

import csv
import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = os.environ.get("DATABASE_URL", "gna.db").replace("sqlite:///./", "")

logger = logging.getLogger(__name__)


def init_db():
    """Initialize the schema, applying lightweight column migrations between
    CREATE TABLE and CREATE INDEX so the new columns (risk_tier, topic, etc.)
    exist before any index references them."""
    schema_path = Path(__file__).parent / "schema.sql"
    schema_sql = schema_path.read_text()

    # Split into individual statements, skip blank lines / comment-only lines
    statements: list[str] = []
    for raw in schema_sql.split(";"):
        stmt = "\n".join(line for line in raw.splitlines() if not line.strip().startswith("--")).strip()
        if stmt:
            statements.append(stmt)

    with get_conn() as conn:
        # 1) CREATE TABLE statements first
        for stmt in statements:
            if stmt.upper().startswith("CREATE TABLE"):
                conn.execute(stmt)
        # 2) Lightweight migrations to backfill new columns
        _apply_lightweight_migrations(conn)
        # 3) CREATE INDEX (now safe to reference the new columns)
        for stmt in statements:
            if stmt.upper().startswith("CREATE INDEX"):
                conn.execute(stmt)
    print("Database initialized.")


def _apply_lightweight_migrations(conn):
    """Idempotent ALTER TABLE migrations for installs created before
    risk_tier / consecutive_help / camp_name / district_name existed."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(farmers)").fetchall()}
    additions = {
        "risk_tier":         "ALTER TABLE farmers ADD COLUMN risk_tier TEXT DEFAULT 'Low'",
        "consecutive_help":  "ALTER TABLE farmers ADD COLUMN consecutive_help INTEGER DEFAULT 0",
        "camp_name":         "ALTER TABLE farmers ADD COLUMN camp_name TEXT",
        "district_name":     "ALTER TABLE farmers ADD COLUMN district_name TEXT",
    }
    for col, ddl in additions.items():
        if col not in existing:
            try:
                conn.execute(ddl)
                logger.info(f"Migration: added {col} to farmers")
            except sqlite3.OperationalError as e:
                logger.warning(f"Migration skipped for {col}: {e}")

    existing_conv = {row["name"] for row in conn.execute("PRAGMA table_info(conversations)").fetchall()}
    conv_additions = {
        "topic":     "ALTER TABLE conversations ADD COLUMN topic TEXT",
        "language":  "ALTER TABLE conversations ADD COLUMN language TEXT",
    }
    for col, ddl in conv_additions.items():
        if col not in existing_conv:
            try:
                conn.execute(ddl)
                logger.info(f"Migration: added {col} to conversations")
            except sqlite3.OperationalError as e:
                logger.warning(f"Migration skipped for {col}: {e}")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def derive_tier(risk_score: float) -> str:
    """Match the canonical thresholds from notebook §6."""
    if risk_score >= 0.35:
        return "High"
    if risk_score >= 0.20:
        return "Medium"
    return "Low"


class FarmerStore:
    def get(self, farmer_id: str) -> dict | None:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM farmers WHERE farmer_id = ?", (farmer_id,)
            ).fetchone()
            return self._parse(row)

    def get_by_phone(self, phone: str) -> dict | None:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM farmers WHERE phone = ?", (phone,)
            ).fetchone()
            return self._parse(row)

    def get_all_active(self) -> list[dict]:
        with get_conn() as conn:
            rows = conn.execute("SELECT * FROM farmers").fetchall()
            return [self._parse(r) for r in rows]

    def update_risk_score(self, farmer_id: str, score: float):
        tier = derive_tier(score)
        with get_conn() as conn:
            conn.execute(
                "UPDATE farmers SET risk_score = ?, risk_tier = ? WHERE farmer_id = ?",
                (round(score, 4), tier, farmer_id),
            )

    def set_risk_tier(self, farmer_id: str, tier: str):
        with get_conn() as conn:
            conn.execute(
                "UPDATE farmers SET risk_tier = ? WHERE farmer_id = ?",
                (tier, farmer_id),
            )

    def update_last_nudge(self, farmer_id: str):
        with get_conn() as conn:
            conn.execute(
                "UPDATE farmers SET last_nudge_sent = CURRENT_TIMESTAMP WHERE farmer_id = ?",
                (farmer_id,),
            )

    def log_nudge_response(self, farmer_id: str, reply: str):
        with get_conn() as conn:
            row = conn.execute(
                "SELECT nudge_responses, consecutive_help FROM farmers WHERE farmer_id = ?",
                (farmer_id,),
            ).fetchone()
            responses = json.loads(row["nudge_responses"]) if row else {"total": 0, "done": 0, "help": 0, "skip": 0}
            responses["total"] = responses.get("total", 0) + 1
            responses[reply] = responses.get(reply, 0) + 1

            current_help = (row["consecutive_help"] or 0) if row else 0
            consecutive_help = current_help + 1 if reply == "help" else 0

            conn.execute(
                "UPDATE farmers SET nudge_responses = ?, consecutive_help = ? WHERE farmer_id = ?",
                (json.dumps(responses), consecutive_help, farmer_id),
            )

    def reset_consecutive_help(self, farmer_id: str):
        with get_conn() as conn:
            conn.execute(
                "UPDATE farmers SET consecutive_help = 0 WHERE farmer_id = ?",
                (farmer_id,),
            )

    def get_agent(self, agent_id: str) -> dict | None:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM field_agents WHERE agent_id = ?", (agent_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_zone_field_agent(self, zone: str) -> dict | None:
        """Return any field agent assigned to the farmer's zone (for ad-hoc escalation)."""
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM field_agents WHERE zone = ? LIMIT 1", (zone,)
            ).fetchone()
            return dict(row) if row else None

    def get_season_stats(self) -> dict:
        with get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM farmers").fetchone()[0]
            high_risk = conn.execute(
                "SELECT COUNT(*) FROM farmers WHERE risk_tier = 'High'"
            ).fetchone()[0] or conn.execute(
                "SELECT COUNT(*) FROM farmers WHERE risk_score >= 0.35"
            ).fetchone()[0]
            return {
                "total_farmers": total,
                "buyback_rate": 0.77,
                "high_risk_count": high_risk,
                "forecast_low": 2_100_000,
                "forecast_high": 3_200_000,
                "top_risk_zone": "IIa",
            }

    def get_zone_breakdown(self) -> list[dict]:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT zone, COUNT(*) as count, AVG(risk_score) as avg_risk FROM farmers GROUP BY zone"
            ).fetchall()
            return [dict(r) for r in rows]

    def backfill_risk_tiers_from_csv(self, high_risk_csv: str | None = None) -> dict:
        """Mark farmers as High Risk if they appear in the canonical
        cleaned_dataset/high_risk_farmers.csv export from the notebook.
        Other farmers are tiered from their stored risk_score.

        Returns counts: {"high": N, "medium": N, "low": N, "missing_score": N}.
        """
        path = high_risk_csv or os.environ.get(
            "GNA_HIGH_RISK_CSV", "../cleaned_dataset/high_risk_farmers.csv"
        )
        high_ids: set[str] = set()
        try:
            with open(path, newline="") as f:
                for row in csv.DictReader(f):
                    if "farmer_id" in row:
                        high_ids.add(str(row["farmer_id"]))
        except FileNotFoundError:
            logger.warning(f"high_risk CSV not found at {path}; falling back to score thresholds only")

        counts = {"high": 0, "medium": 0, "low": 0, "missing_score": 0}
        with get_conn() as conn:
            farmers = conn.execute(
                "SELECT farmer_id, risk_score FROM farmers"
            ).fetchall()
            for row in farmers:
                fid = str(row["farmer_id"])
                score = row["risk_score"] or 0.0
                if fid in high_ids:
                    tier = "High"
                elif score >= 0.35:
                    tier = "High"
                elif score >= 0.20:
                    tier = "Medium"
                elif score == 0.0:
                    tier = "Low"
                    counts["missing_score"] += 1
                else:
                    tier = "Low"

                conn.execute(
                    "UPDATE farmers SET risk_tier = ? WHERE farmer_id = ?",
                    (tier, fid),
                )
                counts[tier.lower()] += 1
        logger.info(f"backfill_risk_tiers_from_csv: {counts}")
        return counts

    def _parse(self, row) -> dict | None:
        if not row:
            return None
        d = dict(row)
        if "nudge_responses" in d and isinstance(d["nudge_responses"], str):
            d["nudge_responses"] = json.loads(d["nudge_responses"])
        return d
