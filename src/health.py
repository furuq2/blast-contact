from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from src.config import (
    DB_PATH,
    EXPECTED_FILES,
    EXPECTED_MODULES,
    EXPECTED_SCHEMA,
    ROOT,
    SEASON,
)
from src.db import bootstrap_schema, connect, log_health


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""
    repair_action: str | None = None


@dataclass
class Check:
    name: str
    fn: Callable[[], CheckResult]
    repair: Callable[[], str] | None = None


def _today_iso_et() -> str:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _yesterday_iso_et() -> str:
    from zoneinfo import ZoneInfo
    return (datetime.now(ZoneInfo("America/New_York")).date() - timedelta(days=1)).isoformat()


def check_files() -> CheckResult:
    missing = [f for f in EXPECTED_FILES if not (ROOT / f).exists()]
    if missing:
        return CheckResult(
            name="check_files",
            status="fail",
            detail=f"Missing files: {missing}",
        )
    return CheckResult(name="check_files", status="pass")


def check_imports() -> CheckResult:
    failures: list[str] = []
    for mod in EXPECTED_MODULES:
        try:
            importlib.import_module(mod)
        except Exception as e:
            failures.append(f"{mod}: {type(e).__name__}: {e}")
    if failures:
        return CheckResult(
            name="check_imports",
            status="fail",
            detail="; ".join(failures),
        )
    return CheckResult(name="check_imports", status="pass")


def check_db_schema() -> CheckResult:
    if not DB_PATH.exists():
        return CheckResult(
            name="check_db_schema",
            status="fail",
            detail=f"DB file does not exist at {DB_PATH}",
        )
    with connect() as conn:
        missing: list[str] = []
        for table, cols in EXPECTED_SCHEMA.items():
            try:
                cur = conn.execute(f"PRAGMA table_info({table})")
                actual = {row["name"] for row in cur.fetchall()}
            except Exception as e:
                missing.append(f"{table}: {e}")
                continue
            if not actual:
                missing.append(f"missing table: {table}")
                continue
            absent = [c for c in cols if c not in actual]
            if absent:
                missing.append(f"{table} missing columns: {absent}")
    if missing:
        return CheckResult(
            name="check_db_schema",
            status="fail",
            detail="; ".join(missing),
        )
    return CheckResult(name="check_db_schema", status="pass")


def repair_db_schema() -> str:
    bootstrap_schema()
    return "ran bootstrap_schema()"


def check_savant_schema() -> CheckResult:
    from src.savant import get_pitcher_blast, get_team_blast
    try:
        pdf = get_pitcher_blast(SEASON)
        ldf = get_team_blast(SEASON, "L")
        rdf = get_team_blast(SEASON, "R")
    except Exception as e:
        return CheckResult(
            name="check_savant_schema",
            status="fail",
            detail=f"{type(e).__name__}: {e}",
        )
    return CheckResult(
        name="check_savant_schema",
        status="pass",
        detail=f"pitchers={len(pdf)}, teamsL={len(ldf)}, teamsR={len(rdf)}",
    )


def repair_savant_pull() -> str:
    return "no-op (savant fetch already retries with backoff)"


def check_pitcher_hand_coverage(today: str | None = None) -> CheckResult:
    today = today or _today_iso_et()
    with connect() as conn:
        cur = conn.execute(
            "SELECT game_pk, away_pitcher_id, away_pitcher_hand, "
            "home_pitcher_id, home_pitcher_hand "
            "FROM games WHERE game_date = ?",
            (today,),
        )
        gaps: list[str] = []
        for row in cur.fetchall():
            if row["away_pitcher_id"] and not row["away_pitcher_hand"]:
                gaps.append(f"game {row['game_pk']} away SP {row['away_pitcher_id']}")
            if row["home_pitcher_id"] and not row["home_pitcher_hand"]:
                gaps.append(f"game {row['game_pk']} home SP {row['home_pitcher_id']}")
    if gaps:
        return CheckResult(
            name="check_pitcher_hand_coverage",
            status="fail",
            detail=f"missing hand: {gaps}",
        )
    return CheckResult(name="check_pitcher_hand_coverage", status="pass")


def repair_pitcher_hand() -> str:
    from src.self_correct import repair_pitcher_hand as do
    n = do()
    return f"resolved {n} pitcher hand(s)"


def check_yesterday_scores(yesterday: str | None = None) -> CheckResult:
    yesterday = yesterday or _yesterday_iso_et()
    with connect() as conn:
        cur = conn.execute(
            "SELECT COUNT(*) AS n FROM games "
            "WHERE game_date = ? "
            "AND (status IS NULL OR (status NOT IN ('Postponed','Cancelled') "
            "AND (away_score IS NULL OR home_score IS NULL)))",
            (yesterday,),
        )
        n = cur.fetchone()["n"]
    if n > 0:
        return CheckResult(
            name="check_yesterday_scores",
            status="fail",
            detail=f"{n} game(s) from {yesterday} missing final scores",
        )
    return CheckResult(name="check_yesterday_scores", status="pass")


def repair_missing_scores() -> str:
    from src.self_correct import repair_missing_scores as do
    n = do()
    return f"backfilled {n} score(s)"


