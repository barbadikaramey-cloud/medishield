import json
from pathlib import Path

repo = Path(__file__).resolve().parent.parent

for path in repo.rglob("*"):
    if path.is_file() and not any(part in str(path) for part in [".venv", "node_modules", ".git"]):
        if any(term in path.name for term in ["B086BE", "96208E", "b086be", "96208e"]):
            print("Found filename match:", path)
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            if "B086BE" in content or "96208E" in content:
                print(f"Found content match in {path}")
        except Exception:
            pass

