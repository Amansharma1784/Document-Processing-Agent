from typing import List, Optional
from pydantic import BaseModel, Field


class WorkExperience(BaseModel):
    company: Optional[str] = Field(default=None, description="Company or organization name")
    role: Optional[str] = Field(default=None, description="Job title or role")
    start_date: Optional[str] = Field(default=None, description="Start date (ISO or YYYY-MM if possible)")
    end_date: Optional[str] = Field(default=None, description="End date or 'Present'")
    description: Optional[str] = Field(default=None, description="Key responsibilities or achievements")


class Education(BaseModel):
    institution: Optional[str] = Field(default=None, description="School, college, or university name")
    degree: Optional[str] = Field(default=None, description="Degree or qualification earned")
    field_of_study: Optional[str] = Field(default=None, description="Major or field of study")
    graduation_year: Optional[str] = Field(default=None, description="Year of graduation")


class ResumeData(BaseModel):
    full_name: Optional[str] = Field(default=None)
    email: Optional[str] = Field(default=None)
    phone: Optional[str] = Field(default=None)
    location: Optional[str] = Field(default=None)
    summary: Optional[str] = Field(default=None)
    skills: List[str] = Field(default_factory=list)
    work_experience: List[WorkExperience] = Field(default_factory=list)
    education: List[Education] = Field(default_factory=list)
    certifications: List[str] = Field(default_factory=list)
    languages: List[str] = Field(default_factory=list)
