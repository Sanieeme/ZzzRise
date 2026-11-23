import hashlib
import hmac
import os
import secrets
import base64
import io
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from datetime import date, timedelta

from .database import Base, engine, get_db
from .models import HealthConnectImport, MobilePairingToken, SleepEvent, SleepJournal, SleepSession, User
from .schemas import CoachQuestion, EventCreate, HealthConnectSyncInput, JournalCreate, JournalRead, LoginInput, MobileLoginInput, RegisterInput, SessionCreate, SessionEnd, SessionRead, UserRead
from .ai.coach import ask_local_model, LocalAIError

load_dotenv()
Base.metadata.create_all(bind=engine)

# Add the ownership column to databases created by earlier ZzzRise versions.
with engine.begin() as connection:
    columns = {column["name"] for column in inspect(engine).get_columns("sleep_sessions")}
    if "user_id" not in columns:
        connection.execute(text("ALTER TABLE sleep_sessions ADD COLUMN user_id INTEGER REFERENCES users(id)"))

ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
SECRET_KEY = os.getenv("ZZZRISE_SECRET_KEY", "").strip()
if not SECRET_KEY or SECRET_KEY.startswith("replace-this"):
    if ENVIRONMENT == "production":
        raise RuntimeError("Set a unique ZZZRISE_SECRET_KEY before running in production.")
    SECRET_KEY = "zzrise-local-development-only-key-do-not-deploy"
    print("WARNING: Using the local-only development signing key. Set a unique ZZZRISE_SECRET_KEY before deployment.")
serializer = URLSafeTimedSerializer(SECRET_KEY, salt="zzrise-auth")
mobile_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="zzrise-mobile-token")
COOKIE_NAME = "zzrise_session"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true" if ENVIRONMENT == "production" else "false").lower() == "true"

app = FastAPI(
    title="ZzzRise API",
    version="0.3.0",
    docs_url=None if ENVIRONMENT == "production" else "/docs",
    redoc_url=None if ENVIRONMENT == "production" else "/redoc",
    openapi_url=None if ENVIRONMENT == "production" else "/openapi.json",
)

# Lightweight per-process login throttling. For a multi-worker production deployment,
# replace this in-memory store with a shared store such as Redis.
_login_attempts = defaultdict(deque)
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_ATTEMPTS = 10
_coach_requests = defaultdict(deque)
COACH_WINDOW_SECONDS = 60 * 60
COACH_MAX_REQUESTS = 12


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        if origin:
            origin_host = urlsplit(origin).netloc
            if origin_host and origin_host != request.headers.get("host"):
                return JSONResponse(status_code=403, content={"detail": "Request origin was not allowed."})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "style-src-attr 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    )
    if ENVIRONMENT == "production":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


def check_login_rate_limit(request: Request, email: str):
    now = time.monotonic()
    client_ip = request.client.host if request.client else "unknown"
    key = f"{client_ip}:{email.lower().strip()}"
    attempts = _login_attempts[key]
    while attempts and now - attempts[0] > LOGIN_WINDOW_SECONDS:
        attempts.popleft()
    if len(attempts) >= LOGIN_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many login attempts. Please wait 15 minutes and try again.")
    attempts.append(now)


def clear_login_attempts(request: Request, email: str):
    client_ip = request.client.host if request.client else "unknown"
    _login_attempts.pop(f"{client_ip}:{email.lower().strip()}", None)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310000)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 310000)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Please log in to continue.")
    try:
        user_id = int(serializer.loads(token, max_age=60 * 60 * 24 * 30))
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Your session expired. Please log in again.")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="Account not found. Please log in again.")
    return user


