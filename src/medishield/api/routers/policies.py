"""Policy Administration and Publishing Firewall Router (Spec 08 § 4, ADR-006, FEAT-012)."""

from datetime import UTC, date, datetime

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from medishield.agents.policy_ingestion import EmbeddedQdrantPolicyIndex, PolicyPdfIngestor
from medishield.api.deps import (
    ROLE_ADMIN,
    AuthUser,
    get_db,
    get_storage,
    require_role,
)
from medishield.api.schemas.policies import (
    PolicyPublishRequest,
    PolicyPublishResponse,
    PolicyUploadResponse,
)
from medishield.core.config import settings
from medishield.db.models import Policy, PolicyChunk, PolicyVersion
from medishield.intake.storage import StorageBackend

router = APIRouter(prefix="/policies", tags=["policies"])


@router.post(
    "",
    response_model=PolicyUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload New Policy Version (Admin Only)",
)
async def upload_policy(
    policy_id: str = Form(...),
    version_tag: str = Form(...),
    plan_name: str = Form(...),
    policy_type: str = Form("COMPREHENSIVE_HEALTH"),
    effective_start_date: str = Form(...),
    effective_end_date: str | None = Form(None),
    file: UploadFile = File(...),
    current_user: AuthUser = Depends(require_role(ROLE_ADMIN)),
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> PolicyUploadResponse:
    """
    Uploads a draft policy document. Accessible exclusively to ADMINISTRATOR (Spec 08 § 4.1).
    Draft versions remain quarantined until published via the ADR-006 publishing firewall.
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty policy file payload")

    now = datetime.now(UTC)
    start_dt = date.fromisoformat(effective_start_date)
    end_dt = date.fromisoformat(effective_end_date) if effective_end_date else None

    # Check or create parent Policy aggregate
    parent_policy = db.query(Policy).filter(Policy.policy_id == policy_id).first()
    if not parent_policy:
        parent_policy = Policy(
            policy_id=policy_id,
            plan_name=plan_name,
            policy_type=policy_type,
            is_active=True,
            created_at=now,
        )
        db.add(parent_policy)
        db.flush()

    # S3 Storage Key
    object_key = f"policies/{policy_id}/{version_tag}.pdf"
    storage.put_bytes(object_key, content, "application/pdf")

    # Vector collection target
    collection_name = f"policy_{policy_id.lower().replace('-', '_')}_{version_tag.lower().replace('.', '_')}"

    # Persist PolicyVersion record
    version_rec = PolicyVersion(
        policy_id=parent_policy.id,
        version_tag=version_tag,
        effective_start_date=start_dt,
        effective_end_date=end_dt,
        source_pdf_key=object_key,
        vector_collection_name=collection_name,
        is_published=False,
        created_at=now,
    )
    db.add(version_rec)
    db.commit()

    return PolicyUploadResponse(
        policy_id=policy_id,
        version_tag=version_tag,
        plan_name=plan_name,
        policy_type=policy_type,
        effective_start_date=start_dt,
        effective_end_date=end_dt,
        is_published=False,
        source_pdf_key=object_key,
        created_at=now,
    )


@router.post(
    "/{policy_id}/publish",
    response_model=PolicyPublishResponse,
    summary="Publish Policy Version via Firewall (Admin Only)",
)
def publish_policy_version(
    policy_id: str,
    req: PolicyPublishRequest,
    current_user: AuthUser = Depends(require_role(ROLE_ADMIN)),
    db: Session = Depends(get_db),
    storage: StorageBackend = Depends(get_storage),
) -> PolicyPublishResponse:
    """
    ADR-006 Publishing Firewall: Promotes draft policy version to production vector index.
    Strictly forbidden to non-ADMINISTRATOR roles.
    """
    parent_policy = db.query(Policy).filter(Policy.policy_id == policy_id).first()
    if not parent_policy:
        raise HTTPException(status_code=404, detail=f"Policy {policy_id} not found")

    version_rec = (
        db.query(PolicyVersion)
        .filter(
            PolicyVersion.policy_id == parent_policy.id,
            PolicyVersion.version_tag == req.version_tag,
        )
        .first()
    )
    if not version_rec:
        raise HTTPException(
            status_code=404,
            detail=f"Version {req.version_tag} not found for policy {policy_id}",
        )

    # Publishing is the sole authoritative-corpus write path. Customer intake
    # routes never call this code or receive this dependency.
    try:
        pdf_bytes = storage.get_bytes(version_rec.source_pdf_key)
        parsed_chunks = PolicyPdfIngestor().parse(pdf_bytes, policy_id, req.version_tag)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Policy PDF could not be parsed for publication") from exc
    if not parsed_chunks:
        raise HTTPException(status_code=422, detail="Policy PDF contains no indexable authoritative text")

    index = EmbeddedQdrantPolicyIndex(settings.QDRANT_PATH)
    index.index(parsed_chunks)
    existing_chunk_count = len(version_rec.chunks)
    for offset, item in enumerate(parsed_chunks):
        db.add(PolicyChunk(
            policy_version_id=version_rec.id,
            chunk_index=existing_chunk_count + offset,
            section_number=item.chunk.section_number,
            section_title=item.chunk.section_title,
            chunk_text=item.chunk.chunk_text,
            page_number=item.chunk.page_number,
            vector_id=item.chunk.chunk_id,
            chunk_hash=item.chunk_hash,
            stable_chunk_id=item.chunk.chunk_id,
        ))

    now = datetime.now(UTC)
    version_rec.is_published = True
    version_rec.published_by = current_user.user_id
    version_rec.published_at = now
    db.commit()

    return PolicyPublishResponse(
        policy_id=policy_id,
        version_tag=req.version_tag,
        is_published=True,
        published_by=current_user.user_id,
        published_at=now,
        vector_collection_name=version_rec.vector_collection_name,
    )

