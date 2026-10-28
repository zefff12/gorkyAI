"""HTTP-слой: FastAPI + статика. Только транспорт, логика — в assistant.py."""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config
from .assistant import UPCOMING_DAYS, Assistant
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


def _user(x_user_id: str | None) -> str:
    uid = (x_user_id or DEMO_USER).strip()
    if not USER_RE.match(uid):
        raise HTTPException(400, "Некорректный идентификатор пользователя")
    return uid


class MessageIn(BaseModel):
    text: str = Field(..., max_length=1000)
    session_id: str = Field("default", max_length=64)


@app.post("/api/message")
def message(body: MessageIn, x_user_id: str | None = Header(None)):
    reply = assistant.handle(_user(x_user_id), body.text, body.session_id)
    return reply.to_dict()


@app.get("/api/events")
def events(
    start: date | None = Query(None),
    end: date | None = Query(None),
    undated: bool = Query(True, description="добавить невыполненные записи без даты"),
    x_user_id: str | None = Header(None),
):
    today = config.now().date()
    start = start or today
    end = end or start + timedelta(days=UPCOMING_DAYS - 1)
    if (end - start).days > 366:
        raise HTTPException(400, "Слишком большой период")
    return [e.to_dict() for e in storage.in_range(_user(x_user_id), start, end, include_undated=undated)]


class EventPatch(BaseModel):
    done: bool


@app.patch("/api/events/{event_id}")
def update_event(event_id: int, body: EventPatch, x_user_id: str | None = Header(None)):
    """Отметить запись выполненной (или снять отметку) кнопкой в интерфейсе."""
    uid = _user(x_user_id)
    ev = storage.get(uid, event_id)
    if ev is None:
        raise HTTPException(404, "Запись не найдена")
    ev.done = body.done
    storage.update(ev)
    return ev.to_dict()


@app.delete("/api/events/{event_id}")
def delete_event(event_id: int, x_user_id: str | None = Header(None)):
    """Удалить одну запись кнопкой в интерфейсе."""
    uid = _user(x_user_id)
    if not storage.delete(uid, event_id):
        raise HTTPException(404, "Запись не найдена")
    assistant.forget_event(uid, event_id)
    return {"ok": True}


@app.get("/api/history")
def history(limit: int = Query(50, ge=1, le=200), x_user_id: str | None = Header(None)):
    """История переписки — агент помнит разговор после перезагрузки страницы."""
    return storage.messages(_user(x_user_id), limit=limit)


@app.get("/api/export")
def export(x_user_id: str | None = Header(None)):
    """Все данные пользователя: записи, история и контекст диалога."""
    return storage.export_user(_user(x_user_id))


class ImportIn(BaseModel):
    events: list[dict] = Field(default_factory=list, max_length=5000)
    messages: list[dict] = Field(default_factory=list, max_length=500)
    dialog: dict | None = None


@app.post("/api/import")
def import_backup(body: ImportIn, x_user_id: str | None = Header(None)):
    """Восстановить данные из резервной копии браузера (бесплатный хостинг теряет базу
    при перезапуске). Только в пустой профиль, чтобы не задвоить записи."""
    uid = _user(x_user_id)
    if storage.count(uid) > 0 or storage.messages(uid, limit=1):
        raise HTTPException(409, "Профиль не пуст — восстановление не требуется")
    try:
        restored = storage.import_user(uid, body.model_dump())
    except (KeyError, ValueError, TypeError):
        raise HTTPException(400, "Повреждённая резервная копия")
    assistant.reset_user(uid)
    return restored


@app.get("/api/stats")
def stats(x_user_id: str | None = Header(None)):
    return {"total": storage.count(_user(x_user_id))}


@app.post("/api/reset")
def reset(x_user_id: str | None = Header(None)):
    """Удалить все записи пользователя."""
    uid = _user(x_user_id)
    deleted = storage.delete_all(uid)
    assistant.reset_user(uid)
    return {"ok": True, "deleted": deleted}


@app.post("/api/demo-data")
def demo_data(x_user_id: str | None = Header(None)):
    """Загрузить пользователю 1000 тестовых записей из задания (только в пустой список)."""
    uid = _user(x_user_id)
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


STATIC_DIR = config.BASE_DIR / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
