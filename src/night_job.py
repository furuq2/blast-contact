from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.db import connect, log_health, update_score
from src.health import boot_check
from src.statsapi import get_schedule

log = logging.getLogger("night_job")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _yesterday_iso_et() -> str:
    return (datetime.now(ZoneInfo("America/New_York")).date() - timedelta(days=1)).isoformat()


def update_yesterday_scores() -> tuple[int, int]:
    """Pull yesterday's games and update scores. Returns (updated, total)."""
    yesterday = _yesterday_iso_et()
    games = get_schedule(yesterday)
    log.info("Found %d games on %s", len(games), yesterday)

    updated = 0
    with connect() as conn:
        for g in games:
            update_score(
                conn,
                int(g["game_pk"]),
                g["away_score"],
                g["home_score"],
                g["status"],
            )
            updated += 1
    return updated, len(games)


def post_job_assertions(yesterday: str) -> list[str]:
    fails: list[str] = []
    with connect() as conn:
        cur = conn.execute(
            "SELECT game_pk, status, away_score, home_score "
            "FROM games WHERE game_date = ?",
            (yesterday,),
        )
        for row in cur.fetchall():
            status = row["status"] or ""
            if status in ("Postponed", "Cancelled", "Suspended"):
                continue
            if row["away_score"] is None or row["home_score"] is None:
                fails.append(
                    f"game {row['game_pk']} ({status}): score missing"
                )
    return fails


def main() -> int:
    boot_check("night")
    yesterday = _yesterday_iso_et()

    try:
        updated, total = update_yesterday_scores()
        log.info("Updated %d / %d game rows for %s", updated, total, yesterday)
    except Exception as e:
        with connect() as conn:
            log_health(conn, "night", "score_update", "fail", str(e))
        log.exception("night_job failed during score update")
        return 1

    try:
        from src.spreadsheet import write_csv
        wrote = write_csv()
        log.info("Wrote %d rows to data/master.csv", wrote)
    except Exception as e:
        with connect() as conn:
            log_health(conn, "night", "csv_write", "fail", str(e))
        log.exception("night_job failed during CSV write")
        return 1

    fails = post_job_assertions(yesterday)
    with connect() as conn:
        if fails:
            log_health(
                conn,
                "night",
                "post_job_assertions",
                "fail",
                "; ".join(fails[:10]),
            )
            log.error("Post-job assertions failed: %s", fails)
            return 1
        log_health(conn, "night", "post_job_assertions", "pass", f"{total} rows OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
