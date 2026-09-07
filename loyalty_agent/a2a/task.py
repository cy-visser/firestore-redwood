"""
A2A (Agent2Agent) Protocol - Task Models.
Standard request/response task contracts conforming to the A2A specification.
Supports both camelCase (A2A JSON standard) and snake_case field aliases.
"""

from enum import Enum
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import uuid
from pydantic import BaseModel, Field, ConfigDict


class TaskState(str, Enum):
    SUBMITTED = "SUBMITTED"
    WORKING = "WORKING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class TaskRequest(BaseModel):
    """A2A task execution request dispatched from a consumer agent to a provider agent."""
    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:12]}", alias="taskId")
    skill_id: str = Field(..., alias="skillId", description="ID of the declared skill to invoke.")
    session_id: str = Field(..., alias="sessionId", description="Correlation session ID.")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Input parameters for the skill.")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class TaskResponse(BaseModel):
    """A2A task execution response returned by a provider agent."""
    model_config = ConfigDict(populate_by_name=True)

    task_id: str = Field(..., alias="taskId")
    skill_id: str = Field(..., alias="skillId")
    status: TaskState = TaskState.COMPLETED
    output: Dict[str, Any] = Field(default_factory=dict, description="Execution result conforming to skill schema.")
    error_message: Optional[str] = Field(None, alias="errorMessage")
    execution_metadata: Dict[str, Any] = Field(default_factory=dict, alias="executionMetadata")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
