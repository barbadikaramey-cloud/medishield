"""Administrator Portal & Terminology Catalog API (Spec 08 § 1.4, ADR-012).

Provides administrative control over:
1. Dynamic medical terminology CSV ingestion (CPT, ICD-10, HCPCS, Revenue codes).
2. Terminology catalog search and lookup.
3. System-wide executive adjudication and STP metrics dashboard.
"""

import csv
import io
import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from medishield.api.deps import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    AuthUser,
    InMemoryDBSession,
    get_db,
    require_role,
)
from medishield.api.schemas.admin import (
    AdminDashboardMetrics,
    CodeListResponse,
    CodeUploadSummary,
    MedicalCodeCreateRequest,
    MedicalCodeItem,
)
from medishield.db.catalog import SqlCodeCatalogRepository
from medishield.db.enums import BusinessDecision, CodingSystem
from medishield.db.models import Case, Decision, MedicalCode, Policy

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def _parse_date(val: str | None) -> date | None:
    if not val or not val.strip():
        return None
    cleaned = val.strip()
    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        pass
    for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            from datetime import datetime
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


@router.post(
    "/codes/upload",
    response_model=CodeUploadSummary,
    status_code=status.HTTP_200_OK,
    summary="Upload Medical Code Catalog CSV (Admin Only)",
)
async def upload_medical_codes_csv(
    file: UploadFile = File(...),
    current_user: AuthUser = Depends(require_role(ROLE_ADMIN)),
    db: Session = Depends(get_db),
) -> CodeUploadSummary:
    """
    Ingests a CSV of medical codes (CPT, HCPCS, ICD-10-CM, Revenue) into the database.
    Updates the high-throughput active code cache automatically (ADR-012).
    """
    if not file.filename or not file.filename.lower().endswith((".csv", ".txt")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be a CSV file (.csv or .txt)",
        )

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded CSV file is empty",
        )

    try:
        text_content = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text_content = raw_bytes.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text_content))
    if not reader.fieldnames:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV file contains no header row",
        )

    # Normalize header mapping
    header_map: dict[str, str] = {}
    for col in reader.fieldnames:
        clean = col.strip().lower().replace(" ", "_").replace("-", "_")
        header_map[clean] = col

    code_col = header_map.get("code") or header_map.get("cpt_code") or header_map.get("icd_code")
    desc_col = header_map.get("short_description") or header_map.get("description") or header_map.get("title")

    if not code_col or not desc_col:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CSV must contain at least 'code' and 'short_description' (or 'description') columns.",
        )

    parsed_items: list[dict[str, Any]] = []
    errors: list[str] = []
    systems_updated: set[str] = set()

    for row_idx, row in enumerate(reader, start=2):
        raw_code = (row.get(code_col) or "").strip()
        raw_desc = (row.get(desc_col) or "").strip()
        if not raw_code:
            continue

        raw_sys = (row.get(header_map.get("coding_system", "")) or "CPT").strip().upper()
        # Normalization
        if raw_sys in ("CPT", "CPT-4"):
            sys_enum = CodingSystem.CPT
        elif raw_sys in ("HCPCS", "HCPCS2", "HCPCS_LEVEL_II"):
            sys_enum = CodingSystem.HCPCS
        elif raw_sys in ("ICD10", "ICD-10", "ICD10_CM", "ICD-10-CM"):
            sys_enum = CodingSystem.ICD10_CM
        elif raw_sys in ("REV", "REVENUE", "REV_CODE"):
            sys_enum = CodingSystem.REV_CODE
        elif raw_sys in ("NDC", "DRUG"):
            sys_enum = CodingSystem.NDC
        else:
            try:
                sys_enum = CodingSystem(raw_sys)
            except ValueError:
                errors.append(f"Row {row_idx}: Unsupported coding system '{raw_sys}'")
                continue

        systems_updated.add(sys_enum.value)

        charge_val = None
        raw_charge = (row.get(header_map.get("standard_charge", "")) or "").replace("$", "").replace(",", "").strip()
        if raw_charge:
            try:
                charge_val = Decimal(raw_charge)
            except InvalidOperation:
                charge_val = None


        raw_pa = (row.get(header_map.get("requires_prior_auth", "")) or "").strip().lower()
        prior_auth = raw_pa in ("true", "1", "yes", "t", "y")

        start_date = _parse_date(row.get(header_map.get("effective_from", "")))
        end_date = _parse_date(row.get(header_map.get("effective_to", "")))

        raw_active = (row.get(header_map.get("is_active", "")) or "true").strip().lower()
        is_active = raw_active not in ("false", "0", "no", "f", "n")

        parsed_items.append({
            "code": raw_code,
            "coding_system": sys_enum,
            "short_description": raw_desc or f"Procedure {raw_code}",
            "long_description": (row.get(header_map.get("long_description", "")) or "").strip() or None,
            "category": (row.get(header_map.get("category", "")) or "").strip() or None,
            "standard_charge": charge_val,
            "requires_prior_auth": prior_auth,
            "effective_from": start_date,
            "effective_to": end_date,
            "is_active": is_active,
        })

    if not parsed_items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No valid medical code rows could be parsed from CSV. Sample errors: {errors[:5]}",
        )

    # Ingest into repository
    if isinstance(db, InMemoryDBSession):
        # In-memory dev fallback
        from medishield.db.catalog import InMemoryCodeCatalogRepository
        repo = InMemoryCodeCatalogRepository()
        inserted_count = repo.bulk_upsert(parsed_items)
        for it in parsed_items:
            db.add(it)
    else:
        repo = SqlCodeCatalogRepository(db=db)
        inserted_count = repo.bulk_upsert(parsed_items)

    return CodeUploadSummary(
        records_processed=len(parsed_items) + len(errors),
        records_inserted=inserted_count,
        coding_systems_updated=sorted(systems_updated),
        errors_count=len(errors),
        error_sample=errors[:5],
        message=f"Successfully ingested {inserted_count} codes across {len(systems_updated)} coding system(s).",
    )


