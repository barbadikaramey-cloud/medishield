"""
Error Level Analysis (ELA) Image Forensics and JPEG Tamper Detection Engine.
Specification Reference: ADR-017 § Feature 3.

Analyzes non-uniform compression characteristics in JPEG documentation to assist
in discovering digitally edited text, forged seals, spliced dates, and manipulated billing values.
"""

from __future__ import annotations

import io
import logging
from enum import Enum

import numpy as np
from PIL import Image, ImageChops, ImageOps
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Enforce decompression bomb defense (Pillow standard safeguard)
Image.MAX_IMAGE_PIXELS = 50_000_000


class ELAVerdict(str, Enum):
    """Forensic assessment outcome for digital image integrity."""
    ANALYZED_NO_ANOMALY_DETECTED = "ANALYZED_NO_ANOMALY_DETECTED"
    ANOMALY_DETECTED = "ANOMALY_DETECTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"


class ELAResult(BaseModel):
    """Structured report returned by the Error Level Analysis engine."""
    status: ELAVerdict
    mean_error_level: float = 0.0
    max_error_level: float = 0.0
    std_dev_error_level: float = 0.0
    suspicious_region_count: int = 0
    anomaly_score: float = Field(default=0.0, ge=0.0, le=1.0)
    flagged_reasons: list[str] = Field(default_factory=list)
    quality_used: int = 90
    block_size_px: int = 16
    ela_image_bytes: bytes | None = None


