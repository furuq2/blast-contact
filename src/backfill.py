from __future__ import annotations

import logging
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from src.config import SEASON, SEASON_OPENING_DAY
from src.db import (
    bootstrap_schema,
    connect,
    upsert_game,
    upsert_pitcher_hand,
    upsert_pitcher_snapshot,
    upsert_team_snapshot,
    get_pitcher_hand_cached,
)
from src.savant import get_pitcher_blast, get_team_blast
from src.statsapi import get_pitcher_hand, get_schedule

log = logging.getLogger("backfill")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _yesterday_iso_et() -> str:
    return (datetime.now(ZoneInfo("America/New_York")).date() - timedelta(days=1)).isoformat()


def _now_utc_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _daterange(start_iso: str, end_iso: str):
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    cur = start
    while cur <= end:
        yield cur.isoformat()
        cur += timedelta(days=1)


def main() -> int:
    bootstrap_schema()

    end_iso = _yesterday_iso_et()
    log.info("Backfilling %s -> %s", SEASON_OPENING_DAY, end_iso)

    # Pull current season-to-date Savant snapshots once and store with today's date.
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    log.info("Pulling current Savant snapshots (stamped %s)", today)
    pdf = get_pitcher_blast(SEASON)
    ldf = get_team_blast(SEASON, "L")
    rdf = get_team_blast(SEASON, "R")

    with connect() as conn:
        for pid, prow in pdf.iterrows():
            upsert_pitcher_snapshot(conn, {
                "snapshot_date": today,
                "pitcher_id": int(pid),
                "pitcher_name": prow.get("pitcher_name"),
                "blast_per_bat_contact": _f(prow.get("blast_per_bat_contact")),
                "blast_per_swing": _f(prow.get("blast_per_swing")),
                "swings": _i(prow.get("swings_competitive")),
                "contact": _i(prow.get("contact")),
            })
        for hand, df in (("L", ldf), ("R", rdf)):
            for tid, trow in df.iterrows():
                upsert_team_snapshot(conn, {
                    "snapshot_date": today,
                    "team_id": int(tid),
                    "team_name": trow.get("team_name"),
                    "vs_pitcher_hand": hand,
                    "blast_per_bat_contact": _f(trow.get("blast_per_bat_contact")),
                    "blast_per_swing": _f(trow.get("blast_per_swing")),
                    "swings": _i(trow.get("swings_competitive")),
                })

    # Walk every date in the season, write game rows with current Savant stats.
    # NOTE: backfilled rows lack point-in-time stat snapshots (the snapshot
    # columns reflect today's season-to-date, not the value as of game day).
    # Forward-going daily snapshots are point-in-time-correct.
    pitcher_blast = pdf["blast_per_bat_contact"].to_dict()
    team_blast_l = ldf["blast_per_bat_contact"].to_dict()
    team_blast_r = rdf["blast_per_bat_contact"].to_dict()

    total = 0
    pitcher_hand_cache: dict[int, str] = {}

    for d in _daterange(SEASON_OPENING_DAY, end_iso):
        try:
            games = get_schedule(d)
        except Exception as e:
            log.warning("schedule fetch failed for %s: %s", d, e)
            continue
        if not games:
            continue
        with connect() as conn:
            for g in games:
                away_pid = g["away_pitcher_id"]
                home_pid = g["home_pitcher_id"]

                away_hand = _resolve_hand(conn, pitcher_hand_cache, away_pid)
                home_hand = _resolve_hand(conn, pitcher_hand_cache, home_pid)

                away_pitcher_blast = (
                    _f(pitcher_blast.get(int(away_pid))) if away_pid else None
                )
                home_pitcher_blast = (
                    _f(pitcher_blast.get(int(home_pid))) if home_pid else None
                )

                away_team_blast_vs_opp = _team_blast_vs(
                    team_blast_l, team_blast_r, home_hand, g["away_team_id"]
                )
                home_team_blast_vs_opp = _team_blast_vs(
                    team_blast_l, team_blast_r, away_hand, g["home_team_id"]
                )

                upsert_game(conn, {
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
                    "snapshot_night_at": _now_utc_iso(),
                })
                total += 1
        log.info("%s: wrote %d games (cumulative %d)", d, len(games), total)

    log.info("Backfill complete. Total game rows: %d", total)
    return 0


def _resolve_hand(conn, cache: dict[int, str], pid):
    if not pid:
        return None
    pid = int(pid)
    if pid in cache:
        return cache[pid]
    cached = get_pitcher_hand_cached(conn, pid)
    if cached:
        cache[pid] = cached
        return cached
    try:
        h = get_pitcher_hand(pid)
    except Exception:
        return None
    upsert_pitcher_hand(conn, pid, h)
    cache[pid] = h
    return h


def _team_blast_vs(team_l, team_r, opp_hand, team_id):
    if not opp_hand or not team_id:
        return None
    src = team_l if opp_hand == "L" else team_r
    return _f(src.get(int(team_id)))


def _f(v):
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        if v is None:
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    sys.exit(main())
