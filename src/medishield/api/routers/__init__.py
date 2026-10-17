"""API Routers package."""

from medishield.api.routers.admin import router as admin_router
from medishield.api.routers.audit import router as audit_router
from medishield.api.routers.auth import router as auth_router
from medishield.api.routers.cases import router as cases_router
from medishield.api.routers.policies import router as policies_router
from medishield.api.routers.review import router as review_router

__all__ = [
    "admin_router",
    "audit_router",
    "auth_router",
    "cases_router",
    "policies_router",
    "review_router",
]