class ErrorLevelAnalysisEngine:
    """
    Forensic engine analyzing compression error discrepancies across image blocks.
    Deterministic, offline, pure Pillow + NumPy execution.
    """

    def __init__(self, quality: int = 90, scale_factor: int = 15, block_size: int = 16) -> None:
        self.quality = quality
        self.scale_factor = scale_factor
        self.block_size = block_size

    def analyze_jpeg(
        self,
        image_bytes: bytes,
        quality: int | None = None,
        scale_factor: int | None = None,
        generate_visual: bool = True,
    ) -> ELAResult:
        """
        Executes Error Level Analysis on image bytes.
        Returns typed ELAResult with forensic metrics and optional enhanced visual map.
        """
        target_quality = quality or self.quality
        target_scale = scale_factor or self.scale_factor

        if not image_bytes or len(image_bytes) < 16:
            return ELAResult(
                status=ELAVerdict.ANALYSIS_FAILED,
                flagged_reasons=["Empty or truncated image byte sequence"],
            )

        # 1. Format inspection & safety loading
        try:
            with Image.open(io.BytesIO(image_bytes)) as original:
                img_format = (original.format or "").upper()
                if img_format not in ("JPEG", "JPG", "MPO"):
                    return ELAResult(
                        status=ELAVerdict.NOT_APPLICABLE,
                        flagged_reasons=[f"Image format '{img_format}' is not JPEG; ELA applies exclusively to DCT-compressed JPEG."],
                    )

                # Normalize orientation and convert to RGB
                normalized = ImageOps.exif_transpose(original)
                rgb_img = normalized.convert("RGB")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to open image for ELA forensics: %s", exc)
            return ELAResult(
                status=ELAVerdict.ANALYSIS_FAILED,
                flagged_reasons=[f"Image decompression failure: {type(exc).__name__}"],
            )

        # 2. Resave at reference uniform JPEG quality in memory
        try:
            buf = io.BytesIO()
            rgb_img.save(buf, format="JPEG", quality=target_quality)
            buf.seek(0)
            with Image.open(buf) as recompressed:
                recompressed_rgb = recompressed.convert("RGB")

                # 3. Compute pixel-wise absolute difference
                diff = ImageChops.difference(rgb_img, recompressed_rgb)
                diff_arr = np.asarray(diff, dtype=np.float32)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error during JPEG recompression difference calculation: %s", exc)
            return ELAResult(
                status=ELAVerdict.ANALYSIS_FAILED,
                flagged_reasons=[f"Recompression analysis error: {exc}"],
            )

        # 4. Statistical Metrics across color channels
        # Max error across R, G, B channels per pixel
        max_channel_diff = np.max(diff_arr, axis=2)

        mean_err = float(np.mean(max_channel_diff))
        max_err = float(np.max(max_channel_diff))
        std_err = float(np.std(max_channel_diff))

        # 5. Localized Block Anomaly Detection (16x16 JPEG MCU blocks)
        h, w = max_channel_diff.shape
        bs = self.block_size
        n_blocks_y = max(1, h // bs)
        n_blocks_x = max(1, w // bs)

        # Anomaly threshold: 2.0 standard deviations above mean error, floor of 1.5
        local_threshold = max(1.5, mean_err + 2.0 * std_err)
        anomalous_blocks = np.zeros((n_blocks_y, n_blocks_x), dtype=bool)

        for by in range(n_blocks_y):
            for bx in range(n_blocks_x):
                block = max_channel_diff[by * bs : (by + 1) * bs, bx * bs : (bx + 1) * bs]
                if block.size > 0:
                    b_mean = float(np.mean(block))
                    b_max = float(np.max(block))
                    if b_mean > local_threshold or (b_max >= 20.0 and b_mean > (mean_err + 1.2 * std_err)):
                        anomalous_blocks[by, bx] = True

        # Count distinct connected anomalous regions using 4-connectivity flood fill
        suspicious_regions = self._count_connected_regions(anomalous_blocks)
        anomalous_block_count = int(np.sum(anomalous_blocks))

        # 6. Anomaly Score Formulation [0.0, 1.0]
        anomaly_score = 0.0
        flagged_reasons: list[str] = []

        if suspicious_regions > 0 and max_err >= 18.0:
            region_factor = min(0.40, suspicious_regions * 0.15)
            magnitude_factor = min(0.40, (max_err - 15.0) / 40.0)
            variance_factor = min(0.20, std_err / 15.0)
            anomaly_score = round(min(1.0, 0.40 + region_factor + magnitude_factor + variance_factor), 2)
        else:
            anomaly_score = round(min(0.35, max_err / 180.0), 2)

        # 7. Final Verdict Determination
        if anomaly_score >= 0.70 or suspicious_regions >= 2:
            status = ELAVerdict.ANOMALY_DETECTED
            flagged_reasons.append(
                f"Discovered {suspicious_regions} localized compression anomaly regions ({anomalous_block_count} anomalous blocks, max error: {max_err:.1f}, score: {anomaly_score:.2f}). Possible digital alteration, font splicing, or modified billing amount."
            )
        else:
            status = ELAVerdict.ANALYZED_NO_ANOMALY_DETECTED

        # 8. Render visual difference map if requested
        visual_bytes: bytes | None = None
        if generate_visual:
            try:
                # Scale differences for human review inspectability
                enhanced = np.clip(diff_arr * target_scale, 0, 255).astype(np.uint8)
                vis_img = Image.fromarray(enhanced, mode="RGB")
                out_buf = io.BytesIO()
                vis_img.save(out_buf, format="PNG")
                visual_bytes = out_buf.getvalue()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Failed to render visual ELA PNG: %s", exc)

        return ELAResult(
            status=status,
            mean_error_level=round(mean_err, 2),
            max_error_level=round(max_err, 2),
            std_dev_error_level=round(std_err, 2),
            suspicious_region_count=suspicious_regions,
            anomaly_score=anomaly_score,
            flagged_reasons=flagged_reasons,
            quality_used=target_quality,
            block_size_px=bs,
            ela_image_bytes=visual_bytes,
        )

    def _count_connected_regions(self, mask: np.ndarray) -> int:
        """Counts 4-connected components in binary 2D block grid using pure NumPy."""
        h, w = mask.shape
        visited = np.zeros_like(mask, dtype=bool)
        regions = 0

        for y in range(h):
            for x in range(w):
                if mask[y, x] and not visited[y, x]:
                    regions += 1
                    # Breadth-first search flood fill
                    queue = [(y, x)]
                    visited[y, x] = True
                    while queue:
                        cy, cx = queue.pop(0)
                        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                            ny, nx = cy + dy, cx + dx
                            if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not visited[ny, nx]:
                                visited[ny, nx] = True
                                queue.append((ny, nx))
        return regions


# Global singleton forensics engine
default_ela_engine = ErrorLevelAnalysisEngine()
