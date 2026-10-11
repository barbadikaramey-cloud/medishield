import os
import sys

import pytest

# Ensure src/ is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


@pytest.fixture
def sample_audit_payload():
    return {
        "event": "CASE_CREATED",
        "case_id": "CASE-20261002-000001",
        "status": "RECEIVED",
        "details": {"source": "REST_API", "file_count": 2},
    }
