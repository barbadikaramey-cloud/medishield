"""Object storage layout, S3 URI builders, and pluggable storage backends (Spec 03 § 5.2, Spec 07)."""

import os
import re
from pathlib import Path
from typing import Protocol

from medishield.core.config import settings
from medishield.core.tracing import trace_span


def sanitize_filename(filename: str) -> str:
    """Removes path separators and unsafe characters from filenames."""
    clean = os.path.basename(filename.replace("\\", "/"))
    return re.sub(r"[^\w\.-]", "_", clean)


# ── Canonical S3 Object Key Paths (Spec 03 § 5.2 & Spec 07 § 2) ───────────────

def generate_raw_object_key(case_id: str, document_id: str, file_name: str) -> str:
    """Generates canonical key for raw document upload: cases/{case_id}/raw/{doc_id}_{filename}."""
    safe_name = sanitize_filename(file_name)
    return f"cases/{case_id}/raw/{document_id}_{safe_name}"


def generate_page_object_key(case_id: str, document_id: str, page_number: int) -> str:
    """Generates canonical key for rendered page raster: cases/{case_id}/pages/{doc_id}_p{page}.png."""
    return f"cases/{case_id}/pages/{document_id}_p{page_number}.png"


def generate_ocr_object_key(case_id: str, document_id: str) -> str:
    """Generates canonical key for OCR payload: ocr/{case_id}/{document_id}.json."""
    return f"ocr/{case_id}/{document_id}.json"


def build_s3_uri(bucket_name: str, object_key: str) -> str:
    """Constructs fully qualified s3:// URI."""
    return f"s3://{bucket_name}/{object_key}"


# ── Storage Backend Protocol & Implementations ────────────────────────────────

