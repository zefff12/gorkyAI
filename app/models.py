"""Доменные модели: запись календаря и разобранная команда пользователя."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import Enum


class EventType(str, Enum):
    TASK = "task"
    MEETING = "meeting"
    REMINDER = "reminder"


class Intent(str, Enum):
    CREATE = "create"          # создать задачу / напоминание / встречу
    LIST = "list"              # что у меня на период
    SEARCH = "search"          # найти старую запись
    RESCHEDULE = "reschedule"  # перенести запись
    COMPLETE = "complete"      # отметить выполненной
    DELETE = "delete"          # удалить
    RENAME = "rename"          # изменить название / текст записи
    CONFIRM = "confirm"        # «да»
    REJECT = "reject"          # «нет», «отмена»
    HELP = "help"
    UNKNOWN = "unknown"


class Recurrence(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    WEEKDAYS = "weekdays"


@dataclass
class Event:
    id: int | None
    user_id: str
    type: EventType
    title: str
    date: date | None = None
    time: time | None = None
    duration_min: int = 60
    done: bool = False
    recurrence: Recurrence | None = None
    created_at: datetime | None = None
    source_text: str | None = None

    @property
    def start(self) -> datetime | None:
        if self.date is None:
            return None
        return datetime.combine(self.date, self.time or time(0, 0))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "title": self.title,
            "date": self.date.isoformat() if self.date else None,
            "time": self.time.strftime("%H:%M") if self.time else None,
            "duration_min": self.duration_min,
            "done": self.done,
            "recurrence": self.recurrence.value if self.recurrence else None,
            "created_at": self.created_at.isoformat(timespec="seconds") if self.created_at else None,
        }


@dataclass
class Period:
    start: date
    end: date            # включительно
    label: str           # «сегодня», «на этой неделе», ...


@dataclass
class ParsedCommand:
    """Результат понимания запроса — одинаковый для LLM- и rule-based парсера."""

    intent: Intent
    event_type: EventType | None = None
    title: str | None = None
    date: date | None = None
    time: time | None = None
    has_deadline: bool = False          # «до пятницы» — срок, а не точное время
    recurrence: Recurrence | None = None
    period: Period | None = None        # для LIST и фильтра поиска
    query: str | None = None            # для SEARCH и для выбора записи при изменении
    tense: str | None = None            # "past" | "future" | None — для поиска
    refers_to_last: bool = False        # «её», «эту встречу» — контекст диалога
    source: str = "rules"               # кто разобрал: rules | llm
    extras: dict = field(default_factory=dict)
