from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    PENDING = "PENDING"
    FETCHING = "FETCHING"
    CONVERTING = "CONVERTING"
    PARSING = "PARSING"
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RunContext(BaseModel):
    run_id: str
    cu_id: str
    status: RunStatus = RunStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.utcnow)
    error_message: Optional[str] = None