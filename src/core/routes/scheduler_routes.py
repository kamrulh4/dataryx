"""
API routes for scheduler functionality.
Provides endpoints for managing scheduled workflow jobs.
"""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.database.connection import SessionLocal
from core.database import models as db_models
from core.schemas import scheduler_schemas
from core.dataryx.scheduler_service import scheduler_service
from core.configs import logger
from core.auth.jwt import get_current_user

router = APIRouter()


def get_db():
    """Dependency for database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/jobs", response_model=scheduler_schemas.ScheduledJobResponse, status_code=status.HTTP_201_CREATED)
async def create_scheduled_job(
    job_data: scheduler_schemas.ScheduledJobCreate,
    db: Session = Depends(get_db),
    current_user: db_models.User = Depends(get_current_user)
):
    """
    Create a new scheduled job.
    
    Args:
        job_data: Job configuration including name, flow_id, cron expression
        db: Database session
        current_user: Authenticated user
        
    Returns:
        Created scheduled job details
    """
    try:
        # Create database record
        db_job = db_models.ScheduledJob(
            name=job_data.name,
            description=job_data.description,
            flow_id=job_data.flow_id,
            user_id=current_user.id,
            cron_expression=job_data.cron_expression,
            timezone=job_data.timezone,
            is_active=job_data.is_active,
            max_retries=job_data.max_retries,
            retry_delay_seconds=job_data.retry_delay_seconds
        )
        
        db.add(db_job)
        db.commit()
        db.refresh(db_job)
        
        # Add to scheduler if active
        if db_job.is_active:
            scheduler_service.add_job(
                job_id=db_job.id,
                flow_id=db_job.flow_id,
                cron_expression=db_job.cron_expression,
                timezone=db_job.timezone
            )
        
        logger.info(f"Created scheduled job {db_job.id}: {db_job.name}")
        
        # Prepare response
        response = scheduler_schemas.ScheduledJobResponse.from_orm(db_job)
        response.cron_human_readable = scheduler_schemas.CronExpressionValidator.to_human_readable(
            db_job.cron_expression
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Error creating scheduled job: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create scheduled job: {str(e)}"
        )


@router.get("/jobs", response_model=scheduler_schemas.JobsListResponse)
async def list_scheduled_jobs(
    skip: int = 0,
    limit: int = 100,
    active_only: bool = False,
    db: Session = Depends(get_db),
    current_user: db_models.User = Depends(get_current_user)
):
    """
    List all scheduled jobs for the current user.
    """
    query = db.query(db_models.ScheduledJob).filter(
        db_models.ScheduledJob.user_id == current_user.id
    )
    
    if active_only:
        query = query.filter(db_models.ScheduledJob.is_active == True)
    
    # Get total count
    total_jobs = query.count()
    
    # Get paginated jobs
    jobs = query.offset(skip).limit(limit).all()
    
    # Add human-readable cron descriptions
    response_jobs = []
    for job in jobs:
        job_response = scheduler_schemas.ScheduledJobResponse.from_orm(job)
        job_response.cron_human_readable = scheduler_schemas.CronExpressionValidator.to_human_readable(
            job.cron_expression
        )
        response_jobs.append(job_response)
    
    # Calculate pagination details
    import math
    page = (skip // limit) + 1 if limit > 0 else 1
    total_pages = math.ceil(total_jobs / limit) if limit > 0 else 1
    
    return scheduler_schemas.JobsListResponse(
        jobs=response_jobs,
        total=total_jobs,
        page=page,
        page_size=limit,
        total_pages=total_pages
    )


@router.get("/jobs/{job_id}", response_model=scheduler_schemas.ScheduledJobResponse)
async def get_scheduled_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: db_models.User = Depends(get_current_user)
):
    """
    Get details of a specific scheduled job.
    
    Args:
        job_id: ID of the scheduled job
        db: Database session
        current_user: Authenticated user
        
    Returns:
        Scheduled job details
    """
    job = db.query(db_models.ScheduledJob).filter(
        db_models.ScheduledJob.id == job_id,
        db_models.ScheduledJob.user_id == current_user.id
    ).first()
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scheduled job {job_id} not found"
        )
    
    response = scheduler_schemas.ScheduledJobResponse.from_orm(job)
    response.cron_human_readable = scheduler_schemas.CronExpressionValidator.to_human_readable(
        job.cron_expression
    )
    
    return response


@router.put("/jobs/{job_id}", response_model=scheduler_schemas.ScheduledJobResponse)
async def update_scheduled_job(
    job_id: int,
    job_update: scheduler_schemas.ScheduledJobUpdate,
    db: Session = Depends(get_db),
    current_user: db_models.User = Depends(get_current_user)
):
    """
    Update a scheduled job.
    
    Args:
        job_id: ID of the scheduled job
        job_update: Fields to update
        db: Database session
        current_user: Authenticated user
        
    Returns:
        Updated scheduled job details
    """
    job = db.query(db_models.ScheduledJob).filter(
        db_models.ScheduledJob.id == job_id,
        db_models.ScheduledJob.user_id == current_user.id
    ).first()
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scheduled job {job_id} not found"
        )
    
    try:
        # Update fields
        update_data = job_update.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(job, field, value)
        
        db.commit()
        db.refresh(job)
        
        # Update in scheduler if needed
        if 'cron_expression' in update_data or 'timezone' in update_data or 'is_active' in update_data:
            scheduler_service.remove_job(job_id)
            if job.is_active:
                scheduler_service.add_job(
                    job_id=job.id,
                    flow_id=job.flow_id,
                    cron_expression=job.cron_expression,
                    timezone=job.timezone
                )
        
        logger.info(f"Updated scheduled job {job_id}")
        
        response = scheduler_schemas.ScheduledJobResponse.from_orm(job)
        response.cron_human_readable = scheduler_schemas.CronExpressionValidator.to_human_readable(
            job.cron_expression
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Error updating scheduled job {job_id}: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update scheduled job: {str(e)}"
        )


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scheduled_job(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: db_models.User = Depends(get_current_user)
):
    """
    Delete a scheduled job.
    
    Args:
        job_id: ID of the scheduled job
        db: Database session
        current_user: Authenticated user
    """
    job = db.query(db_models.ScheduledJob).filter(
        db_models.ScheduledJob.id == job_id,
        db_models.ScheduledJob.user_id == current_user.id
    ).first()
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scheduled job {job_id} not found"
        )
    
    try:
        # Remove from scheduler
        scheduler_service.remove_job(job_id)
        
        # Delete from database
        db.delete(job)
        db.commit()
        
        logger.info(f"Deleted scheduled job {job_id}")
        
    except Exception as e:
        logger.error(f"Error deleting scheduled job {job_id}: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete scheduled job: {str(e)}"
        )


@router.post("/jobs/{job_id}/toggle", response_model=scheduler_schemas.ScheduledJobResponse)
async def toggle_scheduled_job(
    job_id: int,
    toggle_data: scheduler_schemas.JobToggleRequest,
    db: Session = Depends(get_db),
    current_user: db_models.User = Depends(get_current_user)
):
    """
    Enable or disable a scheduled job.
    
    Args:
        job_id: ID of the scheduled job
        toggle_data: Active status
        db: Database session
        current_user: Authenticated user
        
    Returns:
        Updated scheduled job details
    """
    job = db.query(db_models.ScheduledJob).filter(
        db_models.ScheduledJob.id == job_id,
        db_models.ScheduledJob.user_id == current_user.id
    ).first()
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scheduled job {job_id} not found"
        )
    
    try:
        job.is_active = toggle_data.is_active
        db.commit()
        db.refresh(job)
        
        # Update scheduler
        if job.is_active:
            scheduler_service.add_job(
                job_id=job.id,
                flow_id=job.flow_id,
                cron_expression=job.cron_expression,
                timezone=job.timezone
            )
        else:
            scheduler_service.remove_job(job_id)
        
        logger.info(f"Toggled scheduled job {job_id} to {'active' if job.is_active else 'inactive'}")
        
        response = scheduler_schemas.ScheduledJobResponse.from_orm(job)
        response.cron_human_readable = scheduler_schemas.CronExpressionValidator.to_human_readable(
            job.cron_expression
        )
        
        return response
        
    except Exception as e:
        logger.error(f"Error toggling scheduled job {job_id}: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to toggle scheduled job: {str(e)}"
        )


@router.get("/jobs/{job_id}/runs", response_model=List[scheduler_schemas.JobRunResponse])
async def get_job_runs(
    job_id: int,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: db_models.User = Depends(get_current_user)
):
    """
    Get execution history for a scheduled job.
    
    Args:
        job_id: ID of the scheduled job
        skip: Number of records to skip
        limit: Maximum number of records to return
        db: Database session
        current_user: Authenticated user
        
    Returns:
        List of job execution records with pagination info
    """
    # Verify job belongs to user
    job = db.query(db_models.ScheduledJob).filter(
        db_models.ScheduledJob.id == job_id,
        db_models.ScheduledJob.user_id == current_user.id
    ).first()
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scheduled job {job_id} not found"
        )
    
    # Calculate total runs for pagination
    total_runs = db.query(db_models.JobRun).filter(
        db_models.JobRun.job_id == job_id
    ).count()

    # Get job runs
    runs = db.query(db_models.JobRun).filter(
        db_models.JobRun.job_id == job_id
    ).order_by(
        db_models.JobRun.started_at.desc()
    ).offset(skip).limit(limit).all()
    
    response_runs = [scheduler_schemas.JobRunResponse.from_orm(run) for run in runs]
    
    # Calculate pagination info
    import math
    page = (skip // limit) + 1 if limit > 0 else 1
    total_pages = math.ceil(total_runs / limit) if limit > 0 else 0
    
    return scheduler_schemas.JobRunsListResponse(
        runs=response_runs,
        total=total_runs,
        page=page,
        page_size=limit,
        total_pages=total_pages
    )


@router.post("/jobs/{job_id}/trigger", status_code=status.HTTP_202_ACCEPTED)
async def trigger_job_execution(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: db_models.User = Depends(get_current_user)
):
    """
    Manually trigger immediate execution of a scheduled job.
    
    Args:
        job_id: ID of the scheduled job
        db: Database session
        current_user: Authenticated user
        
    Returns:
        Acknowledgment message
    """
    job = db.query(db_models.ScheduledJob).filter(
        db_models.ScheduledJob.id == job_id,
        db_models.ScheduledJob.user_id == current_user.id
    ).first()
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scheduled job {job_id} not found"
        )
    
    try:
        # Trigger immediate execution via scheduler
        import asyncio
        asyncio.create_task(
            scheduler_service._execute_flow(job_id=job.id, flow_id=job.flow_id)
        )
        
        logger.info(f"Manually triggered execution of job {job_id}")
        
        return {
            "message": f"Job {job_id} execution triggered",
            "job_id": job_id,
            "job_name": job.name
        }
        
    except Exception as e:
        logger.error(f"Error triggering job {job_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to trigger job execution: {str(e)}"
        )
