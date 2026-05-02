from __future__ import annotations

import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from src.config import SEASON
from src.db import (
    connect,
    log_health,
    upsert_game,
    upsert_pitcher_hand,
    upsert_pitcher_snapshot,
    upsert_team_snapshot,
    get_pitcher_hand_cached,
)
from src.health import boot_check
from src.savant import get_pitcher_blast, get_team_blast
from src.statsapi import get_pitcher_hand, get_schedule

log = logging.getLogger("morning_job")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _today_iso_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _now_utc_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def build_today_slate() -> int:
    """Fetch today's slate, snapshots, and write to DB. Returns row count."""
    today = _today_iso_et()
    log.info("Building slate for %s", today)

    games = get_schedule(today)
    log.info("Found %d games on schedule", len(games))
    if not games:
        return 0

    pitcher_df = get_pitcher_blast(SEASON)
    team_l_df = get_team_blast(SEASON, "L")
    team_r_df = get_team_blast(SEASON, "R")
    log.info(
        "Savant: pitchers=%d teamsL=%d teamsR=%d",
        len(pitcher_df), len(team_l_df), len(team_r_df),
    )

    written = 0
    with connect() as conn:
        # Pitcher snapshots
        for pid, prow in pitcher_df.iterrows():
            upsert_pitcher_snapshot(
                conn,
                {
                    "snapshot_date": today,
                    "pitcher_id": int(pid),
                    "pitcher_name": prow.get("pitcher_name"),
                    "blast_per_bat_contact": _f(prow.get("blast_per_bat_contact")),
                    "blast_per_swing": _f(prow.get("blast_per_swing")),
                    "swings": _i(prow.get("swings_competitive")),
                    "contact": _i(prow.get("contact")),
                },
            )

        # Team snapshots, both hands
        for hand, df in (("L", team_l_df), ("R", team_r_df)):
            for tid, trow in df.iterrows():
                upsert_team_snapshot(
                    conn,
                    {
                        "snapshot_date": today,
                        "team_id": int(tid),
                        "team_name": trow.get("team_name"),
                        "vs_pitcher_hand": hand,
                        "blast_per_bat_contact": _f(trow.get("blast_per_bat_contact")),
                        "blast_per_swing": _f(trow.get("blast_per_swing")),
                        "swings": _i(trow.get("swings_competitive")),
                    },
                )

        # Resolve pitcher hand for any unseen ids
        unique_ids: set[int] = set()
        for g in games:
            if g["away_pitcher_id"]:
                unique_ids.add(int(g["away_pitcher_id"]))
            if g["home_pitcher_id"]:
                unique_ids.add(int(g["home_pitcher_id"]))
        for pid in unique_ids:
            if get_pitcher_hand_cached(conn, pid):
                continue
            try:
                hand = get_pitcher_hand(pid)
                upsert_pitcher_hand(conn, pid, hand)
            except Exception as e:
                log.warning("Could not resolve hand for pitcher %s: %s", pid, e)

        # Build and upsert game rows
        for g in games:
            away_pid = g["away_pitcher_id"]
            home_pid = g["home_pitcher_id"]
            away_hand = (
                get_pitcher_hand_cached(conn, int(away_pid)) if away_pid else None
            )
            home_hand = (
                get_pitcher_hand_cached(conn, int(home_pid)) if home_pid else None
            )

            # Pitcher blast contact% allowed
            away_pitcher_blast = (
                _f(pitcher_df.loc[int(away_pid), "blast_per_bat_contact"])
                if away_pid and int(away_pid) in pitcher_df.index
                else None
            )
            home_pitcher_blast = (
                _f(pitcher_df.loc[int(home_pid), "blast_per_bat_contact"])
                if home_pid and int(home_pid) in pitcher_df.index
                else None
            )

            # Opposing-hand team blast contact%
            away_team_blast_vs_opp = _team_blast_vs(
                team_l_df, team_r_df, home_hand, g["away_team_id"]
            )
            home_team_blast_vs_opp = _team_blast_vs(
                team_l_df, team_r_df, away_hand, g["home_team_id"]
            )

            row = {
                "game_pk": int(g["game_pk"]),
                "game_date": g["game_date"],
                "season": int(g["season"]),
                "away_team_id": g["away_team_id"],
                "away_team": g["away_team"],
                "home_team_id": g["home_team_id"],
                "home_team": g["home_team"],
                "away_pitcher_id": away_pid,
                "away_pitcher_name": g["away_pitcher_name"],
                "away_pitcher_hand": away_hand,
                "home_pitcher_id": home_pid,
                "home_pitcher_name": g["home_pitcher_name"],
                "home_pitcher_hand": home_hand,
                "away_pitcher_blast_contact_allowed": away_pitcher_blast,
                "home_pitcher_blast_contact_allowed": home_pitcher_blast,
                "away_team_blast_contact_vs_opp_hand": away_team_blast_vs_opp,
                "home_team_blast_contact_vs_opp_hand": home_team_blast_vs_opp,
                "away_score": g["away_score"],
                "home_score": g["home_score"],
                "status": g["status"],
                "snapshot_morning_at": _now_utc_iso(),
                "snapshot_night_at": None,
            }
            upsert_game(conn, row)
            written += 1

    return written


