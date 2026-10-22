from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class DocumentResponse(BaseModel):
    id: int
    username: str | None
    email: str | None
    original_filename: str
    storage_key: str
    mime_type: str
    file_size: int
    file_hash: str | None
    status: str
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
