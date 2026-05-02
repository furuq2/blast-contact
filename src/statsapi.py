from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import requests

from src.config import (
    REGULAR_AND_POSTSEASON_GAME_TYPES,
    STATSAPI_BASE,
)


class StatsAPIError(RuntimeError):
    pass


def _get(url: str, params: dict[str, Any] | None = None, retries: int = 3) -> dict:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise StatsAPIError(f"GET {url} failed after {retries} retries: {last_err}")


def get_schedule(date_iso: str) -> list[dict]:
    """Return a list of game dicts for the given date (YYYY-MM-DD).

    Each dict contains: game_pk, game_date, season, away_team_id, away_team,
    home_team_id, home_team, away_pitcher_id, away_pitcher_name,
    home_pitcher_id, home_pitcher_name, away_score, home_score, status.
    """
    payload = _get(
        f"{STATSAPI_BASE}/schedule",
        params={
            "sportId": 1,
            "date": date_iso,
            "hydrate": "probablePitcher,linescore",
        },
    )
    games: list[dict] = []
    for date_block in payload.get("dates", []):
        for g in date_block.get("games", []):
            game_type = g.get("gameType")
            if game_type not in REGULAR_AND_POSTSEASON_GAME_TYPES:
                continue
            teams = g.get("teams", {})
            away = teams.get("away", {})
            home = teams.get("home", {})
            away_team = away.get("team", {})
            home_team = home.get("team", {})
            away_prob = away.get("probablePitcher") or {}
            home_prob = home.get("probablePitcher") or {}
            game_date = g.get("officialDate") or g.get("gameDate", "")[:10]
            season = int(g.get("season", date_iso[:4]))
            games.append(
                {
                    "game_pk": int(g["gamePk"]),
                    "game_date": game_date,
                    "season": season,
                    "away_team_id": int(away_team.get("id", 0)) or None,
                    "away_team": away_team.get("name", ""),
                    "home_team_id": int(home_team.get("id", 0)) or None,
                    "home_team": home_team.get("name", ""),
                    "away_pitcher_id": int(away_prob["id"]) if away_prob.get("id") else None,
                    "away_pitcher_name": away_prob.get("fullName"),
                    "home_pitcher_id": int(home_prob["id"]) if home_prob.get("id") else None,
                    "home_pitcher_name": home_prob.get("fullName"),
                    "away_score": away.get("score"),
                    "home_score": home.get("score"),
                    "status": (g.get("status") or {}).get("detailedState", ""),
                }
            )
    return games


def get_pitcher_hand(pitcher_id: int) -> str:
    """Return 'L' or 'R' for the given MLBAM pitcher id."""
    payload = _get(f"{STATSAPI_BASE}/people/{pitcher_id}")
    people = payload.get("people", [])
    if not people:
        raise StatsAPIError(f"No person found for pitcher_id {pitcher_id}")
    code = (people[0].get("pitchHand") or {}).get("code")
    if code not in ("L", "R"):
        raise StatsAPIError(
            f"Unexpected pitchHand for pitcher_id {pitcher_id}: {code!r}"
        )
    return code
