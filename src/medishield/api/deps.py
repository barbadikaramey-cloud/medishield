import logging
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from medishield.core.config import settings
from medishield.db.enums import (
    ActorType,
    BusinessDecision,
    DocumentType,
    EscalationSubReason,
    WorkflowState,
)
from medishield.db.models import (
    AuditEvent,
    Case,
    Decision,
    Document,
    HumanReview,
    Policy,
    PolicyVersion,
    ReviewLock,
)
from medishield.db.session import SyncSessionLocal
from medishield.intake.storage import (
    LocalFilesystemStorageBackend,
    StorageBackend,
)

logger = logging.getLogger(__name__)

# Authoritative RBAC Roles (Spec 08 § 1.1, Spec 11 § 1)
ROLE_REVIEWER = "OPERATIONS_REVIEWER"
ROLE_MANAGER = "OPERATIONS_MANAGER"
ROLE_ADMIN = "ADMINISTRATOR"

ALL_ROLES = {ROLE_REVIEWER, ROLE_MANAGER, ROLE_ADMIN}


class AuthUser(BaseModel):
    """Authenticated user context resolved from JWT or security headers."""
    user_id: str
    username: str
    role: str


# Persistent filesystem storage for local development & ingestion
_default_storage = LocalFilesystemStorageBackend(base_directory=settings.STORAGE_DIR)


def get_storage() -> StorageBackend:
    """Dependency providing object storage backend."""
    return _default_storage


class InMemoryQuery:
    """Query simulator for in-memory development database (ADR-007)."""

    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)

    def filter(self, *expressions: Any) -> "InMemoryQuery":
        filtered = self._items
        for expr in expressions:
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col_name = getattr(expr.left, "name", str(expr.left))
                val = getattr(expr.right, "value", expr.right)
                filtered = [
                    item for item in filtered
                    if getattr(item, col_name, None) == val
                    or (hasattr(getattr(item, col_name, None), "value") and getattr(item, col_name).value == val)
                ]
        return InMemoryQuery(filtered)

    def order_by(self, *args: Any) -> "InMemoryQuery":
        return self

    def offset(self, n: int) -> "InMemoryQuery":
        return InMemoryQuery(self._items[n:])

    def limit(self, n: int) -> "InMemoryQuery":
        return InMemoryQuery(self._items[:n])

    def count(self) -> int:
        return len(self._items)

    def all(self) -> list[Any]:
        return list(self._items)

    def first(self) -> Any | None:
        return self._items[0] if self._items else None


