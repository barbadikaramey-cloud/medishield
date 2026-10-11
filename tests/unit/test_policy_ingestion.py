from pathlib import Path

from medishield.agents.policy import PolicyChunkRecord
from medishield.agents.policy_ingestion import EmbeddedQdrantPolicyIndex, ParsedPolicyChunk


def test_embedded_qdrant_filters_policy_and_version(tmp_path: Path) -> None:
    index = EmbeddedQdrantPolicyIndex(tmp_path / "qdrant")
    index.index([
        ParsedPolicyChunk(PolicyChunkRecord("gold", "GOLD", "v1", "1", "Gold", "covered cardiac service", 1), "a" * 64),
        ParsedPolicyChunk(PolicyChunkRecord("silver", "SILVER", "v1", "1", "Silver", "covered cardiac service", 1), "b" * 64),
    ])
    results = index.search("GOLD", "v1", "cardiac service")
    assert [result["chunk_id"] for result in results] == ["gold"]
