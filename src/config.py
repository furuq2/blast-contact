from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "blast_contact.db"
MASTER_CSV_PATH = DATA_DIR / "master.csv"

SEASON = int(os.environ.get("SEASON", "2025"))
SEASON_OPENING_DAY = f"{SEASON}-03-27"

ET_TZ = "America/New_York"

SAVANT_BASE = "https://baseballsavant.mlb.com/leaderboard/bat-tracking"
STATSAPI_BASE = "https://statsapi.mlb.com/api/v1"

REGULAR_AND_POSTSEASON_GAME_TYPES = {"R", "F", "D", "L", "W"}

GITHUB_REPO = os.environ.get("GITHUB_REPOSITORY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")

EXPECTED_FILES = [
    "requirements.txt",
    ".gitignore",
    "src/__init__.py",
    "src/config.py",
    "src/db.py",
    "src/statsapi.py",
    "src/savant.py",
    "src/spreadsheet.py",
    "src/health.py",
    "src/self_correct.py",
    "src/morning_job.py",
    "src/night_job.py",
    "src/healthcheck_job.py",
    "src/backfill.py",
    ".github/workflows/morning.yml",
    ".github/workflows/night.yml",
    ".github/workflows/healthcheck.yml",
]

EXPECTED_MODULES = [
    "src.config",
    "src.db",
    "src.statsapi",
    "src.savant",
    "src.spreadsheet",
    "src.health",
    "src.self_correct",
    "src.morning_job",
    "src.night_job",
    "src.healthcheck_job",
    "src.backfill",
]

EXPECTED_SCHEMA = {
    "games": [
        "game_pk", "game_date", "season",
        "away_team_id", "away_team", "home_team_id", "home_team",
        "away_pitcher_id", "away_pitcher_name", "away_pitcher_hand",
        "home_pitcher_id", "home_pitcher_name", "home_pitcher_hand",
        "away_pitcher_blast_contact_allowed", "home_pitcher_blast_contact_allowed",
        "away_team_blast_contact_vs_opp_hand", "home_team_blast_contact_vs_opp_hand",
        "away_score", "home_score", "status",
        "snapshot_morning_at", "snapshot_night_at",
    ],
    "pitcher_blast_snapshots": [
        "snapshot_date", "pitcher_id", "pitcher_name",
        "blast_per_bat_contact", "blast_per_swing", "swings", "contact",
    ],
    "team_bat_blast_snapshots": [
        "snapshot_date", "team_id", "team_name", "vs_pitcher_hand",
        "blast_per_bat_contact", "blast_per_swing", "swings",
    ],
    "pitcher_hand_cache": ["pitcher_id", "hand"],
    "health_log": ["ts", "job", "check_name", "status", "detail", "repair_action"],
}

SHEET_COLUMNS = [
    ("game_date", "Date"),
    ("away_team", "Away"),
    ("away_pitcher_name", "Away SP"),
    ("away_pitcher_hand", "Hand"),
    ("away_pitcher_blast_contact_allowed", "Away SP Blast Contact% Allowed"),
    ("home_team_blast_contact_vs_opp_hand", "Home Bat Blast Contact% vs SP Hand"),
    ("home_team", "Home"),
    ("home_pitcher_name", "Home SP"),
    ("home_pitcher_hand", "Hand"),
    ("home_pitcher_blast_contact_allowed", "Home SP Blast Contact% Allowed"),
    ("away_team_blast_contact_vs_opp_hand", "Away Bat Blast Contact% vs SP Hand"),
    ("away_score", "Away R"),
    ("home_score", "Home R"),
    ("status", "Status"),
]