def get_sleep_analytics(
    days: int,
    user: User,
    db: Session
):
    """Calculate sleep analytics for the authenticated user."""

    now = datetime.now(timezone.utc)

    # Include today and the previous days in the selected period.
    start_date = now.date() - timedelta(days=days - 1)

    start_datetime = datetime.combine(
        start_date,
        datetime.min.time(),
        tzinfo=timezone.utc
    )

    # Retrieve only this user's completed sleep sessions.
    sessions = (
        db.query(SleepSession)
        .filter(
            SleepSession.user_id == user.id,
            SleepSession.ended_at.isnot(None),
            SleepSession.started_at >= start_datetime,
            SleepSession.started_at <= now
        )
        .order_by(SleepSession.started_at.asc())
        .all()
    )

    # Prepare one analytics record for every day.
    daily_data = {}

    for day_number in range(days):
        current_date = start_date + timedelta(days=day_number)

        daily_data[current_date.isoformat()] = {
            "date": current_date.isoformat(),
            "hours": 0,
            "target_hours": 0,
            "bedtime_hour": [],
            "wake_hour": [],
            "session_count": 0,
            "goals_achieved": 0,
            "goals_missed": 0
        }

    total_minutes = 0
    goals_achieved = 0
    goals_missed = 0
    invalid_sessions = 0

    for session in sessions:

        started_at = as_utc(session.started_at)
        ended_at = as_utc(session.ended_at)

        duration_seconds = (
            ended_at - started_at
        ).total_seconds()

        # Ignore invalid durations and sessions longer than 24 hours.
        if duration_seconds <= 0 or duration_seconds > 86400:
            invalid_sessions += 1
            continue

        duration_minutes = duration_seconds / 60
        duration_hours = duration_minutes / 60

        target_minutes = session.target_minutes or 480

        # Group the session by its start date.
        session_date = started_at.date().isoformat()

        if session_date not in daily_data:
            continue

        day = daily_data[session_date]

        day["hours"] += duration_hours
        day["target_hours"] += target_minutes / 60
        day["session_count"] += 1

        # Calculate bedtime as decimal hours.
        bedtime = (
            started_at.hour
            + started_at.minute / 60
        )

        # Move after-midnight bedtimes to the next part
        # of the chart's 24-hour scale.
        if bedtime < 12:
            bedtime += 24

        day["bedtime_hour"].append(bedtime)

        # Calculate wake-up time.
        wake_time = (
            ended_at.hour
            + ended_at.minute / 60
        )

        day["wake_hour"].append(wake_time)

        total_minutes += duration_minutes

        # Compare actual sleep duration with the user's target.
        if duration_minutes >= target_minutes:
            goals_achieved += 1
            day["goals_achieved"] += 1
        else:
            goals_missed += 1
            day["goals_missed"] += 1

    valid_sessions = sum(
        day["session_count"]
        for day in daily_data.values()
    )

    total_hours = total_minutes / 60

    average_hours = (
        total_hours / valid_sessions
        if valid_sessions > 0
        else 0
    )

    goal_percentage = (
        goals_achieved / valid_sessions * 100
        if valid_sessions > 0
        else 0
    )

    # Prepare clean daily data for Chart.js.
    daily_results = []

    for day in daily_data.values():

        bedtime_values = day.pop("bedtime_hour")
        wake_values = day.pop("wake_hour")

        average_bedtime = (
            sum(bedtime_values) / len(bedtime_values)
            if bedtime_values else None
        )

        average_wake_time = (
            sum(wake_values) / len(wake_values)
            if wake_values else None
        )

        daily_results.append({
            **day,
            "hours": round(day["hours"], 2),
            "target_hours": round(day["target_hours"], 2),
            "bedtime_hour": (
                round(average_bedtime, 2)
                if average_bedtime is not None else None
            ),
            "wake_hour": (
                round(average_wake_time, 2)
                if average_wake_time is not None else None
            )
        })

    return {
        "period_days": days,

        "summary": {
            "total_hours": round(total_hours, 2),
            "average_hours": round(average_hours, 2),
            "session_count": valid_sessions,
            "goal_percentage": round(goal_percentage, 2),
            "goals_achieved": goals_achieved,
            "goals_missed": goals_missed,
            "invalid_sessions": invalid_sessions
        },

        "daily": daily_results
    }


@app.get("/api/analytics/sleep")
def sleep_analytics(
    days: int = Query(default=7, ge=1, le=365),
    user: User = Depends(current_user),
    db: Session = Depends(get_db)
):
    """Return sleep analytics for the logged-in user."""

    return get_sleep_analytics(
        days=days,
        user=user,
        db=db
    )


def mobile_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Sign in to the ZzzRise Android companion first.")
    token = header.split(" ", 1)[1].strip()
    try:
        user_id = int(mobile_serializer.loads(token, max_age=60 * 60 * 24))
    except (BadSignature, SignatureExpired, ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Your mobile sign-in expired. Please sign in again.")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="ZzzRise account not found. Please sign in again.")
    return user


def set_login_cookie(response: Response, user: User):
    response.set_cookie(
        COOKIE_NAME, serializer.dumps(str(user.id)), max_age=60 * 60 * 24 * 30,
        httponly=True, samesite="lax", secure=COOKIE_SECURE, path="/",
    )


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "ZzzRise API", "ai_mode": "local", "ai_model": os.getenv("OLLAMA_MODEL", "qwen2.5:3b")}


