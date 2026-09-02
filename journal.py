"""
journal.py -- persistent per-deployer launch/death tracking.

Same lesson learned the hard way on the weather bot: Railway's default
filesystem is ephemeral, wiped on every redeploy. Set JOURNAL_PATH to a
mounted Volume path (e.g. /data/launches.csv) from day one this time --
don't wait to lose real data before fixing it.
"""

import csv
import os
import datetime as dt

JOURNAL_PATH = os.environ.get("JOURNAL_PATH", "./launches.csv")

FIELDNAMES = [
    "mint", "deployer", "name", "symbol", "launched_at_utc",
    "initial_liquidity_usd", "status", "checked_at_utc",
    "final_liquidity_usd", "outcome",  # outcome: "alive" or "dead" (24h deployer-rep check)
    "stage1_status", "stage1_passed",  # 15-min free pre-filter, gates Birdeye spend
    "birdeye_checked", "birdeye_passed",  # Tier 2 holder-quality result, budgeted
]


def _ensure_file():
    dirpath = os.path.dirname(JOURNAL_PATH)
    if dirpath:
        os.makedirs(dirpath, exist_ok=True)
    if not os.path.exists(JOURNAL_PATH):
        with open(JOURNAL_PATH, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()
        print(f"[journal] Created new journal at: {os.path.abspath(JOURNAL_PATH)}")


def log_launch(mint: str, deployer: str, name: str, symbol: str,
                initial_liquidity_usd: float = None):
    """Call this the moment a new token launch is detected."""
    _ensure_file()
    row = {
        "mint": mint, "deployer": deployer, "name": name, "symbol": symbol,
        "launched_at_utc": dt.datetime.utcnow().isoformat(),
        "initial_liquidity_usd": initial_liquidity_usd or "",
        "status": "pending_check", "checked_at_utc": "",
        "final_liquidity_usd": "", "outcome": "",
        "stage1_status": "pending", "stage1_passed": "",
        "birdeye_checked": "", "birdeye_passed": "",
    }
    with open(JOURNAL_PATH, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=FIELDNAMES).writerow(row)


def get_pending_checks(min_age_hours: float = 24.0) -> list:
    """Returns rows launched more than min_age_hours ago that haven't been
    checked yet -- these are ready to have their current liquidity checked
    to determine alive/dead."""
    _ensure_file()
    with open(JOURNAL_PATH, "r") as f:
        rows = list(csv.DictReader(f))
    now = dt.datetime.utcnow()
    pending = []
    for r in rows:
        if r["status"] != "pending_check":
            continue
        try:
            launched = dt.datetime.fromisoformat(r["launched_at_utc"])
        except ValueError:
            continue
        if (now - launched).total_seconds() / 3600 >= min_age_hours:
            pending.append(r)
    return pending


def mark_checked(mint: str, final_liquidity_usd: float, dead_threshold_usd: float = 1000.0):
    """Updates a row once its liquidity has been re-checked. Dead = liquidity
    fell below dead_threshold_usd (matches the real $1,000 threshold used in
    the Solidus Labs methodology -- not an arbitrary number)."""
    _ensure_file()
    with open(JOURNAL_PATH, "r") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        if row["mint"] == mint and row["status"] == "pending_check":
            outcome = "dead" if final_liquidity_usd < dead_threshold_usd else "alive"
            row["status"] = "checked"
            row["checked_at_utc"] = dt.datetime.utcnow().isoformat()
            row["final_liquidity_usd"] = final_liquidity_usd
            row["outcome"] = outcome
            break
    with open(JOURNAL_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def deployer_stats(deployer: str) -> dict:
    """Returns {total, checked, dead, alive, death_rate} for one deployer address."""
    _ensure_file()
    with open(JOURNAL_PATH, "r") as f:
        rows = [r for r in csv.DictReader(f) if r["deployer"] == deployer]
    checked = [r for r in rows if r["status"] == "checked"]
    dead = [r for r in checked if r["outcome"] == "dead"]
    death_rate = len(dead) / len(checked) if checked else None
    return {
        "total": len(rows), "checked": len(checked),
        "dead": len(dead), "alive": len(checked) - len(dead),
        "death_rate": round(death_rate, 3) if death_rate is not None else None,
    }


def classify_deployer(deployer: str, min_history: int = 3,
                       serial_rugger_threshold: float = 0.80) -> str:
    """
    Returns 'serial_rugger', 'clean', or 'unknown' (not enough history yet).
    Threshold matches DaybreakScan's real published methodology (>80% death
    rate = near-certain serial pattern) -- not an arbitrary guess.
    """
    stats = deployer_stats(deployer)
    if stats["checked"] < min_history:
        return "unknown"
    if stats["death_rate"] >= serial_rugger_threshold:
        return "serial_rugger"
    return "clean"


def get_pending_stage1_checks(min_age_minutes: float = 15.0) -> list:
    """Returns rows old enough for the free Stage 1 liquidity pre-check,
    not yet checked. This gates whether we spend a budgeted Birdeye call."""
    _ensure_file()
    with open(JOURNAL_PATH, "r") as f:
        rows = list(csv.DictReader(f))
    now = dt.datetime.utcnow()
    pending = []
    for r in rows:
        if r.get("stage1_status") != "pending":
            continue
        try:
            launched = dt.datetime.fromisoformat(r["launched_at_utc"])
        except ValueError:
            continue
        if (now - launched).total_seconds() / 60 >= min_age_minutes:
            pending.append(r)
    return pending


def mark_stage1_result(mint: str, passed: bool):
    """Records the free Stage 1 pre-check result. Only 'passed' rows should
    ever trigger a paid Birdeye call."""
    _ensure_file()
    with open(JOURNAL_PATH, "r") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        if row["mint"] == mint and row.get("stage1_status") == "pending":
            row["stage1_status"] = "checked"
            row["stage1_passed"] = "yes" if passed else "no"
            break
    with open(JOURNAL_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def get_pending_birdeye_checks() -> list:
    """Rows that passed Stage 1 and haven't had a Birdeye check yet --
    the actual candidates worth spending budget on."""
    _ensure_file()
    with open(JOURNAL_PATH, "r") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if r.get("stage1_passed") == "yes" and not r.get("birdeye_checked")]


def mark_birdeye_result(mint: str, passed: bool):
    _ensure_file()
    with open(JOURNAL_PATH, "r") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        if row["mint"] == mint:
            row["birdeye_checked"] = "yes"
            row["birdeye_passed"] = "yes" if passed else "no"
            break
    with open(JOURNAL_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
