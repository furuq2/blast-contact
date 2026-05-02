from __future__ import annotations

import csv

from src.config import DATA_DIR, MASTER_CSV_PATH, SHEET_COLUMNS
from src.db import connect, fetch_master_rows


def _format_cell(value, column_key: str):
    if value is None:
        return ""
    if column_key.endswith("_blast_contact_allowed") or column_key.endswith(
        "_blast_contact_vs_opp_hand"
    ):
        try:
            return f"{float(value):.4f}"
        except (TypeError, ValueError):
            return ""
    return value


def write_csv() -> int:
    """Replace data/master.csv with the full games table.

    Returns the number of data rows written (excludes header).
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        rows = fetch_master_rows(conn)

    keys = [k for k, _ in SHEET_COLUMNS]
    headers = [h for _, h in SHEET_COLUMNS]

    with open(MASTER_CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for row in rows:
            w.writerow(
                [_format_cell(row[k] if k in row.keys() else None, k) for k in keys]
            )
    return len(rows)


def get_csv_row_count() -> int:
    """Return number of data rows in master.csv (excludes header). 0 if missing."""
    if not MASTER_CSV_PATH.exists():
        return 0
    with open(MASTER_CSV_PATH, "r", encoding="utf-8") as f:
        return max(sum(1 for _ in f) - 1, 0)
