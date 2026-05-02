from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Iterable, Iterator

from src.config import DB_PATH, DATA_DIR

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS games (
  game_pk              INTEGER PRIMARY KEY,
  game_date            DATE NOT NULL,
  season               INTEGER NOT NULL,
  away_team_id         INTEGER NOT NULL,
  away_team            TEXT NOT NULL,
  home_team_id         INTEGER NOT NULL,
  home_team            TEXT NOT NULL,
  away_pitcher_id      INTEGER,
  away_pitcher_name    TEXT,
  away_pitcher_hand    TEXT,
  home_pitcher_id      INTEGER,
  home_pitcher_name    TEXT,
  home_pitcher_hand    TEXT,
  away_pitcher_blast_contact_allowed   REAL,
  home_pitcher_blast_contact_allowed   REAL,
  away_team_blast_contact_vs_opp_hand  REAL,
  home_team_blast_contact_vs_opp_hand  REAL,
  away_score           INTEGER,
  home_score           INTEGER,
  status               TEXT,
  snapshot_morning_at  TIMESTAMP,
  snapshot_night_at    TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_games_date ON games(game_date);

CREATE TABLE IF NOT EXISTS pitcher_blast_snapshots (
  snapshot_date          DATE NOT NULL,
  pitcher_id             INTEGER NOT NULL,
  pitcher_name           TEXT,
  blast_per_bat_contact  REAL,
  blast_per_swing        REAL,
  swings                 INTEGER,
  contact                INTEGER,
  PRIMARY KEY (snapshot_date, pitcher_id)
);

CREATE TABLE IF NOT EXISTS team_bat_blast_snapshots (
  snapshot_date          DATE NOT NULL,
  team_id                INTEGER NOT NULL,
  team_name              TEXT,
  vs_pitcher_hand        TEXT NOT NULL,
  blast_per_bat_contact  REAL,
  blast_per_swing        REAL,
  swings                 INTEGER,
  PRIMARY KEY (snapshot_date, team_id, vs_pitcher_hand)
);

CREATE TABLE IF NOT EXISTS pitcher_hand_cache (
  pitcher_id  INTEGER PRIMARY KEY,
  hand        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS health_log (
  ts                TIMESTAMP NOT NULL,
  job               TEXT NOT NULL,
  check_name        TEXT NOT NULL,
  status            TEXT NOT NULL,
  detail            TEXT,
  repair_action     TEXT,
  PRIMARY KEY (ts, job, check_name)
);
CREATE INDEX IF NOT EXISTS idx_health_recent ON health_log(ts DESC);
"""


def bootstrap_schema(conn: sqlite3.Connection | None = None) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    own = conn is None
    if own:
        conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        if own:
            conn.close()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_game(conn: sqlite3.Connection, row: dict) -> None:
    cols = list(row.keys())
    placeholders = ",".join("?" for _ in cols)
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "game_pk")
    sql = (
        f"INSERT INTO games ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(game_pk) DO UPDATE SET {updates}"
    )
    conn.execute(sql, [row[c] for c in cols])


def upsert_pitcher_snapshot(conn: sqlite3.Connection, row: dict) -> None:
    cols = list(row.keys())
    placeholders = ",".join("?" for _ in cols)
    updates = ",".join(
        f"{c}=excluded.{c}" for c in cols if c not in ("snapshot_date", "pitcher_id")
    )
    sql = (
        f"INSERT INTO pitcher_blast_snapshots ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(snapshot_date, pitcher_id) DO UPDATE SET {updates}"
    )
    conn.execute(sql, [row[c] for c in cols])


def upsert_team_snapshot(conn: sqlite3.Connection, row: dict) -> None:
    cols = list(row.keys())
    placeholders = ",".join("?" for _ in cols)
    updates = ",".join(
        f"{c}=excluded.{c}"
        for c in cols
        if c not in ("snapshot_date", "team_id", "vs_pitcher_hand")
    )
    sql = (
        f"INSERT INTO team_bat_blast_snapshots ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(snapshot_date, team_id, vs_pitcher_hand) DO UPDATE SET {updates}"
    )
    conn.execute(sql, [row[c] for c in cols])


def upsert_pitcher_hand(conn: sqlite3.Connection, pitcher_id: int, hand: str) -> None:
    conn.execute(
        "INSERT INTO pitcher_hand_cache (pitcher_id, hand) VALUES (?, ?) "
        "ON CONFLICT(pitcher_id) DO UPDATE SET hand=excluded.hand",
        (pitcher_id, hand),
    )


def get_pitcher_hand_cached(conn: sqlite3.Connection, pitcher_id: int) -> str | None:
    cur = conn.execute(
        "SELECT hand FROM pitcher_hand_cache WHERE pitcher_id = ?", (pitcher_id,)
    )
    row = cur.fetchone()
    return row["hand"] if row else None


def log_health(
    conn: sqlite3.Connection,
    job: str,
    check_name: str,
    status: str,
    detail: str | None = None,
    repair_action: str | None = None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO health_log "
        "(ts, job, check_name, status, detail, repair_action) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            datetime.utcnow().isoformat(timespec="seconds"),
            job,
            check_name,
            status,
            detail,
            repair_action,
        ),
    )


def fetch_master_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    cur = conn.execute(
        "SELECT * FROM games ORDER BY game_date DESC, game_pk"
    )
    return cur.fetchall()


def update_score(
    conn: sqlite3.Connection,
    game_pk: int,
    away_score: int | None,
    home_score: int | None,
    status: str,
) -> None:
    conn.execute(
        "UPDATE games SET away_score=?, home_score=?, status=?, "
        "snapshot_night_at=? WHERE game_pk=?",
        (
            away_score,
            home_score,
            status,
            datetime.utcnow().isoformat(timespec="seconds"),
            game_pk,
        ),
    )