@app.post("/api/auth/register", response_model=UserRead, status_code=201)
def register(payload: RegisterInput, response: Response, db: Session = Depends(get_db)):
    email = payload.email.lower().strip()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="An account with this email already exists. Please log in.")
    user = User(name=payload.name.strip(), email=email, password_hash=hash_password(payload.password), created_at=datetime.now(timezone.utc))
    db.add(user)
    db.flush()
    # Keep any sessions from an older single-user version by assigning them to the first account.
    if db.query(SleepSession).filter(SleepSession.user_id.is_(None)).count():
        db.query(SleepSession).filter(SleepSession.user_id.is_(None)).update({SleepSession.user_id: user.id})
    db.commit()
    db.refresh(user)
    set_login_cookie(response, user)
    return user


@app.post("/api/auth/login", response_model=UserRead)
def login(payload: LoginInput, request: Request, response: Response, db: Session = Depends(get_db)):
    email = payload.email.lower().strip()
    check_login_rate_limit(request, email)
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    clear_login_attempts(request, email)
    set_login_cookie(response, user)
    return user


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, httponly=True, samesite="lax", secure=COOKIE_SECURE, path="/")
    return {"message": "You have been logged out."}


@app.get("/api/auth/me", response_model=UserRead)
def me(user: User = Depends(current_user)):
    return user


