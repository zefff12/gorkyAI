"""Хранилище на SQLite: записи, состояние диалога и история сообщений.

Модуль ничего не знает про язык: состояние диалога хранится как готовый JSON,
который сериализует app/memory.py.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterator

from .models import Event, EventType, Recurrence

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT    NOT NULL,
    type         TEXT    NOT NULL CHECK (type IN ('task', 'meeting', 'reminder')),
    title        TEXT    NOT NULL,
    date         TEXT,                -- YYYY-MM-DD, NULL у задачи без срока
    time         TEXT,                -- HH:MM, NULL если время не указано
    duration_min INTEGER NOT NULL DEFAULT 60,
    done         INTEGER NOT NULL DEFAULT 0,
    recurrence   TEXT,                -- daily | weekly | weekdays | NULL
    created_at   TEXT    NOT NULL,
    source_text  TEXT                 -- исходная фраза пользователя / строка CSV
);
CREATE INDEX IF NOT EXISTS idx_events_user_date ON events (user_id, date);

-- Память агента: контекст диалога (на кого ссылаются «её», незаконченные уточнения,
-- последний запрос для коротких реплик вроде «а на завтра?») переживает перезапуск.
CREATE TABLE IF NOT EXISTS dialog_state (
    user_id    TEXT PRIMARY KEY,
    state      TEXT NOT NULL,         -- JSON
    updated_at TEXT NOT NULL
);

-- История переписки: показывается при повторном открытии и даёт контекст LLM.
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    role       TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_user ON messages (user_id, id);
"""


def _row_to_event(row: sqlite3.Row) -> Event:
    return Event(
        id=row["id"],
        user_id=row["user_id"],
        type=EventType(row["type"]),
        title=row["title"],
        date=date.fromisoformat(row["date"]) if row["date"] else None,
        time=time.fromisoformat(row["time"]) if row["time"] else None,
        duration_min=row["duration_min"],
        done=bool(row["done"]),
        recurrence=Recurrence(row["recurrence"]) if row["recurrence"] else None,
        created_at=datetime.fromisoformat(row["created_at"]),
        source_text=row["source_text"],
    )


def _occurs_on(ev: Event, day: date) -> bool:
    """Попадает ли повторяющаяся запись на конкретный день."""
    if ev.date is None or day < ev.date:
        return False
    if ev.recurrence is Recurrence.DAILY:
        return True
    if ev.recurrence is Recurrence.WEEKLY:
        return day.weekday() == ev.date.weekday()
    if ev.recurrence is Recurrence.WEEKDAYS:
        return day.weekday() < 5
    return day == ev.date


