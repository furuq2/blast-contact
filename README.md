# MLB Blast Contact Daily Pipeline

Daily ingest of pre-game pitcher Blast Contact% allowed and opposing-team batter Blast Contact% vs that pitcher's hand, paired with final scores. Master record is SQLite; daily output is `data/master.csv` committed to the git repo.

The metric tracked is **Blast Contact %** = Baseball Savant `blast_per_bat_contact` (blasts per batted-ball contact), not `blast_per_swing`.

## Architecture

- `src/morning_job.py` — runs ~10 AM ET, writes today's slate + per-day stat snapshots, regenerates `data/master.csv`.
- `src/night_job.py` — runs ~3:30 AM ET, fills final scores for yesterday, regenerates `data/master.csv`.
- `src/healthcheck_job.py` — runs daily 09:00 UTC, self-checks every component, auto-repairs what it can, opens GitHub Issue on unrepaired failure.
- `src/backfill.py` — one-shot, walks the 2025 season schedule and writes game rows.

Three layers of self-checking:
1. **Boot-check** at the top of every job — file presence, module import, DB schema.
2. **Post-job assertions** at the bottom of `morning_job` and `night_job`.
3. **Daily healthcheck** — independent run with auto-repair + GitHub Issue creation.

Every check, repair attempt, and result is written to the `health_log` table.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export SEASON=2025   # or 2026 once the season starts
```

No third-party auth, secrets, or service accounts are required.

## First run

```bash
# 1. Backfill 2025 season-to-date (one-shot, ~5–10 min)
python -m src.backfill

# 2. Run today's morning job
python -m src.morning_job

# 3. After last game ends, run night job for yesterday
python -m src.night_job

# 4. Inspect outputs
sqlite3 data/blast_contact.db "SELECT COUNT(*) FROM games WHERE season = 2025"
open data/master.csv          # opens in Numbers / Excel

# 5. Inspect health log
sqlite3 data/blast_contact.db "SELECT * FROM health_log ORDER BY ts DESC LIMIT 20"
```

## Daily output: `data/master.csv`

- Rolling spreadsheet with one row per game ever logged.
- Header row uses friendly names (`Away SP Blast Contact% Allowed`, `Home Bat Blast Contact% vs SP Hand`, etc.).
- Blast contact columns are decimals (e.g. `0.0780` = 7.80%); Excel/Numbers can format-as-percent on open.
- Opens directly in Excel, Numbers, Google Sheets (File → Import), pandas, R, etc.
- GitHub renders it as a sortable table when you browse the repo on the web.

## Deploy to GitHub Actions

1. Push to a GitHub repo.
2. (Optional) Repo → Settings → Secrets and variables → Actions → Variables: add `SEASON` (defaults to `2025`).
3. Trigger each workflow manually once via Actions → workflow → "Run workflow".
4. Cron schedules take over; both `data/blast_contact.db` and `data/master.csv` are committed back to the repo on every run.

The default `GITHUB_TOKEN` (auto-provisioned) is the only credential the workflows use — no manual secrets to set up.

## Self-heal operating model

**Boot-check** runs at the top of every job and asserts:
- All expected files exist.
- All expected modules import cleanly.
- DB schema has every expected table + column. Auto-repairs additive drift.

**Post-job assertions** verify the job actually populated what it was supposed to:
- Morning: every game today has all four blast contact columns + pitcher hand.
- Night: every non-postponed yesterday game has scores.

**Daily healthcheck** at 09:00 UTC runs all checks, attempts repair on each failure, re-checks, and opens a GitHub Issue (label `auto-health-fail`) for anything that couldn't self-repair. Issues auto-close on next clean run.

## Inspecting failures

```bash
# Last 20 non-pass entries
sqlite3 data/blast_contact.db \
  "SELECT ts, job, check_name, status, detail, repair_action
   FROM health_log WHERE status != 'pass'
   ORDER BY ts DESC LIMIT 20"
```

## Schema (key tables)

- `games` — one row per game (PK `game_pk`), all four blast contact columns + scores + status.
- `pitcher_blast_snapshots` — point-in-time pitcher stats (PK `snapshot_date, pitcher_id`).
- `team_bat_blast_snapshots` — point-in-time team-vs-hand stats (PK `snapshot_date, team_id, vs_pitcher_hand`).
- `pitcher_hand_cache` — `pitcher_id → 'L' | 'R'`, populated from MLB Stats API.
- `health_log` — append-only audit of every check + repair.

## Out of scope (v1)

- Pattern recognition / model training (read from this DB in a separate script).
- Rolling-window stat snapshots (L7/L15/L30) — schema is forward-compatible; add columns later.
- Closing odds / market lines — joinable on `game_pk` later.
