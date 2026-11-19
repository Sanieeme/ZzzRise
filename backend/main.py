import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from .database import Base, engine, get_db
from .models import SleepEvent, SleepSession, User
from .schemas import EventCreate, LoginInput, RegisterInput, SessionCreate, SessionEnd, SessionRead, UserRead

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
        "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
        "style-src-attr 'unsafe-inline'; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
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


def set_login_cookie(response: Response, user: User):
    response.set_cookie(
        COOKIE_NAME, serializer.dumps(str(user.id)), max_age=60 * 60 * 24 * 30,
        httponly=True, samesite="lax", secure=COOKIE_SECURE, path="/",
    )


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "ZzzRise API"}


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


@app.get("/")
def home():
    return FileResponse("frontend/index.html")


app.mount("/static", StaticFiles(directory="frontend"), name="static")
