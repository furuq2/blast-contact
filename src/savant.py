from __future__ import annotations

import io
import time

import pandas as pd
import requests

from src.config import SAVANT_BASE


class SavantError(RuntimeError):
    pass


REQUIRED_COLS = {"id", "name", "blast_per_bat_contact", "blast_per_swing"}
PITCHER_MIN_ROWS = 25
TEAM_ROW_COUNT = 30


def _fetch_csv(params: dict, retries: int = 3) -> pd.DataFrame:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(SAVANT_BASE, params=params, timeout=60)
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text), encoding="utf-8-sig")
            df.columns = [c.lstrip("﻿").strip() for c in df.columns]
            return df
        except Exception as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise SavantError(f"Savant fetch failed after {retries} retries: {last_err}")


def _assert_schema(df: pd.DataFrame, context: str) -> None:
    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        raise SavantError(
            f"Savant {context} CSV missing required columns: {missing}. "
            f"Got columns: {list(df.columns)}"
        )


def get_pitcher_blast(season: int, min_swings: int = 1) -> pd.DataFrame:
    """Return pitcher leaderboard with blast contact% allowed.

    Indexed by MLBAM pitcher_id. Asserts column schema and a row-count floor
    to catch silent param fallback to the default batter leaderboard.
    """
    df = _fetch_csv(
        {"type": "pitcher", "year": season, "min": min_swings, "csv": "true"}
    )
    _assert_schema(df, "pitcher")
    if len(df) < PITCHER_MIN_ROWS:
        raise SavantError(
            f"Pitcher CSV returned {len(df)} rows (< {PITCHER_MIN_ROWS}). "
            f"Likely silent param fallback. Inspect: {df.head().to_dict()}"
        )
    df = df.rename(columns={"id": "pitcher_id", "name": "pitcher_name"})
    df["pitcher_id"] = df["pitcher_id"].astype(int)
    df["blast_per_bat_contact"] = pd.to_numeric(df["blast_per_bat_contact"], errors="coerce")
    df["blast_per_swing"] = pd.to_numeric(df["blast_per_swing"], errors="coerce")
    return df.set_index("pitcher_id")


def get_team_blast(season: int, hand: str) -> pd.DataFrame:
    """Return team batter leaderboard filtered by opposing pitcher handedness.

    `hand` must be 'L' or 'R'. Indexed by MLBAM team_id.
    Asserts row count == 30 to catch silent fallback.
    """
    if hand not in ("L", "R"):
        raise ValueError(f"hand must be 'L' or 'R', got {hand!r}")
    df = _fetch_csv(
        {
            "type": "batting-team",
            "year": season,
            "pitchHand": hand,
            "min": 0,
            "csv": "true",
        }
    )
    _assert_schema(df, f"batting-team vs {hand}")
    if len(df) != TEAM_ROW_COUNT:
        raise SavantError(
            f"Team CSV vs {hand} returned {len(df)} rows (expected {TEAM_ROW_COUNT}). "
            f"Likely silent param fallback or season filter mismatch."
        )
    df = df.rename(columns={"id": "team_id", "name": "team_name"})
    df["team_id"] = df["team_id"].astype(int)
    df["blast_per_bat_contact"] = pd.to_numeric(df["blast_per_bat_contact"], errors="coerce")
    df["blast_per_swing"] = pd.to_numeric(df["blast_per_swing"], errors="coerce")
    return df.set_index("team_id")
