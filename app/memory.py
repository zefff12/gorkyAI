"""Память агента: контекст диалога и его сохранение между запросами и перезапусками.

Что помнит агент:
- focus    — записи из последнего ответа («её», «вторую», «перенеси её на завтра»);
- history  — накопительная история упомянутых записей, самые свежие первыми
             (focus перезаписывается каждым ответом, history — нет);
- last     — последний запрос списка/поиска, чтобы понимать короткие реплики
             «а на завтра?», «а встречи?», «а с Марией?»;
- pending  — незаконченное уточнение («На какое время поставить встречу?»).

Состояние сериализуется в JSON и хранится в таблице dialog_state, поэтому
контекст не теряется при перезагрузке страницы и перезапуске сервера.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

from .models import Event, EventType, Intent, ParsedCommand, Period, Recurrence

HISTORY_SIZE = 30


@dataclass
class Pending:
    kind: str                         # need_title | need_time | need_new_time | need_new_title |
                                      # confirm_conflict | confirm_duplicate | confirm_delete | choose
    draft: Event | None = None
    action: str | None = None         # для choose: reschedule | complete | delete | rename
    candidates: list[Event] = field(default_factory=list)
    cmd: ParsedCommand | None = None
    deadline: bool = False            # дата черновика — срок («до пятницы»), время не нужно


@dataclass
class DialogContext:
    """Сводка о последнем запросе — передаётся в разбор следующей реплики."""

    intent: Intent | None = None
    event_type: EventType | None = None
    period: Period | None = None
    query: str | None = None
    tense: str | None = None

    @classmethod
    def from_command(cls, cmd: ParsedCommand) -> "DialogContext":
        return cls(cmd.intent, cmd.event_type, cmd.period, cmd.query, cmd.tense)


@dataclass
class Session:
    focus: list[int] = field(default_factory=list)
    history: list[int] = field(default_factory=list)
    last: DialogContext | None = None
    pending: Pending | None = None

    def remember(self, ids: list[int | None]) -> None:
        """Сделать записи текущим фокусом и добавить их в начало истории."""
        ids = [i for i in ids if i is not None]
        self.focus = ids
        self.history = (ids + [h for h in self.history if h not in ids])[:HISTORY_SIZE]

    def forget(self, event_id: int) -> None:
        self.focus = [i for i in self.focus if i != event_id]
        self.history = [i for i in self.history if i != event_id]

    def recency(self, event_id: int) -> int | None:
        """Насколько давно упоминалась запись: 0 — только что, None — не упоминалась."""
        return self.history.index(event_id) if event_id in self.history else None

    # --- сериализация -----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "focus": self.focus,
            "history": self.history,
            "last": _context_to_dict(self.last),
            "pending": _pending_to_dict(self.pending),
        }

    @classmethod
    def from_dict(cls, data: dict | None, load_event) -> "Session":
        """load_event(id) -> Event | None — подгружает записи-кандидаты из базы."""
        if not data:
            return cls()
        return cls(
            focus=[i for i in data.get("focus", []) if isinstance(i, int)],
            history=[i for i in data.get("history", []) if isinstance(i, int)][:HISTORY_SIZE],
            last=_context_from_dict(data.get("last")),
            pending=_pending_from_dict(data.get("pending"), load_event),
        )


# --- helpers ---------------------------------------------------------------

def _d(v):
    return v.isoformat() if isinstance(v, (date, datetime)) else v


def _t(v):
    return v.strftime("%H:%M") if isinstance(v, time) else v


def _period_to_dict(p: Period | None) -> dict | None:
    return {"start": p.start.isoformat(), "end": p.end.isoformat(), "label": p.label} if p else None


def _period_from_dict(d: dict | None) -> Period | None:
    if not d:
        return None
    return Period(date.fromisoformat(d["start"]), date.fromisoformat(d["end"]), d.get("label", ""))


def _context_to_dict(c: DialogContext | None) -> dict | None:
    if c is None:
        return None
    return {
        "intent": c.intent.value if c.intent else None,
        "event_type": c.event_type.value if c.event_type else None,
        "period": _period_to_dict(c.period),
        "query": c.query,
        "tense": c.tense,
    }


def _context_from_dict(d: dict | None) -> DialogContext | None:
    if not d:
        return None
    return DialogContext(
        intent=Intent(d["intent"]) if d.get("intent") else None,
        event_type=EventType(d["event_type"]) if d.get("event_type") else None,
        period=_period_from_dict(d.get("period")),
        query=d.get("query"),
        tense=d.get("tense"),
    )


def event_to_dict(e: Event) -> dict:
    return e.to_dict() | {"user_id": e.user_id}


def event_from_dict(d: dict) -> Event:
    return Event(
        id=d.get("id"), user_id=d["user_id"], type=EventType(d["type"]), title=d["title"],
        date=date.fromisoformat(d["date"]) if d.get("date") else None,
        time=time.fromisoformat(d["time"]) if d.get("time") else None,
        duration_min=d.get("duration_min") or 60, done=bool(d.get("done")),
        recurrence=Recurrence(d["recurrence"]) if d.get("recurrence") else None,
    )


def command_to_dict(c: ParsedCommand | None) -> dict | None:
    if c is None:
        return None
    return {
        "intent": c.intent.value,
        "event_type": c.event_type.value if c.event_type else None,
        "title": c.title, "date": _d(c.date), "time": _t(c.time),
        "has_deadline": c.has_deadline,
        "recurrence": c.recurrence.value if c.recurrence else None,
        "period": _period_to_dict(c.period),
        "query": c.query, "tense": c.tense, "refers_to_last": c.refers_to_last,
        "source": c.source, "extras": c.extras,
    }


def command_from_dict(d: dict | None) -> ParsedCommand | None:
    if not d:
        return None
    return ParsedCommand(
        intent=Intent(d["intent"]),
        event_type=EventType(d["event_type"]) if d.get("event_type") else None,
        title=d.get("title"),
        date=date.fromisoformat(d["date"]) if d.get("date") else None,
        time=time.fromisoformat(d["time"]) if d.get("time") else None,
        has_deadline=bool(d.get("has_deadline")),
        recurrence=Recurrence(d["recurrence"]) if d.get("recurrence") else None,
        period=_period_from_dict(d.get("period")),
        query=d.get("query"), tense=d.get("tense"), refers_to_last=bool(d.get("refers_to_last")),
        source=d.get("source", "rules"), extras=d.get("extras") or {},
    )


def _pending_to_dict(p: Pending | None) -> dict | None:
    if p is None:
        return None
    return {
        "kind": p.kind,
        "draft": event_to_dict(p.draft) if p.draft else None,
        "action": p.action,
        "candidates": [e.id for e in p.candidates],
        "cmd": command_to_dict(p.cmd),
        "deadline": p.deadline,
    }


def _pending_from_dict(d: dict | None, load_event) -> Pending | None:
    if not d:
        return None
    draft = event_from_dict(d["draft"]) if d.get("draft") else None
    if draft is not None and draft.id is not None:
        # черновик ссылается на сохранённую запись — берём актуальную версию из базы
        draft = load_event(draft.id) or None
        if draft is None:
            return None
    candidates = [e for e in (load_event(i) for i in d.get("candidates", [])) if e]
    if d["kind"] == "choose" and not candidates:
        return None
    return Pending(kind=d["kind"], draft=draft, action=d.get("action"), candidates=candidates,
                   cmd=command_from_dict(d.get("cmd")), deadline=bool(d.get("deadline")))
