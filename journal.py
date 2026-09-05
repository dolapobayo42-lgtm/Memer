import csv
import os
import datetime as dt

JOURNAL_PATH = os.environ.get("JOURNAL_PATH", "./launches.csv")

FIELDNAMES = [
    "mint", "deployer", "name", "symbol", "launched_at_utc",
    "bonded", "bonded_at_utc", "alerted", "alerted_at_utc", "mc_at_alert",
]


def _ensure_file():
    dirpath = os.path.dirname(JOURNAL_PATH)
    if dirpath:
        os.makedirs(dirpath, exist_ok=True)
    if not os.path.exists(JOURNAL_PATH):
        with open(JOURNAL_PATH, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=FIELDNAMES).writeheader()
        print(f"[journal] Created new journal at: {os.path.abspath(JOURNAL_PATH)}")


def log_launch(mint: str, deployer: str, name: str, symbol: str):
    _ensure_file()
    row = {
        "mint": mint, "deployer": deployer, "name": name, "symbol": symbol,
        "launched_at_utc": dt.datetime.utcnow().isoformat(),
        "bonded": "no", "bonded_at_utc": "",
        "alerted": "no", "alerted_at_utc": "", "mc_at_alert": "",
    }
    with open(JOURNAL_PATH, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=FIELDNAMES).writerow(row)


def mark_bonded(mint: str):
    """Call this when a subscribeMigration event fires for a mint."""
    _ensure_file()
    with open(JOURNAL_PATH, "r") as f:
        rows = list(csv.DictReader(f))
    changed = False
    for row in rows:
        if row["mint"] == mint and row["bonded"] == "no":
            row["bonded"] = "yes"
            row["bonded_at_utc"] = dt.datetime.utcnow().isoformat()
            changed = True
            break
    if changed:
        with open(JOURNAL_PATH, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)


def get_stalled_candidates(min_age_hours: float = 5.0) -> list:
    """
    Returns not-yet-bonded, not-yet-alerted launches old enough to check.
    These are the only rows worth spending a DexScreener call on.
    """
    _ensure_file()
    with open(JOURNAL_PATH, "r") as f:
        rows = list(csv.DictReader(f))
    now = dt.datetime.utcnow()
    candidates = []
    for r in rows:
        if r["bonded"] == "yes" or r["alerted"] == "yes":
            continue
        try:
            launched = dt.datetime.fromisoformat(r["launched_at_utc"])
        except ValueError:
            continue
        if (now - launched).total_seconds() / 3600 >= min_age_hours:
            candidates.append(r)
    return candidates


def mark_alerted(mint: str, mc: float):
    _ensure_file()
    with open(JOURNAL_PATH, "r") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        if row["mint"] == mint:
            row["alerted"] = "yes"
            row["alerted_at_utc"] = dt.datetime.utcnow().isoformat()
            row["mc_at_alert"] = mc
            break
    with open(JOURNAL_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
