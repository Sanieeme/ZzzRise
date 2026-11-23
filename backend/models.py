from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from .database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(80), nullable=False)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    sessions = relationship("SleepSession", back_populates="user")


class SleepSession(Base):
    __tablename__ = "sleep_sessions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    started_at = Column(DateTime(timezone=True), nullable=False, index=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    target_minutes = Column(Integer, nullable=False, default=480)
    start_method = Column(String(40), nullable=False, default="manual")
    status = Column(String(20), nullable=False, default="active")
    notes = Column(Text, nullable=True)
    user = relationship("User", back_populates="sessions")
    events = relationship("SleepEvent", back_populates="session", cascade="all, delete-orphan")


class SleepEvent(Base):
    __tablename__ = "sleep_events"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("sleep_sessions.id"), nullable=True)
    event_type = Column(String(60), nullable=False)
    recorded_at = Column(DateTime(timezone=True), nullable=False)
    value = Column(Float, nullable=True)
    details = Column(Text, nullable=True)
    session = relationship("SleepSession", back_populates="events")


class SleepJournal(Base):
    __tablename__ = "sleep_journal"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    entry_date = Column(String(10), nullable=False, index=True)
    mood = Column(String(30), nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)


class HealthConnectImport(Base):
    __tablename__ = "health_connect_imports"
    __table_args__ = (UniqueConstraint("user_id", "record_id", name="uq_health_import_user_record"),)
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    record_id = Column(String(255), nullable=False)
    source_app = Column(String(255), nullable=True)
    session_id = Column(Integer, ForeignKey("sleep_sessions.id"), nullable=False)
    imported_at = Column(DateTime(timezone=True), nullable=False)


class MobilePairingToken(Base):
    __tablename__ = "mobile_pairing_tokens"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    used_at = Column(DateTime(timezone=True), nullable=True)
