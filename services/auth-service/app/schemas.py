from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.passwords import MAX_PASSWORD_LENGTH, password_policy_violations


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    full_name: str = Field(min_length=1, max_length=200)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("full_name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("full_name must not be blank")
        return value

    @field_validator("password")
    @classmethod
    def _password_policy(cls, value: str) -> str:
        problems = password_policy_violations(value)
        if problems:
            raise ValueError("Password " + ", ".join(problems))
        return value


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=4096)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    full_name: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 (OAuth token type)
    expires_in: int
    expires_at: datetime


class VerifyResponse(BaseModel):
    valid: bool
    customer_id: str
    email: str
    expires_at: datetime