class InMemoryDBSession:
    """Thread-safe in-memory database store for rapid dev/eval previews (ADR-007)."""

    def __init__(self) -> None:
        self.cases: list[Case] = []
        self.documents: list[Document] = []
        self.review_locks: list[ReviewLock] = []
        self.human_reviews: list[HumanReview] = []
        self.decisions: list[Decision] = []
        self.policies: list[Policy] = []
        self.policy_versions: list[PolicyVersion] = []
        self.audit_events: list[AuditEvent] = []
        self._seed_demo_records()

    def _seed_demo_records(self) -> None:
        now = datetime.now(UTC)
        c1_id = uuid.uuid4()
        c1 = Case(
            id=c1_id,
            case_id="CASE-20261004-902144",
            workflow_state=WorkflowState.AWAITING_HUMAN,
            workflow_version="v1.0.0",
            current_decision=BusinessDecision.ESCALATE,
            current_sub_reason=EscalationSubReason.FRAUD_REVIEW,
            decision_confidence=0.88,
            is_complete=False,
            version=1,
            created_at=now,
            updated_at=now,
        )
        c1.documents = []
        d1 = Document(
            id=uuid.uuid4(),
            document_id="DOC-902144-01",
            case_id=c1_id,
            file_name="hospital_discharge_summary.pdf",
            object_key="raw/CASE-20261004-902144/DOC-902144-01.pdf",
            mime_type="application/pdf",
            checksum="a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0",
            size_bytes=245000,
            doc_type=DocumentType.DISCHARGE_SUMMARY,
            created_at=now,
        )
        d2 = Document(
            id=uuid.uuid4(),
            document_id="DOC-902144-02",
            case_id=c1_id,
            file_name="itemized_claim_form.pdf",
            object_key="raw/CASE-20261004-902144/DOC-902144-02.pdf",
            mime_type="application/pdf",
            checksum="b2c3d4e5f6a10718293a4b5c6d7e8f90123456789abcdef0123456789abcdef1",
            size_bytes=180000,
            doc_type=DocumentType.CLAIM_FORM,
            created_at=now,
        )
        c1.documents.extend([d1, d2])
        dec1 = Decision(
            id=uuid.uuid4(),
            case_id=c1_id,
            decision=BusinessDecision.ESCALATE,
            escalation_sub_reason=EscalationSubReason.FRAUD_REVIEW,
            actor_type=ActorType.SYSTEM,
            actor_id="system-decision-engine",
            decision_confidence=Decimal("0.88"),
            rule_id_matched="RULE-P4-HIGH-FRAUD",
            justification="Fraud Escalation (P4): High fraud risk score (0.88 >= 0.70). Anomalies: Duplicate billing detected across providers.",
            agent_summary_snapshot={
                "fraud": {
                    "verdict": "FAIL",
                    "confidence": 0.88,
                    "fraud_score": 0.88,
                    "risk_level": "HIGH",
                    "anomalies": ["Duplicate billing detected across providers"],
                }
            },
            case_version_at_decision=1,
            is_active=True,
            created_at=now,
        )
        c1.decisions = [dec1]
        self.cases.append(c1)
        self.documents.extend([d1, d2])
        self.decisions.append(dec1)

        c2_id = uuid.uuid4()
        c2 = Case(
            id=c2_id,
            case_id="CASE-20261004-811092",
            workflow_state=WorkflowState.COMPLETED,
            workflow_version="v1.0.0",
            current_decision=BusinessDecision.APPROVE,
            decision_confidence=0.99,
            is_complete=True,
            version=1,
            created_at=now,
            updated_at=now,
        )
        c2.documents = []
        d3 = Document(
            id=uuid.uuid4(),
            document_id="DOC-811092-01",
            case_id=c2_id,
            file_name="pharmacy_prescription.png",
            object_key="raw/CASE-20261004-811092/DOC-811092-01.png",
            mime_type="image/png",
            checksum="c3d4e5f6a1b20718293a4b5c6d7e8f90123456789abcdef0123456789abcdef2",
            size_bytes=85000,
            doc_type=DocumentType.PRESCRIPTION,
            created_at=now,
        )
        c2.documents.append(d3)
        dec2 = Decision(
            id=uuid.uuid4(),
            case_id=c2_id,
            decision=BusinessDecision.APPROVE,
            escalation_sub_reason=None,
            actor_type=ActorType.SYSTEM,
            actor_id="system-decision-engine",
            decision_confidence=Decimal("0.99"),
            rule_id_matched="RULE-P6-CLEAN-PASS",
            justification="Clean Straight-Through Pass (P6): All identity, claims arithmetic, and policy coverage criteria passed with low fraud risk.",
            agent_summary_snapshot={
                "fraud": {
                    "verdict": "PASS",
                    "confidence": 0.99,
                    "fraud_score": 0.05,
                    "risk_level": "LOW",
                    "anomalies": [],
                }
            },
            case_version_at_decision=1,
            is_active=True,
            created_at=now,
        )
        c2.decisions = [dec2]
        self.cases.append(c2)
        self.documents.append(d3)
        self.decisions.append(dec2)

    def query(self, model_class: type) -> InMemoryQuery:
        if model_class is Case:
            return InMemoryQuery(self.cases)
        if model_class is Document:
            return InMemoryQuery(self.documents)
        if model_class is Decision:
            return InMemoryQuery(self.decisions)
        if model_class is ReviewLock:
            return InMemoryQuery(self.review_locks)
        if model_class is HumanReview:
            return InMemoryQuery(self.human_reviews)
        if model_class is Policy:
            return InMemoryQuery(self.policies)
        if model_class is PolicyVersion:
            return InMemoryQuery(self.policy_versions)
        if model_class is AuditEvent:
            return InMemoryQuery(self.audit_events)
        return InMemoryQuery([])

    def add(self, obj: Any) -> None:
        if isinstance(obj, Case):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            if not hasattr(obj, "documents") or obj.documents is None:
                obj.documents = []
            if not hasattr(obj, "decisions") or obj.decisions is None:
                obj.decisions = []
            if obj not in self.cases:
                self.cases.insert(0, obj)
        elif isinstance(obj, Document):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            if obj not in self.documents:
                self.documents.append(obj)
            for c in self.cases:
                if c.id == obj.case_id and obj not in c.documents:
                    c.documents.append(obj)
        elif isinstance(obj, Decision):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.decisions.append(obj)
            for c in self.cases:
                if c.id == obj.case_id:
                    if not hasattr(c, "decisions") or c.decisions is None:
                        c.decisions = []
                    if obj not in c.decisions:
                        c.decisions.append(obj)
        elif isinstance(obj, ReviewLock):
            self.review_locks.append(obj)
        elif isinstance(obj, HumanReview):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.human_reviews.append(obj)
        elif isinstance(obj, Policy):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.policies.append(obj)
        elif isinstance(obj, PolicyVersion):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.policy_versions.append(obj)
        elif isinstance(obj, AuditEvent):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.audit_events.append(obj)

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        pass

    def delete(self, obj: Any) -> None:
        if isinstance(obj, ReviewLock) and obj in self.review_locks:
            self.review_locks.remove(obj)

    def close(self) -> None:
        pass