@app.post("/api/mobile/pairing/create")
def create_mobile_pairing(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Create a one-use QR pairing token valid for five minutes."""
    now = datetime.now(timezone.utc)
    db.query(MobilePairingToken).filter(
        MobilePairingToken.user_id == user.id,
        MobilePairingToken.used_at.is_(None),
    ).delete(synchronize_session=False)
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    expires = now + timedelta(minutes=5)
    db.add(MobilePairingToken(user_id=user.id, token_hash=token_hash, expires_at=expires))
    db.commit()
    try:
        import qrcode
        image = qrcode.make(f"ZZRISEPAIR:{raw_token}")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        qr_data = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    except ImportError as exc:
        print(f"QR IMPORT ERROR: {exc}")
        raise HTTPException(
            status_code=503,
            detail=f"QR dependency error: {exc}"
        )
    return {"qr_data": qr_data, "expires_at": expires.isoformat(), "expires_in": 300, "scan_value": f"ZZRISEPAIR:{raw_token}"}


@app.post("/api/mobile/pairing/claim")
def claim_mobile_pairing(payload: dict, db: Session = Depends(get_db)):
    raw_token = str(payload.get("pairing_token", "")).strip()
    if not raw_token or len(raw_token) > 200:
        raise HTTPException(status_code=400, detail="The QR pairing code is missing or invalid.")
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    pairing = db.query(MobilePairingToken).filter(MobilePairingToken.token_hash == token_hash).first()
    now = datetime.now(timezone.utc)
    if not pairing or pairing.used_at is not None or as_utc(pairing.expires_at) <= now:
        raise HTTPException(status_code=400, detail="This QR code has expired or has already been used. Generate a new one on your computer.")
    pairing.used_at = now
    db.commit()
    user = db.query(User).filter(User.id == pairing.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="The ZzzRise account for this pairing could not be found.")
    return {"access_token": mobile_serializer.dumps(str(user.id)), "token_type": "bearer", "expires_in": 86400, "user": {"name": user.name, "email": user.email}}


@app.post("/api/mobile/auth/login")
def mobile_login(payload: MobileLoginInput, request: Request, db: Session = Depends(get_db)):
    email = payload.email.lower().strip()
    check_login_rate_limit(request, email)
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    clear_login_attempts(request, email)
    return {"access_token": mobile_serializer.dumps(str(user.id)), "token_type": "bearer", "expires_in": 86400, "user": {"name": user.name, "email": user.email}}


@app.post("/api/health-connect/sync")
def sync_health_connect(payload: HealthConnectSyncInput, db: Session = Depends(get_db), user: User = Depends(mobile_current_user)):
    imported = 0
    skipped = 0
    now = datetime.now(timezone.utc)
    for record in payload.records:
        start = as_utc(record.start_time)
        end = as_utc(record.end_time)
        if end <= start or (end - start).total_seconds() > 24 * 60 * 60:
            skipped += 1
            continue
        if db.query(HealthConnectImport).filter(HealthConnectImport.user_id == user.id, HealthConnectImport.record_id == record.record_id).first():
            skipped += 1
            continue
        session = SleepSession(
            user_id=user.id, started_at=start, ended_at=end, target_minutes=480,
            start_method="health_connect", status="completed",
            notes="Imported from Android Health Connect"
        )
        db.add(session)
        db.flush()
        db.add(HealthConnectImport(user_id=user.id, record_id=record.record_id,
                                   source_app=(record.source_app or "")[:255] or None,
                                   session_id=session.id, imported_at=now))
        imported += 1
    db.commit()
    return {"imported": imported, "skipped": skipped, "message": f"Imported {imported} sleep session(s)."}


@app.get("/api/health-connect/status")
def health_connect_status(db: Session = Depends(get_db), user: User = Depends(current_user)):
    total = db.query(HealthConnectImport).filter(HealthConnectImport.user_id == user.id).count()
    latest = (db.query(HealthConnectImport).filter(HealthConnectImport.user_id == user.id)
              .order_by(HealthConnectImport.imported_at.desc()).first())
    return {"connected": total > 0, "imported_sessions": total,
            "last_sync": latest.imported_at if latest else None,
            "message": "Android sleep data has been imported." if total else "No Android sleep sessions imported yet."}


@app.get("/api/sessions", response_model=list[SessionRead])
def list_sessions(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return db.query(SleepSession).filter(SleepSession.user_id == user.id).order_by(SleepSession.started_at.desc()).limit(100).all()


@app.post("/api/sessions", response_model=SessionRead, status_code=201)
def create_session(payload: SessionCreate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    active = db.query(SleepSession).filter(SleepSession.user_id == user.id, SleepSession.status == "active").first()
    if active:
        raise HTTPException(status_code=409, detail="You already have a sleep session in progress.")
    item = SleepSession(**payload.model_dump(), user_id=user.id, status="active")
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@app.patch("/api/sessions/{session_id}/end", response_model=SessionRead)
def end_session(session_id: int, payload: SessionEnd, db: Session = Depends(get_db), user: User = Depends(current_user)):
    item = db.query(SleepSession).filter(SleepSession.id == session_id, SleepSession.user_id == user.id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Sleep session not found.")
    if item.status != "active":
        raise HTTPException(status_code=409, detail="This session has already ended.")
    if as_utc(payload.ended_at) < as_utc(item.started_at):
        raise HTTPException(status_code=400, detail="End time cannot be before the start time.")
    item.ended_at = payload.ended_at
    item.status = "completed"
    db.commit()
    db.refresh(item)
    return item


@app.post("/api/events", status_code=201)
def create_event(payload: EventCreate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if payload.session_id and not db.query(SleepSession).filter(SleepSession.id == payload.session_id, SleepSession.user_id == user.id).first():
        raise HTTPException(status_code=404, detail="Related sleep session not found.")
    event = SleepEvent(**payload.model_dump())
    db.add(event)
    db.commit()
    db.refresh(event)
    return {"id": event.id, "message": "Event recorded"}


@app.get("/api/analytics")
def analytics(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = (db.query(SleepSession)
            .filter(SleepSession.user_id == user.id, SleepSession.status == "completed", SleepSession.ended_at.isnot(None))
            .order_by(SleepSession.started_at.desc()).limit(100).all())
    completed = []
    for row in rows:
        minutes = max(0, (as_utc(row.ended_at) - as_utc(row.started_at)).total_seconds() / 60)
        if minutes <= 1440:
            completed.append({"date": as_utc(row.started_at).date(), "minutes": minutes,
                              "target": row.target_minutes or 480, "met": minutes >= (row.target_minutes or 480)})
    recent7 = [item for item in completed if item["date"] >= date.today() - timedelta(days=6)]
    recent30 = completed[:30]
    last30days = [x for x in completed if x["date"] >= date.today() - timedelta(days=29)]
    avg = sum(x["minutes"] for x in recent30) / len(recent30) if recent30 else 0
    goal_rate = round(sum(x["met"] for x in recent30) / len(recent30) * 100) if recent30 else 0
    monthly_goal_rate = round(sum(x["met"] for x in last30days) / len(last30days) * 100) if last30days else 0
    # This is an application engagement score, not a clinical sleep-quality score.
    duration_component = min(100, round(avg / 480 * 100)) if recent30 else 0
    score = round(duration_component * 0.7 + goal_rate * 0.3) if recent30 else None
    successful_dates = sorted({x["date"] for x in completed if x["met"]}, reverse=True)
    streak = 0
    cursor = date.today()
    if successful_dates and successful_dates[0] < cursor - timedelta(days=1):
        streak = 0
    else:
        date_set = set(successful_dates)
        while cursor in date_set:
            streak += 1
            cursor -= timedelta(days=1)
        if streak == 0 and (date.today() - timedelta(days=1)) in date_set:
            cursor = date.today() - timedelta(days=1)
            while cursor in date_set:
                streak += 1
                cursor -= timedelta(days=1)
    return {
        "completed_sessions": len(completed),
        "average_minutes": round(avg),
        "goal_rate": goal_rate,
        "sleep_score": score,
        "streak_days": streak,
        "last_7_days_minutes": round(sum(x["minutes"] for x in recent7)),
        "last_30_days_minutes": round(sum(x["minutes"] for x in last30days)),
        "last_30_days_sessions": len(last30days),
        "monthly_goal_rate": monthly_goal_rate,
        "last_30_sessions": [{"date": x["date"].isoformat(), "duration_minutes": round(x["minutes"]),
                              "target_minutes": x["target"], "goal_met": x["met"]} for x in recent30],
        "score_note": "ZzzRise estimate based on recorded duration and goal achievement; not a medical measurement."
    }


@app.get("/api/journal", response_model=list[JournalRead])
def list_journal(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return (db.query(SleepJournal).filter(SleepJournal.user_id == user.id)
            .order_by(SleepJournal.entry_date.desc(), SleepJournal.id.desc()).limit(30).all())


@app.post("/api/journal", response_model=JournalRead, status_code=201)
def create_journal(payload: JournalCreate, db: Session = Depends(get_db), user: User = Depends(current_user)):
    try:
        date.fromisoformat(payload.entry_date)
    except ValueError:
        raise HTTPException(status_code=422, detail="Enter a valid journal date.")
    entry = SleepJournal(user_id=user.id, entry_date=payload.entry_date, mood=payload.mood,
                         notes=(payload.notes or "").strip() or None, created_at=datetime.now(timezone.utc))
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@app.post("/api/ai/coach")
async def ask_sleep_coach(payload: CoachQuestion, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Answer using only a small, user-scoped sample and the configured local Ollama model."""
    now = time.monotonic()
    attempts = _coach_requests[user.id]
    while attempts and now - attempts[0] > COACH_WINDOW_SECONDS:
        attempts.popleft()
    if len(attempts) >= COACH_MAX_REQUESTS:
        raise HTTPException(status_code=429, detail="You've reached the hourly AI coach limit. Please try again later.")
    attempts.append(now)

    rows = (db.query(SleepSession)
            .filter(SleepSession.user_id == user.id, SleepSession.status == "completed", SleepSession.ended_at.isnot(None))
            .order_by(SleepSession.started_at.desc()).limit(30).all())
    records = []
    for row in rows:
        minutes = max(0, round((as_utc(row.ended_at) - as_utc(row.started_at)).total_seconds() / 60))
        if 0 < minutes <= 1440:
            target = row.target_minutes or 480
            records.append({
                "date": as_utc(row.started_at).date().isoformat(),
                "duration_hours": round(minutes / 60, 1),
                "target_hours": round(target / 60, 1),
                "goal_met": minutes >= target,
                "source": row.start_method,
            })

    journal_rows = (db.query(SleepJournal).filter(SleepJournal.user_id == user.id)
                    .order_by(SleepJournal.entry_date.desc(), SleepJournal.id.desc()).limit(7).all())
    journal = [{
        "date": item.entry_date,
        "mood": item.mood,
        "notes": (item.notes or "")[:250],
    } for item in journal_rows]

    if records:
        average = round(sum(item["duration_hours"] for item in records) / len(records), 1)
        goal_rate = round(sum(item["goal_met"] for item in records) / len(records) * 100)
    else:
        average = None
        goal_rate = None
    summary = {
        "records_available": len(records),
        "average_duration_hours": average,
        "target_achievement_percent": goal_rate,
        "reference_target_hours": 8,
        "note": "These are app-recorded durations, not clinical sleep measurements.",
    }

    try:
        answer = await ask_local_model(payload.question.strip(), summary, records, journal)
        return {"answer": answer, "records_used": len(records), "journal_entries_used": len(journal), "ai_mode": "local"}
    except LocalAIError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        print(f"Local AI coach request failed: {type(exc).__name__}")
        raise HTTPException(status_code=502, detail="The local AI coach could not respond right now. Please try again shortly.") from exc


@app.get("/")
def home():
    return FileResponse("frontend/index.html")


app.mount("/static", StaticFiles(directory="frontend"), name="static")
