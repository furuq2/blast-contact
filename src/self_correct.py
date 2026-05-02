from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.db import (
    connect,
    update_score,
    upsert_pitcher_hand,
)


def _today_iso_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _yesterday_iso_et() -> str:
    return (datetime.now(ZoneInfo("America/New_York")).date() - timedelta(days=1)).isoformat()


def repair_pitcher_hand() -> int:
    """Resolve missing pitcher handedness for today's slate via /people/{id}.

    Returns count of pitchers resolved.
    """
    from src.statsapi import get_pitcher_hand
    today = _today_iso_et()
    resolved = 0
    with connect() as conn:
        cur = conn.execute(
            "SELECT DISTINCT pid FROM ("
            "  SELECT away_pitcher_id AS pid FROM games "
            "    WHERE game_date = ? AND away_pitcher_id IS NOT NULL "
            "    AND away_pitcher_hand IS NULL "
            "  UNION "
            "  SELECT home_pitcher_id AS pid FROM games "
            "    WHERE game_date = ? AND home_pitcher_id IS NOT NULL "
            "    AND home_pitcher_hand IS NULL "
            ")",
            (today, today),
        )
        ids = [row["pid"] for row in cur.fetchall()]
        for pid in ids:
            try:
                hand = get_pitcher_hand(int(pid))
            except Exception:
                continue
            upsert_pitcher_hand(conn, int(pid), hand)
            conn.execute(
                "UPDATE games SET away_pitcher_hand = ? "
                "WHERE game_date = ? AND away_pitcher_id = ?",
                (hand, today, pid),
            )
            conn.execute(
                "UPDATE games SET home_pitcher_hand = ? "
                "WHERE game_date = ? AND home_pitcher_id = ?",
                (hand, today, pid),
            )
            resolved += 1
    return resolved


def repair_missing_scores() -> int:
    """Re-pull yesterday's schedule and update score columns.

    Returns count of game rows whose scores were filled in.
    """
    from src.statsapi import get_schedule
    yesterday = _yesterday_iso_et()
    games = get_schedule(yesterday)
    fixed = 0
    with connect() as conn:
        for g in games:
            if g["status"] in ("Postponed", "Cancelled", "Suspended"):
                continue
            if g["away_score"] is None or g["home_score"] is None:
                continue
            cur = conn.execute(
                "SELECT away_score, home_score FROM games WHERE game_pk = ?",
                (g["game_pk"],),
            )
            row = cur.fetchone()
            if row is None:
                continue
            if row["away_score"] is None or row["home_score"] is None:
                update_score(
                    conn,
                    g["game_pk"],
                    g["away_score"],
                    g["home_score"],
                    g["status"],
                )
                fixed += 1
    return fixed


def repair_today_slate() -> int:
    """Re-run the morning job's slate-build for today.

    Returns count of game rows refreshed.
    """
    from src.morning_job import build_today_slate
    return build_today_slate()
