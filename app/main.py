"""HTTP-слой: FastAPI + статика. Только транспорт, логика — в assistant.py."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import auth, config
from .assistant import UPCOMING_DAYS, Assistant
from .models import EventType
from .search import search as search_events
from .seed import DEMO_USER, seed_if_empty
from .storage import Storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

storage = Storage(config.DB_PATH)
seeded = seed_if_empty(storage, config.SEED_CSV) if config.SEED_DEMO else 0
if seeded:
    log.info("Imported %d events from %s for user %r", seeded, config.SEED_CSV.name, DEMO_USER)

llm = None
if config.USE_LLM:
    from .nlu.llm import LLMParser
    llm = LLMParser()
    log.info("LLM parser enabled: %s", config.LLM_MODEL)
else:
    log.info("LLM parser disabled — using rule-based NLU")

embedder = None
if config.SEMANTIC_SEARCH:
    from .semantic import Embedder
    embedder = Embedder(cache_dir=config.MODEL_CACHE)
    embedder.load_in_background()

assistant = Assistant(storage, llm=llm, embedder=embedder)
app = FastAPI(title="Голосовой ассистент задач и встреч")

USER_RE = re.compile(r"^[\w-]{1,40}$")
SESSION_COOKIE = "va_session"


# ------------------------------------------------------------------ пользователь

def current_account(request: Request) -> auth.Account | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    row = storage.user_by_session(auth.token_hash(token))
    return auth.Account(row["id"], row["name"], row["email"]) if row else None


def current_user(request: Request, x_user_id: str | None = Header(None)) -> str:
    """id владельца данных: аккаунт из cookie, иначе гостевой id из заголовка."""
    account = current_account(request)
    if account:
        return account.data_id
    uid = (x_user_id or DEMO_USER).strip()
    # гостевой id не может выдавать себя за аккаунт
    if not USER_RE.match(uid) or uid.startswith(auth.ACCOUNT_PREFIX):
        raise HTTPException(400, "Некорректный идентификатор пользователя")
    return uid


def _set_session(request: Request, response: Response, user_id: int, remember: bool) -> None:
    token, token_hash = auth.new_token()
    lifetime = timedelta(days=auth.SESSION_DAYS_REMEMBER) if remember else timedelta(hours=auth.SESSION_HOURS_SHORT)
    storage.create_auth_session(token_hash, user_id, datetime.now() + lifetime)
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", secure=secure,
                        max_age=int(lifetime.total_seconds()) if remember else None, path="/")


# ------------------------------------------------------------------ аккаунты

class RegisterIn(BaseModel):
    name: str = Field(..., max_length=120)
    email: str = Field(..., max_length=320)
    password: str = Field(..., max_length=200)
    password2: str = Field(..., max_length=200)
    accept: bool = False


class LoginIn(BaseModel):
    email: str = Field(..., max_length=320)
    password: str = Field(..., max_length=200)
    remember: bool = False


@app.post("/api/auth/register")
def register(body: RegisterIn, request: Request, response: Response, x_user_id: str | None = Header(None)):
    name, email = body.name.strip(), auth.normalize_email(body.email)
    errors = {}
    if len(name) < 2:
        errors["name"] = "Введите имя"
    if not auth.valid_email(email):
        errors["email"] = "Некорректная почта"
    problems = auth.password_problems(body.password)
    if problems:
        errors["password"] = "Пароль: " + ", ".join(problems)
    elif body.password != body.password2:
        errors["password2"] = "Пароли не совпадают"
    if not body.accept:
        errors["accept"] = "Нужно принять условия"
    if errors:
        raise HTTPException(422, {"errors": errors})
    user_id = storage.create_user(name, email, auth.hash_password(body.password))
    if user_id is None:
        raise HTTPException(409, {"errors": {"email": "Эта почта уже зарегистрирована"}})
    account = auth.Account(user_id, name, email)
    # всё, что гость успел создать до регистрации, переезжает в аккаунт
    moved = 0
    guest = (x_user_id or "").strip()
    if guest and USER_RE.match(guest) and not guest.startswith(auth.ACCOUNT_PREFIX) and guest != DEMO_USER:
        moved = storage.reassign(guest, account.data_id)
        assistant.reset_user(guest)
    _set_session(request, response, user_id, remember=True)
    return account.to_dict() | {"moved": moved}


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    email = auth.normalize_email(body.email)
    if auth.too_many_failures(email):
        raise HTTPException(429, "Слишком много попыток. Попробуйте через 15 минут")
    row = storage.user_by_email(email)
    if row is None or not auth.verify_password(body.password, row["password_hash"]):
        auth.register_failure(email)
        raise HTTPException(401, "Неверная почта или пароль")
    auth.clear_failures(email)
    _set_session(request, response, row["id"], remember=body.remember)
    return auth.Account(row["id"], row["name"], row["email"]).to_dict()


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        storage.delete_auth_session(auth.token_hash(token))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(request: Request):
    account = current_account(request)
    return account.to_dict() if account else {"authenticated": False}


# ------------------------------------------------------------------ диалог

class MessageIn(BaseModel):
    text: str = Field(..., max_length=1000)
    session_id: str = Field("default", max_length=64)


@app.post("/api/message")
def message(body: MessageIn, uid: str = Depends(current_user)):
    reply = assistant.handle(uid, body.text, body.session_id)
    return reply.to_dict()


@app.get("/api/history")
def history(limit: int = Query(50, ge=1, le=500), uid: str = Depends(current_user)):
    """История переписки — агент помнит разговор после перезагрузки страницы."""
    return storage.messages(uid, limit=limit)


# ------------------------------------------------------------------ записи

@app.get("/api/events")
def events(
    start: date | None = Query(None),
    end: date | None = Query(None),
    undated: bool = Query(True, description="добавить невыполненные записи без даты"),
    uid: str = Depends(current_user),
):
    today = config.now().date()
    start = start or today
    end = end or start + timedelta(days=UPCOMING_DAYS - 1)
    if (end - start).days > 366:
        raise HTTPException(400, "Слишком большой период")
    return [e.to_dict() for e in storage.in_range(uid, start, end, include_undated=undated)]


@app.get("/api/items")
def items(
    start: date | None = Query(None, description="начало периода; без периода — все записи"),
    end: date | None = Query(None),
    types: str | None = Query(None, description="task,meeting,reminder через запятую"),
    status: str = Query("all", pattern="^(all|active|done|overdue)$"),
    undated: bool = Query(True),
    q: str | None = Query(None, max_length=200, description="поиск по смыслу и словам"),
    uid: str = Depends(current_user),
):
    """Все записи с фильтрами по датам, типу и статусу — страницы «Мои задачи», «Встречи» и др."""
    if start and end and end < start:
        start, end = end, start
    wanted = None
    if types:
        try:
            wanted = {EventType(t.strip()) for t in types.split(",") if t.strip()}
        except ValueError:
            raise HTTPException(400, "Неизвестный тип записи")
    today = config.now().date()
    found = storage.query(uid, start, end, wanted, status, undated, today=today)
    if q and q.strip():
        found = [h.event for h in search_events(found, q, today, limit=len(found) or 1, embedder=embedder)]
    return [e.to_dict() for e in found]


@app.get("/api/search")
def quick_search(q: str = Query(..., min_length=1, max_length=200), uid: str = Depends(current_user)):
    """Быстрый поиск для строки в шапке (Ctrl+K)."""
    hits = search_events(storage.all(uid), q, config.now().date(), limit=8, embedder=embedder)
    return [h.event.to_dict() for h in hits]


class EventPatch(BaseModel):
    done: bool


@app.patch("/api/events/{event_id}")
def update_event(event_id: int, body: EventPatch, uid: str = Depends(current_user)):
    """Отметить запись выполненной (или снять отметку) кнопкой в интерфейсе."""
    ev = storage.get(uid, event_id)
    if ev is None:
        raise HTTPException(404, "Запись не найдена")
    ev.done = body.done
    storage.update(ev)
    return ev.to_dict()


@app.delete("/api/events/{event_id}")
def delete_event(event_id: int, uid: str = Depends(current_user)):
    """Удалить одну запись кнопкой в интерфейсе."""
    if not storage.delete(uid, event_id):
        raise HTTPException(404, "Запись не найдена")
    assistant.forget_event(uid, event_id)
    return {"ok": True}


# ------------------------------------------------------------------ данные профиля

@app.get("/api/export")
def export(uid: str = Depends(current_user)):
    """Все данные пользователя: записи, история и контекст диалога."""
    return storage.export_user(uid)


class ImportIn(BaseModel):
    events: list[dict] = Field(default_factory=list, max_length=5000)
    messages: list[dict] = Field(default_factory=list, max_length=500)
    dialog: dict | None = None


@app.post("/api/import")
def import_backup(body: ImportIn, uid: str = Depends(current_user)):
    """Восстановить данные из резервной копии браузера (бесплатный хостинг теряет базу
    при перезапуске). Только в пустой профиль, чтобы не задвоить записи."""
    if storage.count(uid) > 0 or storage.messages(uid, limit=1):
        raise HTTPException(409, "Профиль не пуст — восстановление не требуется")
    try:
        restored = storage.import_user(uid, body.model_dump())
    except (KeyError, ValueError, TypeError):
        raise HTTPException(400, "Повреждённая резервная копия")
    assistant.reset_user(uid)
    return restored


@app.get("/api/stats")
def stats(uid: str = Depends(current_user)):
    return {"total": storage.count(uid)}


@app.post("/api/reset")
def reset(uid: str = Depends(current_user)):
    """Удалить все записи пользователя."""
    deleted = storage.delete_all(uid)
    assistant.reset_user(uid)
    return {"ok": True, "deleted": deleted}


@app.post("/api/demo-data")
def demo_data(uid: str = Depends(current_user)):
    """Загрузить пользователю 1000 тестовых записей из задания (только в пустой список)."""
    if storage.count(uid) > 0:
        raise HTTPException(409, "Тестовые данные загружаются только в пустой список — сначала очистите его")
    return {"imported": seed_if_empty(storage, config.SEED_CSV, uid)}


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "nlu": f"llm:{config.LLM_MODEL}" if llm else "rules",
        "semantic_search": bool(embedder and embedder.ready),
        "now": config.now().isoformat(timespec="minutes"),
        "events_total": storage.count(),
    }


# ------------------------------------------------------------------ страницы

STATIC_DIR = config.BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/login")
@app.get("/register")
def auth_page():
    return FileResponse(STATIC_DIR / "auth.html")
