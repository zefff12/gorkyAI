"""Человекочитаемые даты и списки на русском — для текстового и голосового ответа."""
from __future__ import annotations

from datetime import date, time

from .models import Event, EventType, Recurrence

MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
              "августа", "сентября", "октября", "ноября", "декабря"]
WEEKDAYS_ACC = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
WEEKDAYS_NOM = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
TYPE_NAMES = {EventType.TASK: "задача", EventType.MEETING: "встреча", EventType.REMINDER: "напоминание"}
TYPE_ACC = {EventType.TASK: "задачу", EventType.MEETING: "встречу", EventType.REMINDER: "напоминание"}


def plural(n: int, one: str, few: str, many: str) -> str:
    n_abs = abs(n) % 100
    if 11 <= n_abs <= 14:
        return many
    if n_abs % 10 == 1:
        return one
    if 2 <= n_abs % 10 <= 4:
        return few
    return many


def human_date(d: date, today: date, with_weekday: bool = True) -> str:
    """«сегодня», «завтра», «в пятницу, 16 октября», «12 марта 2025»."""
    delta = (d - today).days
    if delta == 0:
        return "сегодня"
    if delta == 1:
        return "завтра"
    if delta == 2:
        return "послезавтра"
    if delta == -1:
        return "вчера"
    base = f"{d.day} {MONTHS_GEN[d.month - 1]}"
    if d.year != today.year:
        base += f" {d.year}"
    if with_weekday and 0 < delta < 7:
        prep = "во" if d.weekday() == 1 else "в"
        return f"{prep} {WEEKDAYS_ACC[d.weekday()]}, {base}"
    return base


def human_time(t: time) -> str:
    return t.strftime("%H:%M")


def human_when(ev: Event, today: date) -> str:
    if ev.recurrence:
        rec = {
            Recurrence.DAILY: "каждый день",
            Recurrence.WEEKDAYS: "по будням",
            Recurrence.WEEKLY: f"каждую неделю ({WEEKDAYS_NOM[ev.date.weekday()]})" if ev.date else "каждую неделю",
        }[ev.recurrence]
        return rec + (f" в {human_time(ev.time)}" if ev.time else "")
    if ev.date is None:
        return "без срока"
    s = human_date(ev.date, today)
    if ev.time:
        s += f" в {human_time(ev.time)}"
    return s


def describe(ev: Event, today: date, with_date: bool = True) -> str:
    """Одна строка о записи: «встреча «Созвон с Альфой» — завтра в 15:00»."""
    kind = TYPE_NAMES[ev.type]
    parts = [f"{kind} «{ev.title}»"]
    if with_date:
        parts.append(human_when(ev, today))
    elif ev.time:
        parts.append(human_time(ev.time))
    s = " — ".join(parts)
    if ev.done:
        s += " (выполнено)"
    return s


def count_phrase(events: list[Event]) -> str:
    """«2 встречи и 3 задачи»."""
    meetings = sum(1 for e in events if e.type is EventType.MEETING)
    tasks = len(events) - meetings
    parts = []
    if meetings:
        parts.append(f"{meetings} {plural(meetings, 'встреча', 'встречи', 'встреч')}")
    if tasks:
        parts.append(f"{tasks} {plural(tasks, 'задача', 'задачи', 'задач')}")
    return " и ".join(parts)
