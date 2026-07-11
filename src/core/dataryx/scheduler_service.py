"""
Scheduler service using APScheduler for automated workflow execution.
Handles cron-based job scheduling, execution, and retry logic.
"""

import asyncio
from typing import Optional, List
from datetime import datetime
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from sqlalchemy.orm import Session
from pathlib import Path

from core.configs import logger
from core.database.connection import SessionLocal
from core.database import models as db_models
from core.schemas.scheduler_schemas import CronExpressionValidator
from core.dataryx.manage.io_dataryx import open_flow
from shared.storage_config import storage


class SchedulerService:
    """
    Service for managing scheduled workflow executions.
    Uses APScheduler for robust cron-based scheduling.
    """

    def __init__(self):
        self.scheduler: Optional[AsyncIOScheduler] = None
        self._initialized = False

    def initialize(self, database_url: str = "sqlite:///./dataryx.db"):
        """
        Initialize the APScheduler instance.

        Args:
            database_url: SQLAlchemy database URL for job persistence
        """
        if self._initialized:
            logger.warning("Scheduler already initialized")
            return

        jobstores = {"default": SQLAlchemyJobStore(url=database_url)}

        self.scheduler = AsyncIOScheduler(jobstores=jobstores, timezone=pytz.utc)

        self._initialized = True
        logger.info("Scheduler service initialized")

    def start(self):
        """Start the scheduler."""
        if not self._initialized:
            raise RuntimeError("Scheduler not initialized. Call initialize() first")

        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("Scheduler started")

            # Load existing jobs from database
            self._load_jobs_from_database()

    def shutdown(self):
        """Shutdown the scheduler gracefully."""
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=True)
            logger.info("Scheduler shut down")

    def _load_jobs_from_database(self):
        """Load all active scheduled jobs from database and add them to the scheduler."""
        db = SessionLocal()
        try:
            active_jobs = (
                db.query(db_models.ScheduledJob)
                .filter(db_models.ScheduledJob.is_active == True)
                .all()
            )

            for job in active_jobs:
                try:
                    self.add_job(
                        job_id=job.id,
                        flow_id=job.flow_id,
                        cron_expression=job.cron_expression,
                        timezone=job.timezone,
                    )
                    logger.info(f"Loaded job {job.id}: {job.name}")
                except Exception as e:
                    logger.error(f"Failed to load job {job.id}: {e}")

        finally:
            db.close()

    def add_job(
        self, job_id: int, flow_id: int, cron_expression: str, timezone: str = "UTC"
    ):
        """
        Add a new scheduled job to the scheduler.

        Args:
            job_id: Database ID of the scheduled job
            flow_id: ID of the flow to execute
            cron_expression: Cron expression for scheduling
            timezone: Timezone for the schedule
        """
        if not self._initialized:
            raise RuntimeError("Scheduler not initialized")

        # Parse cron expression
        parts = cron_expression.split()
        if len(parts) != 5:
            raise ValueError("Invalid cron expression")

        minute, hour, day, month, day_of_week = parts

        # Create cron trigger
        tz = pytz.timezone(timezone)
        trigger = CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
            timezone=tz,
        )

        # Add job to scheduler
        # Add job to scheduler
        self.scheduler.add_job(
            func=execute_job_logic,
            trigger=trigger,
            id=f"job_{job_id}",
            kwargs={"job_id": job_id, "flow_id": flow_id},
            replace_existing=True,
            misfire_grace_time=300,  # 5 minutes grace period
        )

        # Update next_run_at in database
        self._update_next_run_time(job_id)

        logger.info(f"Added job {job_id} with cron: {cron_expression}")

    def remove_job(self, job_id: int):
        """
        Remove a scheduled job from the scheduler.

        Args:
            job_id: Database ID of the scheduled job
        """
        if not self._initialized:
            return

        try:
            self.scheduler.remove_job(f"job_{job_id}")
            logger.info(f"Removed job {job_id}")
        except Exception as e:
            logger.warning(f"Failed to remove job {job_id}: {e}")

    def pause_job(self, job_id: int):
        """Pause a scheduled job."""
        if not self._initialized:
            return

        try:
            self.scheduler.pause_job(f"job_{job_id}")
            logger.info(f"Paused job {job_id}")
        except Exception as e:
            logger.warning(f"Failed to pause job {job_id}: {e}")

    def resume_job(self, job_id: int):
        """Resume a paused job."""
        if not self._initialized:
            return

        try:
            self.scheduler.resume_job(f"job_{job_id}")
            logger.info(f"Resumed job {job_id}")
        except Exception as e:
            logger.warning(f"Failed to resume job {job_id}: {e}")

    def get_next_run_time(self, job_id: int) -> Optional[datetime]:
        """Get the next scheduled run time for a job."""
        if not self._initialized:
            return None

        try:
            job = self.scheduler.get_job(f"job_{job_id}")
            return job.next_run_time if job else None
        except Exception:
            return None

    def _update_next_run_time(self, job_id: int):
        """Update the next_run_at field in the database."""
        next_run = self.get_next_run_time(job_id)
        if next_run:
            db = SessionLocal()
            try:
                job = (
                    db.query(db_models.ScheduledJob)
                    .filter(db_models.ScheduledJob.id == job_id)
                    .first()
                )
                if job:
                    job.next_run_at = next_run
                    db.commit()
            finally:
                db.close()


