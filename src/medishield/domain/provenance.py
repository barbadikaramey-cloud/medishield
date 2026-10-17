from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field, model_validator

T = TypeVar("T")


class BBox(BaseModel):
    """
    Canonical normalized geometric bounding box [0.0 - 1.0] relative to page dimensions.
    Standardized fields: x0, y0, x1, y1. Clamps minor boundary overshoots to [0.0, 1.0].
    """
    x0: float = Field(..., description="Normalized left coordinate [0.0 - 1.0]")
    y0: float = Field(..., description="Normalized top coordinate [0.0 - 1.0]")
    x1: float = Field(..., description="Normalized right coordinate [0.0 - 1.0]")
    y1: float = Field(..., description="Normalized bottom coordinate [0.0 - 1.0]")

    @model_validator(mode="before")
    @classmethod
    def parse_and_clamp(cls, data: Any) -> Any:
        if isinstance(data, (list, tuple)) and len(data) == 4:
            data = {"x0": data[0], "y0": data[1], "x1": data[2], "y1": data[3]}
        elif isinstance(data, dict) and "x_min" in data and "x0" not in data:
            data = {
                "x0": data.get("x_min"),
                "y0": data.get("y_min"),
                "x1": data.get("x_max"),
                "y1": data.get("y_max"),
            }
        if isinstance(data, dict):
            try:
                x0 = max(0.0, min(1.0, float(data["x0"])))
                y0 = max(0.0, min(1.0, float(data["y0"])))
                x1 = max(0.0, min(1.0, float(data["x1"])))
                y1 = max(0.0, min(1.0, float(data["y1"])))
                # Enforce x1 >= x0, y1 >= y0
                if x1 < x0:
                    x0, x1 = x1, x0
                if y1 < y0:
                    y0, y1 = y1, y0
                # Guarantee minimal non-zero dimension
                if x1 == x0:
                    x1 = min(1.0, round(x0 + 0.001, 4))
                if y1 == y0:
                    y1 = min(1.0, round(y0 + 0.001, 4))
                return {
                    "x0": round(x0, 4),
                    "y0": round(y0, 4),
                    "x1": round(x1, 4),
                    "y1": round(y1, 4),
                }
            except (KeyError, TypeError, ValueError):
                pass
        return data

    @property
    def x_min(self) -> float:
        return self.x0

    @property
    def y_min(self) -> float:
        return self.y0

    @property
    def x_max(self) -> float:
        return self.x1

    @property
    def y_max(self) -> float:
        return self.y1

    def to_list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]


class ProvenanceType(str, Enum):
    """Authoritative provenance classification types (ADR-004)."""
    SPAN = "SPAN"          # Grounded in a specific OCR text span (Quality: 1.0)
    PAGE = "PAGE"          # Fallback: grounded at page level (Quality: 0.5)
    DERIVED = "DERIVED"    # Computed or database-resolved fact
    INFERRED = "INFERRED"  # Interpreted by LLM reasoning without direct span (Quality: 0.0)


class FieldProvenance(BaseModel):
    """Auditable physical provenance linking an extracted fact to document OCR spans."""
    document_id: str = Field(..., description="Referenced document identifier")
    page_number: int = Field(..., ge=1, description="1-indexed physical page number")
    span_id: str | None = Field(default=None, description="Exact OCR span ID, e.g. 'span-p1-042'")
    text_content: str = Field(..., description="Exact raw text snippet extracted from document")
    bbox: BBox | None = Field(default=None, description="Canonical normalized bounding box [x0, y0, x1, y1]")
    bounding_box: list[float] | None = Field(
        default=None,
        description="Legacy normalized coordinates [x0, y0, x1, y1] for backward compatibility",
    )
    provenance_type: ProvenanceType = Field(
        default=ProvenanceType.SPAN,
        description="Categorization of evidence grounding",
    )
    evidence_quality_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Calibrated evidence score: 1.0 for exact span, 0.5 for page fallback, 0.0 for inferred",
    )
    model_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional model extractor confidence score",
    )

    @model_validator(mode="before")
    @classmethod
    def sync_bbox_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "bbox" in data and data["bbox"] is not None:
                b = data["bbox"]
                if isinstance(b, dict):
                    x0 = b.get("x0", b.get("x_min"))
                    y0 = b.get("y0", b.get("y_min"))
                    x1 = b.get("x1", b.get("x_max"))
                    y1 = b.get("y1", b.get("y_max"))
                    if "bounding_box" not in data or data["bounding_box"] is None:
                        data["bounding_box"] = [x0, y0, x1, y1]
            elif "bounding_box" in data and data["bounding_box"] is not None:
                bb = data["bounding_box"]
                if isinstance(bb, (list, tuple)) and len(bb) == 4:
                    data["bbox"] = {"x0": bb[0], "y0": bb[1], "x1": bb[2], "y1": bb[3]}
        return data


class ProvenanceValue(BaseModel, Generic[T]):
    """Generic envelope binding an extracted fact to confidence and auditable provenance."""
    value: Any
    confidence: float = Field(..., ge=0.0, le=1.0, description="Extraction certainty score")
    provenance: FieldProvenance | None = Field(
        default=None,
        description="Physical OCR grounding evidence",
    )
