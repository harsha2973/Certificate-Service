from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SkipValidation,
    StringConstraints,
    ValidationError,
    field_validator,
)

from app.generator import is_renderable

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

Text120 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


# ---------- requests ----------
class RecipientIn(BaseModel):
    """Validated one-by-one (not as part of the request envelope) so a single bad
    recipient is reported against that recipient instead of rejecting the whole request."""

    name: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
    ] = Field(
        ...,
        description="Full name of the recipient as it will appear on the certificate.",
        examples=["Ada Lovelace"],
    )
    email: Annotated[
        str,
        StringConstraints(strip_whitespace=True, to_lower=True, max_length=254),
    ] = Field(
        ...,
        description="Email address of the recipient.",
        examples=["ada@example.com"],
    )

    @field_validator("name")
    @classmethod
    def _name_printable(cls, v: str) -> str:
        if not v.isprintable():
            raise ValueError("must not contain control characters")
        return v

    @field_validator("email")
    @classmethod
    def _email_shape(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("is not a valid email address")
        return v


class JobCreate(BaseModel):
    course_name: Text120 = Field(
        ...,
        description="Name of the course, training, or achievement.",
        examples=["Python Bootcamp 2026"],
    )
    organization: Text120 = Field(
        ...,
        description="Name of the issuing organization or institution.",
        examples=["Acme Academy"],
    )
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)] = Field(
        default="Certificate of Completion",
        description="Certificate title printed at the top of the certificate.",
        examples=["Certificate of Completion"],
    )
    issue_date: date | None = Field(
        default=None,
        description="Date of issuance (YYYY-MM-DD). Defaults to today (UTC).",
        examples=["2026-10-09"],
    )
    recipients: list[SkipValidation[RecipientIn]] = Field(
        ...,
        min_length=1,
        description=(
            "List of recipients. Validated individually so a single invalid entry "
            "is recorded as a failure rather than rejecting the entire batch."
        ),
        examples=[
            [
                {"name": "Ada Lovelace", "email": "ada@example.com"},
                {"name": "Alan Turing", "email": "alan@example.com"},
            ]
        ],
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "course_name": "Python Bootcamp 2026",
                "organization": "Acme Academy",
                "title": "Certificate of Completion",
                "issue_date": "2026-10-09",
                "recipients": [
                    {"name": "Ada Lovelace", "email": "ada@example.com"},
                    {"name": "Alan Turing", "email": "alan@example.com"},
                ],
            }
        }
    )

    @field_validator("course_name", "organization", "title")
    @classmethod
    def _renderable(cls, v: str) -> str:
        if not is_renderable(v):
            raise ValueError("contains characters the certificate font cannot render")
        return v


def validate_recipient(raw: Any) -> tuple[RecipientIn | None, str | None]:
    """Return (recipient, None) or (None, human readable error)."""
    if isinstance(raw, RecipientIn):
        return raw, None
    if not isinstance(raw, dict):
        return None, "recipient must be an object with 'name' and 'email'"
    try:
        return RecipientIn.model_validate(raw), None
    except ValidationError as exc:
        parts = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "recipient"
            parts.append(f"{loc}: {err['msg'].removeprefix('Value error, ')}")
        return None, "; ".join(parts)


# ---------- responses ----------
class Progress(BaseModel):
    total: int
    succeeded: int
    failed: int
    pending: int
    percent_complete: float


class FailureOut(BaseModel):
    position: int
    recipient_name: str | None
    recipient_email: str | None
    error: str | None


class JobOut(BaseModel):
    id: str
    status: str
    title: str
    course_name: str
    organization: str
    issue_date: date
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None
    progress: Progress
    failures: list[FailureOut]  # capped, see failures_truncated
    failures_truncated: bool
    certificates_url: str
    download_url: str | None  # zip of all successful certificates; set once the job is finished


class CertificateOut(BaseModel):
    id: str
    job_id: str
    position: int
    recipient_name: str | None
    recipient_email: str | None
    status: str
    error: str | None
    completed_at: datetime | None
    download_url: str | None


class CertificateList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CertificateOut]


class JobList(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[JobOut]