class Storage:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # Для :memory: держим одно соединение, иначе база исчезнет между вызовами.
        self._shared = sqlite3.connect(":memory:", check_same_thread=False) if self.path == ":memory:" else None
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = self._shared or sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            if conn is not self._shared:
                conn.close()

    # --- CRUD -------------------------------------------------------------

    def add(self, ev: Event) -> Event:
        created = ev.created_at or datetime.now().replace(microsecond=0)
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO events (user_id, type, title, date, time, duration_min, done, recurrence, created_at, source_text)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ev.user_id, ev.type.value, ev.title,
                    ev.date.isoformat() if ev.date else None,
                    ev.time.strftime("%H:%M") if ev.time else None,
                    ev.duration_min, int(ev.done),
                    ev.recurrence.value if ev.recurrence else None,
                    created.isoformat(timespec="seconds"), ev.source_text,
                ),
            )
        return replace(ev, id=cur.lastrowid, created_at=created)

    def add_many(self, events: list[Event]) -> None:
        with self._conn() as c:
            c.executemany(
                "INSERT INTO events (user_id, type, title, date, time, duration_min, done, recurrence, created_at, source_text)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        e.user_id, e.type.value, e.title,
                        e.date.isoformat() if e.date else None,
                        e.time.strftime("%H:%M") if e.time else None,
                        e.duration_min, int(e.done),
                        e.recurrence.value if e.recurrence else None,
                        (e.created_at or datetime.now()).isoformat(timespec="seconds"), e.source_text,
                    )
                    for e in events
                ],
            )

    def get(self, user_id: str, event_id: int) -> Event | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM events WHERE id = ? AND user_id = ?", (event_id, user_id)).fetchone()
        return _row_to_event(row) if row else None

    def update(self, ev: Event) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE events SET type = ?, title = ?, date = ?, time = ?, duration_min = ?, done = ?, recurrence = ?"
                " WHERE id = ? AND user_id = ?",
                (
                    ev.type.value, ev.title,
                    ev.date.isoformat() if ev.date else None,
                    ev.time.strftime("%H:%M") if ev.time else None,
                    ev.duration_min, int(ev.done),
                    ev.recurrence.value if ev.recurrence else None,
                    ev.id, ev.user_id,
                ),
            )

    def delete(self, user_id: str, event_id: int) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
        return cur.rowcount > 0

    def delete_all(self, user_id: str) -> int:
        """Удалить все записи пользователя вместе с историей и контекстом диалога."""
        with self._conn() as c:
            cur = c.execute("DELETE FROM events WHERE user_id = ?", (user_id,))
            c.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
            c.execute("DELETE FROM dialog_state WHERE user_id = ?", (user_id,))
        return cur.rowcount

    def count(self, user_id: str | None = None) -> int:
        with self._conn() as c:
            if user_id is None:
                return c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            return c.execute("SELECT COUNT(*) FROM events WHERE user_id = ?", (user_id,)).fetchone()[0]

    # --- выборки ------------------------------------------------------------

    def all(self, user_id: str) -> list[Event]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM events WHERE user_id = ? ORDER BY date, time", (user_id,)).fetchall()
        return [_row_to_event(r) for r in rows]

    def in_range(self, user_id: str, start: date, end: date, include_undated: bool = False) -> list[Event]:
        """Записи с датой в [start, end] плюс вхождения повторяющихся записей.

        include_undated=True добавляет невыполненные записи без даты (date IS NULL):
        иначе задача «без срока» не попала бы ни в один список.
        """
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM events WHERE user_id = ? AND ("
                " (recurrence IS NULL AND date BETWEEN ? AND ?)"
                " OR (recurrence IS NOT NULL AND date <= ?)"
                " OR (? AND date IS NULL AND done = 0))",
                (user_id, start.isoformat(), end.isoformat(), end.isoformat(), int(include_undated)),
            ).fetchall()
        result: list[Event] = []
        for ev in map(_row_to_event, rows):
            if ev.recurrence is None or ev.date is None:
                result.append(ev)
                continue
            day = max(start, ev.date)
            while day <= end:
                if _occurs_on(ev, day):
                    result.append(replace(ev, date=day))
                day += timedelta(days=1)
        # записи без даты — в конце списка
        result.sort(key=lambda e: (e.date is None, e.date or date.max, e.time or time(0, 0), e.id))
        return result

    def find_same(self, user_id: str, ev: Event) -> Event | None:
        """Уже существующая запись того же типа с тем же названием, датой и временем."""
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM events WHERE user_id = ? AND type = ? AND lower(title) = lower(?)"
                " AND date IS ? AND time IS ? AND done = 0 LIMIT 1",
                (user_id, ev.type.value, ev.title, ev.date.isoformat() if ev.date else None,
                 ev.time.strftime("%H:%M") if ev.time else None),
            ).fetchone()
        return _row_to_event(row) if row else None

    # --- память агента -------------------------------------------------------

    def load_dialog(self, user_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT state FROM dialog_state WHERE user_id = ?", (user_id,)).fetchone()
        return json.loads(row["state"]) if row else None

    def save_dialog(self, user_id: str, state: dict) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO dialog_state (user_id, state, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at",
                (user_id, json.dumps(state, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")),
            )

    def add_message(self, user_id: str, role: str, text: str, at: datetime | None = None) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO messages (user_id, role, text, created_at) VALUES (?, ?, ?, ?)",
                      (user_id, role, text, (at or datetime.now()).isoformat(timespec="seconds")))

    def messages(self, user_id: str, limit: int = 50) -> list[dict]:
        """Последние сообщения в хронологическом порядке."""
        with self._conn() as c:
            rows = c.execute("SELECT role, text, created_at FROM messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                             (user_id, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    # --- резервная копия (восстановление после перезапуска хостинга) ---------

    def export_user(self, user_id: str) -> dict:
        return {
            "events": [e.to_dict() | {"source_text": e.source_text} for e in self.all(user_id)],
            "messages": self.messages(user_id, limit=200),
            "dialog": self.load_dialog(user_id),
        }

    def import_user(self, user_id: str, data: dict) -> dict:
        """Восстановить данные пользователя из export_user. Старые id записей
        заменяются новыми, ссылки на них в контексте диалога переназначаются."""
        id_map: dict[int, int] = {}
        events = data.get("events", [])[:5000]
        for raw in events:
            ev = Event(
                id=None, user_id=user_id, type=EventType(raw["type"]), title=str(raw["title"])[:500],
                date=date.fromisoformat(raw["date"]) if raw.get("date") else None,
                time=time.fromisoformat(raw["time"]) if raw.get("time") else None,
                duration_min=int(raw.get("duration_min") or 60), done=bool(raw.get("done")),
                recurrence=Recurrence(raw["recurrence"]) if raw.get("recurrence") else None,
                created_at=datetime.fromisoformat(raw["created_at"]) if raw.get("created_at") else None,
                source_text=raw.get("source_text"),
            )
            saved = self.add(ev)
            if raw.get("id") is not None:
                id_map[int(raw["id"])] = saved.id
        messages = [m for m in data.get("messages", [])[-200:] if m.get("role") in ("user", "assistant") and m.get("text")]
        for m in messages:
            self.add_message(user_id, m["role"], str(m["text"])[:5000],
                             datetime.fromisoformat(m["created_at"]) if m.get("created_at") else None)
        if isinstance(data.get("dialog"), dict):
            self.save_dialog(user_id, _remap_ids(data["dialog"], id_map))
        return {"events": len(events), "messages": len(messages)}

    def conflicts(self, user_id: str, day: date, at: time, duration_min: int, exclude_id: int | None = None) -> list[Event]:
        """Встречи, пересекающиеся по времени с [at, at + duration)."""
        start = datetime.combine(day, at)
        end = start + timedelta(minutes=duration_min)
        found = []
        for ev in self.in_range(user_id, day, day):
            if ev.type is not EventType.MEETING or ev.time is None or ev.id == exclude_id:
                continue
            ev_start = ev.start
            ev_end = ev_start + timedelta(minutes=ev.duration_min)
            if ev_start < end and start < ev_end:
                found.append(ev)
        return found


def _remap_ids(obj, id_map: dict[int, int]):
    """Заменить старые id записей в сохранённом контексте диалога на новые."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ("focus", "history", "candidates") and isinstance(v, list):
                out[k] = [id_map[i] for i in v if isinstance(i, int) and i in id_map]
            elif k == "id" and isinstance(v, int):
                out[k] = id_map.get(v)
            else:
                out[k] = _remap_ids(v, id_map)
        return out
    if isinstance(obj, list):
        return [_remap_ids(v, id_map) for v in obj]
    return obj
