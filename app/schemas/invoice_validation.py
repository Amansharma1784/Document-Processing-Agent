
from typing import Literal

from pydantic import BaseModel, Field


class ValidationIssue(BaseModel):
    field: str
    code: str
    message: str
    severity: Literal["error", "warning"]


class InvoiceValidationResult(BaseModel):
    is_valid: bool
    errors: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)
