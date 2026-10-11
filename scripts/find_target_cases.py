import json
import sqlite3
from pathlib import Path

repo = Path(__file__).resolve().parent.parent

# 1. Search SQLite DB
db_path = repo / "data" / "medishield.db"
if db_path.exists():
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        cur.execute("SELECT case_id, workflow_state, current_decision, current_sub_reason, justification FROM cases WHERE case_id LIKE '%B086BE%' OR case_id LIKE '%96208E%'")
        rows = cur.fetchall()
        print("Cases in SQLite DB:")
        for r in rows:
            print("  ", r)
    except Exception as e:
        print("DB query error:", e)
    conn.close()

# 2. Search data/storage and dataset
for root in [repo / "data", repo / "dataset", repo / "scripts"]:
    for p in root.rglob("*"):
        if p.is_file():
            if any(k in p.name.upper() for k in ["B086BE", "96208E"]):
                print("Found file:", p)
            elif p.suffix in (".json", ".txt", ".csv"):
                try:
                    txt = p.read_text(encoding="utf-8", errors="ignore")
                    if "B086BE" in txt or "96208E" in txt:
                        print(f"Found mention in {p}")
                except Exception:
                    pass

