"""
Pydantic schemas for scheduler functionality.
Handles validation and serialization of scheduled job data.
"""

from typing import Optional, Dict, Any, List
from datetime import datetime
from core.configs import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator
import re


class CronExpressionValidator:
    """
    Utility class for validating cron expressions.
    Supports standard cron format: minute hour day month day_of_week
    """

    @staticmethod
    def validate(expression: str) -> bool:
        """
        Validates a cron expression.

        Args:
            expression: Cron expression string (e.g., "0 */6 * * *")

        Returns:
            True if valid, raises ValueError if invalid
        """
        parts = expression.split()
        if len(parts) != 5:
            raise ValueError(
                "Cron expression must have exactly 5 parts: minute hour day month day_of_week"
            )

        # Basic validation of each part
        ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
        for i, (part, (min_val, max_val)) in enumerate(zip(parts, ranges)):
            if part == "*":
                continue
            if "/" in part or "," in part or "-" in part:
                continue  # Allow special characters for now (full validation would be complex)
            try:
                val = int(part)
                if not (min_val <= val <= max_val):
                    raise ValueError(f"Part {i+1} value {val} out of range")
            except ValueError:
                # If it's not a number and not a special character, it's invalid
                if part not in ["*", "?"]:
                    raise ValueError(f"Invalid cron expression part: {part}")

        return True

    @staticmethod
    def to_human_readable(expression: str) -> str:
        """
        Converts cron expression to human-readable format.

        Args:
            expression: Cron expression string

        Returns:
            Human-readable description
        """
        # Simple conversions for common patterns
        patterns = {
            "* * * * *": "Every minute",
            "0 * * * *": "Every hour",
            "0 0 * * *": "Daily at midnight",
            "0 0 * * 0": "Weekly on Sunday at midnight",
            "0 0 1 * *": "Monthly on the 1st at midnight",
            "*/5 * * * *": "Every 5 minutes",
            "*/15 * * * *": "Every 15 minutes",
            "*/30 * * * *": "Every 30 minutes",
            "0 */6 * * *": "Every 6 hours",
            "0 */12 * * *": "Every 12 hours",
        }

        return patterns.get(expression, f"Custom schedule: {expression}")


class ScheduledJobCreate(BaseModel):
    """
    Schema for creating a new scheduled job.
    """

    name: str = Field(
        ..., min_length=1, max_length=100, description="Name of the scheduled job"
    )
    description: Optional[str] = Field(
        None, max_length=500, description="Optional description"
    )
    flow_id: int = Field(..., description="ID of the flow to execute")
    cron_expression: str = Field(
        ..., description="Cron expression (e.g., '0 * * * *' for hourly)"
    )
    timezone: str = Field(default="UTC", description="Timezone for the schedule")
    is_active: bool = Field(default=True, description="Whether the job is active")
    max_retries: int = Field(
        default=0, ge=0, le=5, description="Maximum retry attempts on failure"
    )
    retry_delay_seconds: int = Field(
        default=60, ge=0, description="Delay between retries in seconds"
    )

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v: str) -> str:
        """Validate cron expression format."""
        CronExpressionValidator.validate(v)
        return v

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        """Validate timezone is a valid string."""
        # Basic validation - could be enhanced with pytz
        if not v or len(v) > 50:
            raise ValueError("Invalid timezone")
        return v


class ScheduledJobUpdate(BaseModel):
    """
    Schema for updating an existing scheduled job.
    All fields are optional to allow partial updates.
    """

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    cron_expression: Optional[str] = None
    timezone: Optional[str] = None
    is_active: Optional[bool] = None
    max_retries: Optional[int] = Field(None, ge=0, le=5)
    retry_delay_seconds: Optional[int] = Field(None, ge=0)

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v: Optional[str]) -> Optional[str]:
        """Validate cron expression format if provided."""
        if v is not None:
            CronExpressionValidator.validate(v)
        return v


class ScheduledJobResponse(BaseModel):
    """
    Schema for scheduled job API responses.
    """

    id: int
    name: str
    description: Optional[str]
    flow_id: int
    user_id: int
    cron_expression: str
    cron_human_readable: Optional[str] = Field(
        default=None, description="Human-readable cron description"
    )
    timezone: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_run_at: Optional[datetime]
    next_run_at: Optional[datetime]
    max_retries: int
    retry_delay_seconds: int

    model_config = ConfigDict(from_attributes=True)


class JobsListResponse(BaseModel):
    """
    Schema for paginated job list response.
    """

    jobs: List[ScheduledJobResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class JobRunResponse(BaseModel):
    """
    Schema for job execution history responses.
    """

    id: int
    job_id: int
    started_at: datetime
    completed_at: Optional[datetime]
    status: str
    error_message: Optional[str]
    run_info: Optional[Dict[str, Any]]
    attempt_number: int

    model_config = ConfigDict(from_attributes=True)


class JobRunsListResponse(BaseModel):
    """
    Schema for paginated job runs list response.
    """

    runs: List[JobRunResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class JobToggleRequest(BaseModel):
    """
    Schema for toggling job active status.
    """

    is_active: bool


class JobExecutionTrigger(BaseModel):
    """
    Schema for manually triggering a job execution.
    """

    run_immediately: bool = True