@router.get(
    "/codes",
    response_model=CodeListResponse,
    summary="List and Search Medical Codes (Admin & Manager)",
)
def list_medical_codes(
    query: str = Query("", description="Code prefix or description search term"),
    coding_system: CodingSystem | None = Query(None, description="Optional coding system filter"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: AuthUser = Depends(require_role(ROLE_ADMIN, ROLE_MANAGER)),
    db: Session = Depends(get_db),
) -> CodeListResponse:
    """Lists medical codes in the catalog with optional lexical search and pagination."""
    if isinstance(db, InMemoryDBSession):
        from medishield.db.catalog import InMemoryCodeCatalogRepository
        repo = InMemoryCodeCatalogRepository()
        all_results = repo.search_codes(query=query, coding_system=coding_system, limit=1000)
        items = all_results[offset : offset + limit]
        total = len(all_results)
    else:
        repo = SqlCodeCatalogRepository(db=db)
        stmt = select(MedicalCode)
        if query:
            q = f"%{query.strip().upper()}%"
            stmt = stmt.where(
                (MedicalCode.code.ilike(q)) | (MedicalCode.short_description.ilike(q))
            )
        if coding_system is not None:
            stmt = stmt.where(MedicalCode.coding_system == coding_system)

        total_stmt = select(func.count()).select_from(stmt.subquery())
        total = db.scalar(total_stmt) or 0

        paged_stmt = stmt.order_by(MedicalCode.code.asc()).offset(offset).limit(limit)
        items = list(db.scalars(paged_stmt).all())

    code_items = [
        MedicalCodeItem(
            id=str(c.id) if getattr(c, "id", None) else None,
            code=c.code,
            coding_system=c.coding_system,
            short_description=c.short_description,
            long_description=c.long_description,
            category=c.category,
            standard_charge=c.standard_charge,
            requires_prior_auth=c.requires_prior_auth,
            effective_from=c.effective_from,
            effective_to=c.effective_to,
            is_active=c.is_active,
            created_at=c.created_at,
        )
        for c in items
    ]

    return CodeListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=code_items,
    )


@router.post(
    "/codes",
    response_model=MedicalCodeItem,
    status_code=status.HTTP_201_CREATED,
    summary="Create or Update Medical Code (Admin Only)",
)
def create_medical_code(
    payload: MedicalCodeCreateRequest,
    current_user: AuthUser = Depends(require_role(ROLE_ADMIN)),
    db: Session = Depends(get_db),
) -> MedicalCodeItem:
    """Creates or updates a single medical code definition in the catalog."""
    if isinstance(db, InMemoryDBSession):
        from medishield.db.catalog import InMemoryCodeCatalogRepository
        repo = InMemoryCodeCatalogRepository()
    else:
        repo = SqlCodeCatalogRepository(db=db)

    rec = repo.upsert_code(
        code=payload.code,
        coding_system=payload.coding_system,
        short_description=payload.short_description,
        long_description=payload.long_description,
        category=payload.category,
        standard_charge=payload.standard_charge,
        requires_prior_auth=payload.requires_prior_auth,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        is_active=payload.is_active,
    )
    if hasattr(db, "commit"):
        db.commit()

    return MedicalCodeItem(
        id=str(rec.id) if getattr(rec, "id", None) else None,
        code=rec.code,
        coding_system=rec.coding_system,
        short_description=rec.short_description,
        long_description=rec.long_description,
        category=rec.category,
        standard_charge=rec.standard_charge,
        requires_prior_auth=rec.requires_prior_auth,
        effective_from=rec.effective_from,
        effective_to=rec.effective_to,
        is_active=rec.is_active,
        created_at=rec.created_at,
    )


