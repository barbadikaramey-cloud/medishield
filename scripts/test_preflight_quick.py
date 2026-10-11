import sys
import socket
from urllib.parse import urlparse
from medishield.core.config import settings

sys.stdout.reconfigure(line_buffering=True)
print("Testing preflight quick...", flush=True)

# 1. DB probe
print(f"DATABASE_URL: {settings.DATABASE_URL}", flush=True)

# 2. Qdrant probe via socket
q_url = urlparse(settings.QDRANT_URL)
host = q_url.hostname or "localhost"
port = q_url.port or 6333
try:
    with socket.create_connection((host, port), timeout=0.5):
        print(f"Qdrant reachable on {host}:{port}", flush=True)
except Exception as e:
    print(f"Qdrant unreachable on {host}:{port}: {e}", flush=True)

print("Preflight quick done.", flush=True)

