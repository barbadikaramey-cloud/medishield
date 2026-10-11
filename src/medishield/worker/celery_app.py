"""Celery Application Configuration and Queue Routing (Spec 01 § 2 Layer 3, FEAT-011)."""

from celery import Celery
from kombu import Queue

from medishield.core.config import settings

# Initialize Celery app instance
celery_app = Celery("medishield")

# Broker and backend configuration
celery_app.conf.update(
    broker_url=settings.REDIS_URL,
    result_backend=settings.REDIS_URL,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    # Worker concurrency & prefetch
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Queue Definitions (Spec 01 § 2 Layer 3)
    task_queues=[
        Queue("intake_queue", routing_key="intake.#"),
        Queue("workflow_queue", routing_key="workflow.#"),
        Queue("dlq", routing_key="dlq.#"),
    ],
    task_default_queue="workflow_queue",
    # Task Routing Table
    task_routes={
        "medishield.tasks.intake.*": {"queue": "intake_queue"},
        "medishield.tasks.workflow.*": {"queue": "workflow_queue"},
    },
)

import logging

from celery.signals import worker_process_init


@worker_process_init.connect
def initialize_database(**kwargs):
    """Ensure database tables are created and seeded on worker startup (ADR-008)."""
    logger = logging.getLogger(__name__)
    logger.info("Initializing database on Celery worker startup...")
    try:
        from medishield.db.models import Base
        from medishield.db.schema_compat import prepare_sqlite_schema
        from medishield.db.seeder import seed_benchmark_dataset, seed_demo_cases_if_empty
        from medishield.db.session import engine, get_session_factory
        
        # Strip Postgres-specific types if using SQLite fallback
        if engine.url.get_backend_name() == "sqlite":
            prepare_sqlite_schema()
            
        Base.metadata.create_all(bind=engine)
        
        SessionLocal = get_session_factory()
        with SessionLocal() as session:
            seeded = seed_benchmark_dataset(session)
            seed_demo_cases_if_empty(session)
            logger.info("Worker DB init complete: %d members seeded", seeded)
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to initialize DB on worker startup: %s", e)