async def handle_job_retry(job_id: int, max_retries: int, retry_delay: int):
    """
    Handle retry logic for failed jobs.
    """
    db = SessionLocal()
    try:
        # Get the latest job run
        latest_run = (
            db.query(db_models.JobRun)
            .filter(db_models.JobRun.job_id == job_id)
            .order_by(db_models.JobRun.started_at.desc())
            .first()
        )

        if latest_run and latest_run.attempt_number < max_retries:
            # Schedule a retry
            await asyncio.sleep(retry_delay)

            job = (
                db.query(db_models.ScheduledJob)
                .filter(db_models.ScheduledJob.id == job_id)
                .first()
            )

            if job:
                logger.info(
                    f"Retrying job {job_id}, attempt {latest_run.attempt_number + 1}"
                )
                await execute_job_logic(job_id, job.flow_id)

    finally:
        db.close()


async def execute_job_logic(job_id: int, flow_id: int):
    """
    Execute a scheduled flow.
    """
    db = SessionLocal()
    job_run = None

    try:
        # Get job details
        job = (
            db.query(db_models.ScheduledJob)
            .filter(db_models.ScheduledJob.id == job_id)
            .first()
        )

        if not job:
            logger.error(f"Job {job_id} not found in database")
            return

        # Create job run record
        job_run = db_models.JobRun(
            job_id=job_id,
            started_at=datetime.utcnow(),
            status="running",
            attempt_number=1,
        )
        db.add(job_run)
        db.commit()
        db.refresh(job_run)

        logger.info(
            f"Starting execution of flow {flow_id} (registration_id) for job {job_id}"
        )

        # 1. Load the flow registration to get the path
        registration = (
            db.query(db_models.FlowRegistration)
            .filter(db_models.FlowRegistration.id == flow_id)
            .first()
        )

        if not registration:
            raise ValueError(f"Flow registration {flow_id} not found")

        # 2. Load the flow definition from disk
        flow_path = Path(registration.flow_path)
        if not flow_path.is_absolute():
            # Resolve relative path if necessary
            if not flow_path.exists():
                flow_path = storage.flows_directory / flow_path

        logger.info(f"Loading flow for job {job_id} from {flow_path}")

        if str(flow_path).lower().endswith(".py"):
            # ── Python script: run in a completely isolated OS process ──────────
            import subprocess, sys as _sys

            logger.info(f"Executing external python script: {flow_path}")
            loop = (
                asyncio.get_running_loop()
            )  # get_event_loop() is deprecated in Python 3.10+
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    [_sys.executable, str(flow_path)],
                    capture_output=True,
                    text=True,
                ),
            )

            class MockRunInfo:
                success = result.returncode == 0

                def model_dump(self, mode="json"):
                    return {
                        "success": self.success,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                    }

            run_info_obj = MockRunInfo()
            if not run_info_obj.success:
                raise RuntimeError(
                    f"Script failed (exit {result.returncode}):\n{result.stderr[:500]}"
                )
        else:
            # ── YAML/JSON visual flow: load + run in a thread-pool executor ────
            # This offloads the CPU-bound Polars computation to a worker thread,
            # keeping the Flet asyncio event loop (and thus the UI) responsive.
            logger.info(f"Executing visual flow in thread executor: {flow_path}")
            loop = (
                asyncio.get_running_loop()
            )  # get_event_loop() is deprecated in Python 3.10+

            def _run_flow_sync():
                flow = open_flow(flow_path, user_id=job.user_id)
                return flow.run_graph()

            run_info_obj = await loop.run_in_executor(None, _run_flow_sync)

        # 4. Update job run with results
        job_run.completed_at = datetime.utcnow()
        if run_info_obj and run_info_obj.success:
            job_run.status = "success"
        else:
            job_run.status = "failed"
            job_run.error_message = (
                "Flow execution failed or returned no success indicator"
            )

        # Handle potential serialization issues by using model_dump if available
        if run_info_obj:
            try:
                job_run.run_info = run_info_obj.model_dump(mode="json")
            except (AttributeError, Exception):
                job_run.run_info = {"success": getattr(run_info_obj, "success", False)}

        # Update job's last_run_at
        job.last_run_at = datetime.utcnow()

        db.commit()
        logger.info(
            f"Successfully executed flow {flow_id} for job {job_id}. Status: {job_run.status}"
        )

    except Exception as e:
        logger.error(f"Error executing job {job_id}: {e}", exc_info=True)

        if job_run:
            job_run.completed_at = datetime.utcnow()
            job_run.status = "failed"
            job_run.error_message = str(e)
            db.commit()

        # Handle retries if configured
        if job and job.max_retries > 0:
            await handle_job_retry(job_id, job.max_retries, job.retry_delay_seconds)

    finally:
        db.close()

        # Update next run time
        # Use simple global check or skip if service not available
        try:
            if "scheduler_service" in globals():
                scheduler_service._update_next_run_time(job_id)
        except Exception:
            pass


# Global scheduler instance
scheduler_service = SchedulerService()