class StorageBackend(Protocol):
    """Protocol defining object storage operations."""

    def put_bytes(
        self,
        object_key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Persists binary data and returns fully qualified S3 URI."""
        ...

    def get_bytes(self, object_key: str) -> bytes:
        """Retrieves binary data by object key."""
        ...

    def exists(self, object_key: str) -> bool:
        """Checks if object exists."""
        ...

    def delete(self, object_key: str) -> None:
        """Deletes object."""
        ...


_shared_in_memory_store: dict[str, tuple[bytes, str]] = {}


class InMemoryStorageBackend:
    """In-memory object storage backend for testing and local simulation."""

    def __init__(self, bucket_name: str | None = None, share_store: bool = True) -> None:
        if __import__('os').environ.get('MEDISHIELD_STRICT_EVAL') == '1':
            raise RuntimeError('Strict eval mode active: InMemoryStorageBackend fallback is forbidden.')
        self.bucket_name = bucket_name or settings.S3_BUCKET_NAME
        self._store: dict[str, tuple[bytes, str]] = _shared_in_memory_store if share_store else {}

    def put_bytes(
        self,
        object_key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        with trace_span(
            name="storage_put_bytes",
            run_type="tool",
            component="storage_backend",
            operation="put_bytes",
            inputs={},
        ) as span:
            self._store[object_key] = (data, content_type)
            uri = build_s3_uri(self.bucket_name, object_key)
            span.set_summary(status="SUCCESS", is_complete=True)
            return uri

    def get_bytes(self, object_key: str) -> bytes:
        with trace_span(
            name="storage_get_bytes",
            run_type="tool",
            component="storage_backend",
            operation="get_bytes",
            inputs={},
        ) as span:
            if object_key not in self._store:
                span.set_summary(status="FAILED", is_complete=False)
                raise KeyError(f"Object key '{object_key}' not found in storage bucket '{self.bucket_name}'")
            val = self._store[object_key][0]
            span.set_summary(status="SUCCESS", is_complete=True)
            return val

    def exists(self, object_key: str) -> bool:
        return object_key in self._store

    def delete(self, object_key: str) -> None:
        self._store.pop(object_key, None)


class LocalFilesystemStorageBackend:
    """Filesystem-backed storage simulating S3 layout in a local directory."""

    def __init__(self, base_directory: Path | str, bucket_name: str | None = None) -> None:
        if __import__('os').environ.get('MEDISHIELD_STRICT_EVAL') == '1':
            raise RuntimeError('Strict eval mode active: LocalFilesystemStorageBackend fallback is forbidden.')
        self.base_dir = Path(base_directory)
        self.bucket_name = bucket_name or settings.S3_BUCKET_NAME
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _clean_key(self, object_key: str) -> str:
        clean = object_key.strip()
        if clean.startswith("s3://"):
            parts = clean[5:].split("/", 1)
            clean = parts[1] if len(parts) > 1 else parts[0]
        return clean.lstrip("/\\")

    def _resolve_path(self, object_key: str) -> Path:
        clean = self._clean_key(object_key)
        target = self.base_dir / clean
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def _find_fallback_path(self, object_key: str) -> Path | None:
        clean = self._clean_key(object_key)
        target = self.base_dir / clean
        if target.exists():
            return target

        # Check alternative folder structures
        candidates: list[Path] = []
        if clean.startswith("raw/"):
            # raw/CASE-ID/file -> cases/CASE-ID/raw/file
            parts = clean.split("/")
            if len(parts) >= 3:
                candidates.append(self.base_dir / "cases" / parts[1] / "raw" / "/".join(parts[2:]))
        elif clean.startswith("cases/") and "/raw/" in clean:
            # cases/CASE-ID/raw/file -> raw/CASE-ID/file
            parts = clean.split("/")
            if len(parts) >= 4:
                candidates.append(self.base_dir / "raw" / parts[1] / "/".join(parts[3:]))

        # Check dataset folder if in local dev
        filename = Path(clean).name
        # Remove document prefix if present (e.g. DOC-96208E-01_claim_PT_19116.png -> claim_PT_19116.png)
        raw_name = re.sub(r"^DOC-[A-Za-z0-9]+-\d+_", "", filename)
        dataset_dir = Path("dataset")
        if dataset_dir.exists():
            for match in dataset_dir.glob(f"**/{raw_name}"):
                if match.is_file():
                    candidates.append(match)
            if filename != raw_name:
                for match in dataset_dir.glob(f"**/{filename}"):
                    if match.is_file():
                        candidates.append(match)

        for cand in candidates:
            if cand.exists():
                return cand
        return None

    def put_bytes(
        self,
        object_key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> str:
        with trace_span(
            name="storage_put_bytes",
            run_type="tool",
            component="storage_backend",
            operation="put_bytes",
            inputs={},
        ) as span:
            path = self._resolve_path(object_key)
            path.write_bytes(data)
            uri = build_s3_uri(self.bucket_name, self._clean_key(object_key))
            span.set_summary(status="SUCCESS", is_complete=True)
            return uri

    def get_bytes(self, object_key: str) -> bytes:
        with trace_span(
            name="storage_get_bytes",
            run_type="tool",
            component="storage_backend",
            operation="get_bytes",
            inputs={},
        ) as span:
            path = self._resolve_path(object_key)
            if not path.exists():
                fallback = self._find_fallback_path(object_key)
                if fallback and fallback.exists():
                    val = fallback.read_bytes()
                    span.set_summary(status="SUCCESS", is_complete=True)
                    return val
                span.set_summary(status="FAILED", is_complete=False)
                raise KeyError(f"Object '{object_key}' does not exist in storage: {path}")
            val = path.read_bytes()
            span.set_summary(status="SUCCESS", is_complete=True)
            return val

    def exists(self, object_key: str) -> bool:
        path = self._resolve_path(object_key)
        if path.exists():
            return True
        fallback = self._find_fallback_path(object_key)
        return fallback is not None and fallback.exists()

    def delete(self, object_key: str) -> None:
        path = self._resolve_path(object_key)
        if path.exists():
            path.unlink()