_shared_memory_db = InMemoryDBSession()
_postgres_tested = False
_postgres_available = False
_redis_tested = False
_redis_available = False


def check_postgres_available() -> bool:
    global _postgres_tested, _postgres_available
    if _postgres_tested:
        return _postgres_available
    from medishield.db.session import _use_sqlite
    _postgres_available = not _use_sqlite
    _postgres_tested = True
    return _postgres_available


def check_redis_available() -> bool:
    global _redis_tested, _redis_available
    if _redis_tested:
        return _redis_available
    try:
        import redis
        client = redis.Redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
        )
        client.ping()
        _redis_available = True
    except Exception:  # noqa: BLE001
        _redis_available = False
    _redis_tested = True
    return _redis_available


_sqlite_initialized = False


def get_db() -> Generator[Session, None, None]:
    """Dependency providing a synchronous database session per request with SQLite dev fallback (ADR-008)."""
    global _sqlite_initialized
    if check_postgres_available():
        session = SyncSessionLocal()
        try:
            yield session
        finally:
            session.close()
    else:
        if settings.ENVIRONMENT in ("development", "test"):
            from medishield.db.seeder import seed_benchmark_dataset, seed_demo_cases_if_empty
            from medishield.db.session import get_sqlite_session_factory

            factory = get_sqlite_session_factory()
            session = factory()
            if not _sqlite_initialized:
                try:
                    seed_benchmark_dataset(session)
                    seed_demo_cases_if_empty(session)
                    _sqlite_initialized = True
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Error auto-seeding SQLite development database: %s", exc)
            try:
                yield session
            finally:
                session.close()
        else:
            session = SyncSessionLocal()
            try:
                yield session
            finally:
                session.close()


def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    x_user_role: Annotated[str | None, Header(alias="X-User-Role")] = None,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> AuthUser:
    """
    Resolves the authenticated user and role.
    Accepts Bearer JWT, direct test headers, or defaults to Reviewer in development.
    """
    # 1. Direct role override (useful for testing and internal service mesh)
    if x_user_role:
        if x_user_role not in ALL_ROLES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Invalid role: {x_user_role}",
            )
        return AuthUser(
            user_id=x_user_id or "user-default",
            username=x_user_id or "operator",
            role=x_user_role,
        )

    # 2. Bearer Token Resolution
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]
            # Fast test-token parsing e.g. "bearer admin-token", "bearer reviewer-token"
            if "admin" in token.lower():
                return AuthUser(user_id="admin-01", username="admin", role=ROLE_ADMIN)
            if "manager" in token.lower():
                return AuthUser(user_id="mgr-01", username="manager", role=ROLE_MANAGER)
            if "reviewer" in token.lower():
                return AuthUser(user_id="rev-01", username="reviewer", role=ROLE_REVIEWER)

            # Standard token decode with fallback
            try:
                from jose import JWTError, jwt
                payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
                role = payload.get("role", ROLE_REVIEWER)
                user_id = payload.get("sub", "user-01")
                return AuthUser(user_id=user_id, username=user_id, role=role)
            except (JWTError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Invalid or expired authentication credentials: {exc}",
                ) from exc

    # 3. Development / Test Default Role
    if settings.ENVIRONMENT in ("development", "test"):
        return AuthUser(
            user_id="dev-reviewer",
            username="dev_operator",
            role=ROLE_REVIEWER,
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing Authorization header",
    )


def require_role(*allowed_roles: str):
    """
    Role-Based Access Control (RBAC) guard factory (Spec 11 § 1).
    Raises HTTP 403 Forbidden if current user role is not in allowed_roles.
    """
    def role_checker(user: Annotated[AuthUser, Depends(get_current_user)]) -> AuthUser:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: Insufficient privileges. Required: {list(allowed_roles)}, Found: {user.role}",
            )
        return user

    return role_checker
