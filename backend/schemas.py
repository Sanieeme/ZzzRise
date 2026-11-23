from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, EmailStr, field_serializer
from datetime import timezone


class RegisterInput(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserRead(BaseModel):
    id: int
    name: str
    email: str


class SessionCreate(BaseModel):
    started_at: datetime
    target_minutes: int = Field(default=480, ge=1, le=1440)
    start_method: str = Field(default="manual", max_length=40)
    notes: Optional[str] = None


class SessionEnd(BaseModel):
    ended_at: datetime


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: Optional[int] = None
    started_at: datetime
    ended_at: Optional[datetime]
    target_minutes: int
    start_method: str
    status: str
    notes: Optional[str]

    @field_serializer("started_at", "ended_at")
    def serialize_datetime(self, value: Optional[datetime]):
        if value is None:
            return None
        # SQLite drops timezone metadata; all stored timestamps are UTC.
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class EventCreate(BaseModel):
    session_id: Optional[int] = None
    event_type: str = Field(max_length=60)
    recorded_at: datetime
    value: Optional[float] = None
    details: Optional[str] = None


class JournalCreate(BaseModel):
    entry_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    mood: str = Field(min_length=2, max_length=30)
    notes: Optional[str] = Field(default=None, max_length=1000)


class JournalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    entry_date: str
    mood: str
    notes: Optional[str]
    created_at: datetime


class CoachQuestion(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class MobileLoginInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class HealthConnectSleepRecord(BaseModel):
    record_id: str = Field(min_length=1, max_length=255)
    start_time: datetime
    end_time: datetime
    source_app: Optional[str] = Field(default=None, max_length=255)


class HealthConnectSyncInput(BaseModel):
    records: list[HealthConnectSleepRecord] = Field(max_length=100)
