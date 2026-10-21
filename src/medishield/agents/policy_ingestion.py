"""Authoritative PDF policy ingestion and Qdrant hybrid retrieval (embedded or Qdrant Cloud)."""

from __future__ import annotations

import hashlib
import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from pypdf import PdfReader
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PayloadSchemaType, PointStruct, VectorParams

from medishield.agents.policy import POLICY_STOP_WORDS, PolicyChunkRecord

Embedder = Callable[[list[str]], list[list[float]]]


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


def hash_embedder(texts: list[str]) -> list[list[float]]:
    return [_embedding(text) for text in texts]


class GeminiEmbedder:
    """Hosted embeddings for the cloud policy index (ADR-030). Raises on failure; never falls back."""

    def __init__(self, api_key: str, model: str, dimensions: int) -> None:
        from google import genai

        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.dimensions = dimensions

    def __call__(self, texts: list[str]) -> list[list[float]]:
        from google.genai import types

        response = self.client.models.embed_content(
            model=self.model,
            contents=cast(list[Any], texts),
            config=types.EmbedContentConfig(output_dimensionality=self.dimensions),
        )
        vectors = [list(item.values or []) for item in (response.embeddings or [])]
        if len(vectors) != len(texts) or any(len(v) != self.dimensions for v in vectors):
            raise RuntimeError("Embedding response did not match requested count or dimensions")
        return vectors


@dataclass(frozen=True)
class ParsedPolicyChunk:
    chunk: PolicyChunkRecord
    chunk_hash: str


class EmbeddedQdrantPolicyIndex:
    """Persistent policy index filtered by policy ID and version (embedded or Qdrant Cloud)."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        url: str | None = None,
        api_key: str | None = None,
        collection_name: str = "authoritative_policy_chunks",
        vector_name: str | None = None,
        embedder: Embedder = hash_embedder,
        dimensions: int = 128,
    ) -> None:
        self.collection_name = collection_name
        self.vector_name = vector_name
        self.embedder = embedder
        if url:
            self.client = QdrantClient(url=url, api_key=api_key)
        else:
            self.client = QdrantClient(path=str(path))
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                self.collection_name,
                vectors_config=VectorParams(size=dimensions, distance=Distance.COSINE),
            )
        for field in ("policy_id", "version_tag"):
            self.client.create_payload_index(self.collection_name, field, PayloadSchemaType.KEYWORD)

    def _vector(self, vector: list[float]) -> Any:
        return {self.vector_name: vector} if self.vector_name else vector

    def index(self, parsed: list[ParsedPolicyChunk]) -> None:
        if not parsed:
            return
        vectors = self.embedder([item.chunk.chunk_text for item in parsed])
        points = []
        for item, vector in zip(parsed, vectors, strict=True):
            chunk = item.chunk
            point_id = int(hashlib.sha256(chunk.chunk_id.encode()).hexdigest()[:15], 16)
            points.append(PointStruct(
                id=point_id,
                vector=self._vector(vector),
                payload={
                    "chunk_id": chunk.chunk_id, "policy_id": chunk.policy_id,
                    "version_tag": chunk.version_tag, "section_number": chunk.section_number,
                    "section_title": chunk.section_title, "page_number": chunk.page_number,
                    "chunk_hash": item.chunk_hash, "chunk_text": chunk.chunk_text,
                    "matching_cpt_codes": chunk.matching_cpt_codes,
                    "matching_rev_codes": getattr(chunk, "matching_rev_codes", []),
                },
            ))
        self.client.upsert(self.collection_name, points=points, wait=True)

    def search(self, policy_id: str, version_tag: str, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        # qdrant-client APIs changed; query_points is available in current supported releases.
        from qdrant_client.models import FieldCondition, Filter, MatchValue
        query_vector = self.embedder([query])[0]
        result = self.client.query_points(
            self.collection_name,
            query=query_vector,
            using=self.vector_name,
            query_filter=Filter(must=[
                FieldCondition(key="policy_id", match=MatchValue(value=policy_id)),
                FieldCondition(key="version_tag", match=MatchValue(value=version_tag)),
            ]),
            limit=top_k,
        )
        return [{**(point.payload or {}), "dense_score": float(point.score)} for point in result.points]


class PolicyPdfIngestor:
    """Parses authoritative PDFs into page-preserving, section-bounded chunks."""

    def parse(self, pdf_bytes: bytes, policy_id: str, version_tag: str) -> list[ParsedPolicyChunk]:
        reader = PdfReader(io.BytesIO(pdf_bytes))
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
