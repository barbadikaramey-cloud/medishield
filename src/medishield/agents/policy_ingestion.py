"""Authoritative PDF policy ingestion and local Qdrant hybrid retrieval."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from medishield.agents.policy import POLICY_STOP_WORDS, PolicyChunkRecord


def _tokens(text: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) > 2 and token not in POLICY_STOP_WORDS
    }


def _embedding(text: str, dimensions: int = 128) -> list[float]:
    """Deterministic hashing embedding for offline evaluation (not an LLM fallback)."""
    values = [0.0] * dimensions
    for token in _tokens(text):
        values[int(hashlib.sha256(token.encode()).hexdigest(), 16) % dimensions] += 1.0
    norm = sum(v * v for v in values) ** 0.5
    return [v / norm for v in values] if norm else values


@dataclass(frozen=True)
class ParsedPolicyChunk:
    chunk: PolicyChunkRecord
    chunk_hash: str


class EmbeddedQdrantPolicyIndex:
    """Persistent embedded Qdrant index, filtered by policy ID and version."""

    collection_name = "authoritative_policy_chunks"

    def __init__(self, path: str | Path) -> None:
        self.client = QdrantClient(path=str(path))
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                self.collection_name, vectors_config=VectorParams(size=128, distance=Distance.COSINE)
            )

    def index(self, parsed: list[ParsedPolicyChunk]) -> None:
        points = []
        for item in parsed:
            chunk = item.chunk
            point_id = int(hashlib.sha256(chunk.chunk_id.encode()).hexdigest()[:15], 16)
            points.append(PointStruct(
                id=point_id,
                vector=_embedding(chunk.chunk_text),
                payload={
                    "chunk_id": chunk.chunk_id, "policy_id": chunk.policy_id,
                    "version_tag": chunk.version_tag, "section_number": chunk.section_number,
                    "section_title": chunk.section_title, "page_number": chunk.page_number,
                    "chunk_hash": item.chunk_hash, "chunk_text": chunk.chunk_text,
                    "matching_cpt_codes": chunk.matching_cpt_codes,
                    "matching_rev_codes": getattr(chunk, "matching_rev_codes", []),
                },
            ))
        if points:
            self.client.upsert(self.collection_name, points=points, wait=True)

    def search(self, policy_id: str, version_tag: str, query: str, top_k: int = 5) -> list[dict]:
        # qdrant-client APIs changed; query_points is available in current supported releases.
        from qdrant_client.models import FieldCondition, Filter, MatchValue
        result = self.client.query_points(
            self.collection_name,
            query=_embedding(query),
            query_filter=Filter(must=[
                FieldCondition(key="policy_id", match=MatchValue(value=policy_id)),
                FieldCondition(key="version_tag", match=MatchValue(value=version_tag)),
            ]),
            limit=top_k,
        )
        return [dict(point.payload, dense_score=float(point.score)) for point in result.points]


class PolicyPdfIngestor:
    """Parses authoritative PDFs into page-preserving, section-bounded chunks."""

    def parse(self, pdf_bytes: bytes, policy_id: str, version_tag: str) -> list[ParsedPolicyChunk]:
        reader = PdfReader(__import__("io").BytesIO(pdf_bytes))
        parsed: list[ParsedPolicyChunk] = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            section = "Unsectioned"
            for index, paragraph in enumerate(re.split(r"\n\s*\n", text)):
                paragraph = re.sub(r"\s+", " ", paragraph).strip()
                if not paragraph:
                    continue
                heading = re.match(r"(?:(Section\s+)?[0-9]+(?:\.[0-9]+)*\s+)(.{1,120})", paragraph)
                if heading:
                    section = heading.group(0)
                digest = hashlib.sha256(paragraph.encode("utf-8")).hexdigest()
                chunk_id = f"{policy_id}:{version_tag}:p{page_number}:c{index}:{digest[:12]}"
                parsed.append(ParsedPolicyChunk(
                    PolicyChunkRecord(chunk_id=chunk_id, policy_id=policy_id, version_tag=version_tag,
                                      section_number=section, section_title=section, chunk_text=paragraph,
                                      page_number=page_number), digest,
                ))
        return parsed