def check_today_snapshots(today: str | None = None) -> CheckResult:
    """Hard-fails only on broken joins; missing pitcher Blast Contact% is a
    legitimate data gap (early-season / openers / new call-ups) and is just
    surfaced in the detail string, not as a failure.
    """
    today = today or _today_iso_et()
    with connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM games WHERE game_date = ?", (today,)
        ).fetchone()["n"]
        broken = conn.execute(
            "SELECT COUNT(*) AS n FROM games "
            "WHERE game_date = ? AND ("
            "(away_pitcher_id IS NOT NULL AND away_pitcher_hand IS NULL) OR "
            "(home_pitcher_id IS NOT NULL AND home_pitcher_hand IS NULL) OR "
            "(away_pitcher_hand IS NOT NULL AND away_team_blast_contact_vs_opp_hand IS NULL) OR "
            "(home_pitcher_hand IS NOT NULL AND home_team_blast_contact_vs_opp_hand IS NULL))",
            (today,),
        ).fetchone()["n"]
        soft = conn.execute(
            "SELECT COUNT(*) AS n FROM games "
            "WHERE game_date = ? AND ("
            "(away_pitcher_id IS NOT NULL AND away_pitcher_blast_contact_allowed IS NULL) OR "
            "(home_pitcher_id IS NOT NULL AND home_pitcher_blast_contact_allowed IS NULL))",
            (today,),
        ).fetchone()["n"]
    if total == 0:
        return CheckResult(
            name="check_today_snapshots",
            status="pass",
            detail="no games on slate today",
        )
    if broken > 0:
        return CheckResult(
            name="check_today_snapshots",
            status="fail",
            detail=f"{broken}/{total} games have broken joins (hand/team blast)",
        )
    return CheckResult(
        name="check_today_snapshots",
        status="pass",
        detail=f"{total} games OK ({soft} pitcher(s) without Savant sample)",
    )


def repair_today_slate() -> str:
    from src.self_correct import repair_today_slate as do
    n = do()
    return f"refilled {n} game row(s)"


def check_csv_sync() -> CheckResult:
    from src.spreadsheet import get_csv_row_count
    try:
        with connect() as conn:
            db_n = conn.execute("SELECT COUNT(*) AS n FROM games").fetchone()["n"]
        csv_n = get_csv_row_count()
    except Exception as e:
        return CheckResult(
            name="check_csv_sync",
            status="fail",
            detail=f"{type(e).__name__}: {e}",
        )
    if db_n != csv_n:
        return CheckResult(
            name="check_csv_sync",
            status="fail",
            detail=f"DB has {db_n} rows, master.csv has {csv_n}",
        )
    return CheckResult(
        name="check_csv_sync", status="pass", detail=f"{db_n} rows synced"
    )


def repair_csv_sync() -> str:
    from src.spreadsheet import write_csv
    n = write_csv()
    return f"regenerated master.csv with {n} rows"


def check_db_size() -> CheckResult:
    if not DB_PATH.exists():
        return CheckResult(
            name="check_db_size", status="fail", detail="DB file does not exist"
        )
    size = DB_PATH.stat().st_size
    if size < 4096:
        return CheckResult(
            name="check_db_size",
            status="fail",
            detail=f"DB file suspiciously small: {size} bytes",
        )
    return CheckResult(
        name="check_db_size", status="pass", detail=f"{size} bytes"
    )


def boot_check(job: str) -> None:
    """Run before any real work in morning/night/healthcheck jobs.

    File presence + module import + schema integrity. Auto-repairs additive
    schema drift; raises on anything else.
    """
    bootstrap_schema()  # idempotent — creates db + tables if missing
    checks_with_repair = [
        (check_files, None),
        (check_imports, None),
        (check_db_schema, repair_db_schema),
    ]
    failures: list[CheckResult] = []
    with connect() as conn:
        for chk, repair in checks_with_repair:
            result = chk()
            if result.status == "fail" and repair is not None:
                action = repair()
                result = chk()
                if result.status == "pass":
                    log_health(
                        conn, job, result.name, "repaired", result.detail, action
                    )
                    continue
            log_health(conn, job, result.name, result.status, result.detail)
            if result.status == "fail":
                failures.append(result)
    if failures:
        details = "; ".join(f"{f.name}: {f.detail}" for f in failures)
        raise RuntimeError(f"Boot check failed: {details}")


# Healthcheck registry — order matters (cheap checks first, repairs later).
HEALTHCHECKS: list[Check] = [
    Check("check_files", check_files, None),
    Check("check_imports", check_imports, None),
    Check("check_db_schema", check_db_schema, repair_db_schema),
    Check("check_db_size", check_db_size, None),
    Check("check_savant_schema", check_savant_schema, repair_savant_pull),
    Check("check_pitcher_hand_coverage", check_pitcher_hand_coverage, repair_pitcher_hand),
    Check("check_today_snapshots", check_today_snapshots, repair_today_slate),
    Check("check_yesterday_scores", check_yesterday_scores, repair_missing_scores),
    Check("check_csv_sync", check_csv_sync, repair_csv_sync),
]