def _f(v) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _i(v) -> int | None:
    try:
        if v is None:
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _team_blast_vs(team_l_df, team_r_df, opp_hand: str | None, team_id):
    if not opp_hand or not team_id:
        return None
    df = team_l_df if opp_hand == "L" else team_r_df
    if int(team_id) not in df.index:
        return None
    return _f(df.loc[int(team_id), "blast_per_bat_contact"])


def post_job_assertions(today: str) -> tuple[list[str], list[str]]:
    """Return (hard_fails, soft_warnings).

    Hard fails (something is broken): pitcher hand unresolved when we have a
    pitcher id, or team blast vs opp-hand missing when we know the hand.
    Soft warnings (real-world data gaps, non-fatal): pitcher Blast Contact%
    missing — happens for pitchers with < min_swings tracked (early season,
    new call-ups, openers).
    """
    hard: list[str] = []
    soft: list[str] = []
    with connect() as conn:
        cur = conn.execute(
            "SELECT game_pk, away_pitcher_id, away_pitcher_name, away_pitcher_hand, "
            "home_pitcher_id, home_pitcher_name, home_pitcher_hand, "
            "away_pitcher_blast_contact_allowed, home_pitcher_blast_contact_allowed, "
            "away_team_blast_contact_vs_opp_hand, home_team_blast_contact_vs_opp_hand "
            "FROM games WHERE game_date = ?",
            (today,),
        )
        for row in cur.fetchall():
            if row["away_pitcher_id"] and not row["away_pitcher_hand"]:
                hard.append(f"game {row['game_pk']}: away SP hand unresolved")
            if row["home_pitcher_id"] and not row["home_pitcher_hand"]:
                hard.append(f"game {row['game_pk']}: home SP hand unresolved")
            if row["away_pitcher_hand"] and row["away_team_blast_contact_vs_opp_hand"] is None:
                hard.append(
                    f"game {row['game_pk']}: away team blast vs {row['home_pitcher_hand']}HP missing"
                )
            if row["home_pitcher_hand"] and row["home_team_blast_contact_vs_opp_hand"] is None:
                hard.append(
                    f"game {row['game_pk']}: home team blast vs {row['away_pitcher_hand']}HP missing"
                )
            if row["away_pitcher_id"] and row["away_pitcher_blast_contact_allowed"] is None:
                soft.append(
                    f"game {row['game_pk']} away SP {row['away_pitcher_name']} ({row['away_pitcher_id']}): no Savant sample yet"
                )
            if row["home_pitcher_id"] and row["home_pitcher_blast_contact_allowed"] is None:
                soft.append(
                    f"game {row['game_pk']} home SP {row['home_pitcher_name']} ({row['home_pitcher_id']}): no Savant sample yet"
                )
    return hard, soft


def main() -> int:
    boot_check("morning")
    today = _today_iso_et()

    try:
        n = build_today_slate()
        log.info("Wrote %d game rows for %s", n, today)
    except Exception as e:
        with connect() as conn:
            log_health(conn, "morning", "build_slate", "fail", str(e))
        log.exception("morning_job failed during slate build")
        return 1

    try:
        from src.spreadsheet import write_csv
        wrote = write_csv()
        log.info("Wrote %d rows to data/master.csv", wrote)
    except Exception as e:
        with connect() as conn:
            log_health(conn, "morning", "csv_write", "fail", str(e))
        log.exception("morning_job failed during CSV write")
        return 1

    hard_fails, soft_warns = post_job_assertions(today)
    with connect() as conn:
        if hard_fails:
            log_health(
                conn,
                "morning",
                "post_job_assertions",
                "fail",
                "; ".join(hard_fails[:10]),
            )
            log.error("Post-job HARD failures: %s", hard_fails)
            return 1
        detail = f"{n} rows OK"
        if soft_warns:
            detail += f"; {len(soft_warns)} soft warning(s)"
            log.warning("Soft warnings: %s", soft_warns[:10])
        log_health(conn, "morning", "post_job_assertions", "pass", detail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
