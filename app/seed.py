"""Импорт тестового CSV (id,date,time,text) в базу.

В исходных данных две проблемы, которые мы исправляем при импорте:
1. Нет типа записи — определяем его по тексту (встреча / задача / напоминание).
2. Сгенерированный текст с ошибками в падежах («Позвонить Иваном»,
   «Напомнить Анной про аналитике») — исправляем через pymorphy3, чтобы
   ассистент зачитывал записи нормальным языком. Исходная строка
   сохраняется в source_text.
"""
from __future__ import annotations

import csv
import re
from datetime import date, datetime, time
from pathlib import Path

from .models import Event, EventType
from .nlu.text import _morph
from .storage import Storage

DEMO_USER = "demo"
MEETING_PREFIXES = ("встреча", "созвон", "демо", "провести планёрку", "провести планерку")


def _match_case(src: str, dst: str) -> str:
    return dst.capitalize() if src[:1].isupper() else dst


def _reinflect(word: str, from_case: str, to_case: str) -> str:
    if not re.fullmatch(r"[А-Яа-яЁё]+", word):
        return word
    for p in _morph.parse(word):
        if from_case in p.tag:
            r = p.inflect({to_case})
            if r:
                return _match_case(word, r.word)
    return word


# Темы в датасете — закрытый список. Таблица надёжнее pymorphy: он склоняет
# «хакатон» как одушевлённое («про хакатона») и путает род прилагательных.
TOPIC_ACC = {
    "сайту": "сайт", "интеграции": "интеграцию", "договору": "договор", "хакатону": "хакатон",
    "аналитике": "аналитику", "бюджету": "бюджет", "презентации": "презентацию",
    "новому продукту": "новый продукт", "мобильному приложению": "мобильное приложение", "crm": "CRM",
}


def _topic_acc(phrase: str) -> str:
    return TOPIC_ACC.get(phrase.lower(), phrase)


def fix_grammar(text: str) -> str:
    t = text.strip()
    m = re.fullmatch(r"(Позвонить|Отправить итоги встречи|Отправить (?:[а-яё ]+?)) ([А-ЯЁ][а-яё]+)", t)
    if m:
        return f"{m.group(1)} {_reinflect(m.group(2), 'ablt', 'datv')}"
    m = re.fullmatch(r"Напомнить ([А-ЯЁ][а-яё]+) про (.+)", t)
    if m:
        return f"Напомнить {_reinflect(m.group(1), 'ablt', 'datv')} про {_topic_acc(m.group(2))}"
    m = re.fullmatch(r"Подготовить вопросы для ([А-ЯЁ][а-яё]+)", t)
    if m:
        return f"Подготовить вопросы для {_reinflect(m.group(1), 'ablt', 'gent')}"
    m = re.fullmatch(r"Обсудить (.+?) с ([А-ЯЁ][а-яё]+)", t)
    if m:
        return f"Обсудить {_topic_acc(m.group(1))} с {m.group(2)}"
    if t == "Обсудить бюджет по бюджету":
        return "Обсудить бюджет"
    return t


def classify(text: str) -> EventType:
    low = text.lower()
    if low.startswith(MEETING_PREFIXES) or re.fullmatch(r"обсудить .+ с [а-яё]+", low):
        return EventType.MEETING
    if low.startswith("напомнить"):
        return EventType.REMINDER
    return EventType.TASK


def load_csv(path: Path, user_id: str = DEMO_USER) -> list[Event]:
    events = []
    imported_at = datetime.now().replace(microsecond=0)
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            raw = row["text"].strip()
            events.append(Event(
                id=None,
                user_id=user_id,
                type=classify(raw),
                title=fix_grammar(raw),
                date=date.fromisoformat(row["date"]),
                time=time.fromisoformat(row["time"]) if row.get("time") else None,
                created_at=imported_at,
                source_text=f"csv#{row['id']}: {raw}",
            ))
    return events


def seed_if_empty(storage: Storage, path: Path, user_id: str = DEMO_USER) -> int:
    if storage.count(user_id) > 0 or not path.exists():
        return 0
    events = load_csv(path, user_id)
    storage.add_many(events)
    return len(events)