@router.get(
    "/dashboard",
    response_model=AdminDashboardMetrics,
    summary="Get Executive Adjudication & Health Metrics (Admin & Manager)",
)
def get_admin_dashboard_metrics(
    current_user: AuthUser = Depends(require_role(ROLE_ADMIN, ROLE_MANAGER)),
    db: Session = Depends(get_db),
) -> AdminDashboardMetrics:
    """Computes system-wide STP rate, workflow state breakdown, and catalog statistics."""
    if isinstance(db, InMemoryDBSession):
        total_cases = len(db.cases)
        wf_dist: dict[str, int] = {}
        for c in db.cases:
            st = c.workflow_state.value if hasattr(c.workflow_state, "value") else str(c.workflow_state)
            wf_dist[st] = wf_dist.get(st, 0) + 1

        dec_dist: dict[str, int] = {}
        for d in db.decisions:
            dec = d.decision.value if hasattr(d.decision, "value") else str(d.decision)
            dec_dist[dec] = dec_dist.get(dec, 0) + 1

        esc_dist: dict[str, int] = {}
        for d in db.decisions:
            if d.escalation_sub_reason:
                sr = d.escalation_sub_reason.value if hasattr(d.escalation_sub_reason, "value") else str(d.escalation_sub_reason)
                esc_dist[sr] = esc_dist.get(sr, 0) + 1

        total_codes = len(db.medical_codes)
        codes_by_sys: dict[str, int] = {}
        for c in db.medical_codes:
            cs = c.coding_system.value if hasattr(c.coding_system, "value") else str(c.coding_system)
            codes_by_sys[cs] = codes_by_sys.get(cs, 0) + 1

        active_policies = len(db.policies)
    else:
        total_cases = db.scalar(select(func.count(Case.id))) or 0

        # Workflow distribution
        wf_counts = db.execute(
            select(Case.workflow_state, func.count(Case.id)).group_by(Case.workflow_state)
        ).all()
        wf_dist = {
            (st.value if hasattr(st, "value") else str(st)): count for st, count in wf_counts
        }

        # Decision distribution
        dec_counts = db.execute(
            select(Decision.decision, func.count(Decision.id)).where(Decision.is_active.is_(True)).group_by(Decision.decision)
        ).all()
        dec_dist = {
            (dec.value if hasattr(dec, "value") else str(dec)): count for dec, count in dec_counts
        }

        # Escalation distribution
        esc_counts = db.execute(
            select(Decision.escalation_sub_reason, func.count(Decision.id))
            .where(Decision.is_active.is_(True), Decision.escalation_sub_reason.is_not(None))
            .group_by(Decision.escalation_sub_reason)
        ).all()
        esc_dist = {
            (sr.value if hasattr(sr, "value") else str(sr)): count for sr, count in esc_counts if sr is not None
        }

        # Medical code counts
        total_codes = db.scalar(select(func.count(MedicalCode.id))) or 0
        code_counts = db.execute(
            select(MedicalCode.coding_system, func.count(MedicalCode.id))
            .where(MedicalCode.is_active.is_(True))
            .group_by(MedicalCode.coding_system)
        ).all()
        codes_by_sys = {
            (cs.value if hasattr(cs, "value") else str(cs)): count for cs, count in code_counts
        }

        active_policies = db.scalar(select(func.count(Policy.id)).where(Policy.is_active.is_(True))) or 0

    approves = dec_dist.get(BusinessDecision.APPROVE.value, 0)
    stp_rate = round(float(approves / total_cases * 100), 2) if total_cases > 0 else 0.0

    return AdminDashboardMetrics(
        total_cases=total_cases,
        workflow_distribution=wf_dist,
        decision_distribution=dec_dist,
        straight_through_processing_rate=stp_rate,
        escalation_reason_distribution=esc_dist,
        total_medical_codes=total_codes,
        active_codes_by_system=codes_by_sys,
        active_policies_count=active_policies,
        system_status="HEALTHY",
    )

