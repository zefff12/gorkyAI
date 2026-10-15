"""Дополнительные возможности: переименование, семантический поиск, конфликты."""
import os
from datetime import date, time

import pytest

from app.assistant import Assistant
from app.models import Event, EventType, Intent
from app.nlu.rules import parse
from app.search import search
from app.storage import Storage
from conftest import NOW

U = "u1"


def say(bot, text):
    return bot.handle(U, text)


# --- изменение названия и времени голосом ---------------------------------------

@pytest.mark.parametrize("text, title, query, last", [
    ("Переименуй её в Позвонить Петру", "Позвонить Петру", None, True),
    ("Измени название встречи с Иваном на Созвон с Иваном", "Созвон с Иваном", "встречи Иваном", False),
    ("Поменяй текст на «Купить хлеб»", "Купить хлеб", None, True),
    ("Переименуй задачу про отчёт", None, "отчёт", False),
])
def test_parse_rename(text, title, query, last):
    cmd = parse(text, NOW)
    assert cmd.intent is Intent.RENAME
    assert cmd.title == title and cmd.query == query and cmd.refers_to_last is last


def test_rename_by_context(bot):
    say(bot, "Добавь встречу с Иваном завтра в 12")
    r = say(bot, "Переименуй её в Созвон с Иваном")
    assert "→ «Созвон с Иваном»" in r.text
    assert bot.storage.all(U)[0].title == "Созвон с Иваном"


def test_rename_asks_new_title(bot):
    say(bot, "Поставь задачу отправить отчёт в пятницу в 10")
    r = say(bot, "Переименуй задачу про отчёт")
    assert r.awaiting == "need_new_title"
    say(bot, "Отправить квартальный отчёт")
    assert bot.storage.all(U)[0].title == "Отправить квартальный отчёт"


def test_change_time_by_voice(bot):
    say(bot, "Добавь встречу с Иваном завтра в 12")
    say(bot, "Измени время встречи с Иваном на 15:30")
    assert bot.storage.all(U)[0].time == time(15, 30)


# --- семантический поиск --------------------------------------------------------

class FakeEmbedder:
    """Подставные «смыслы»: близость задаётся словарём, чтобы тест не зависел от модели."""
    ready = True

    def __init__(self, close):
        self.close = close

    def similarities(self, query, texts):
        return [self.close.get((query, t), 0.1) for t in texts]


def _docs():
    return [
        Event(1, U, EventType.TASK, "Подготовить презентацию", date(2026, 10, 20)),
        Event(2, U, EventType.TASK, "Проверить договор", date(2026, 10, 21)),
        Event(3, U, EventType.TASK, "Купить билеты", date(2026, 10, 22)),
    ]


def test_semantic_finds_without_shared_words():
    emb = FakeEmbedder({("выступление перед инвесторами", "Подготовить презентацию"): 0.72})
    hits = search(_docs(), "выступление перед инвесторами", NOW.date(), embedder=emb)
    assert [h.event.id for h in hits] == [1]
    assert search(_docs(), "выступление перед инвесторами", NOW.date()) == []      # без семантики — пусто


def test_semantic_never_outranks_exact_match():
    emb = FakeEmbedder({("договор", "Купить билеты"): 0.95})
    hits = search(_docs(), "договор", NOW.date(), embedder=emb)
    assert hits[0].event.id == 2


def test_assistant_uses_embedder_in_search():
    st = Storage(":memory:")
    for e in _docs():
        st.add(Event(None, U, e.type, e.title, e.date))
    emb = FakeEmbedder({("слова для выступления", "Подготовить презентацию"): 0.8})
    bot = Assistant(st, clock=lambda: NOW, embedder=emb)
    r = bot.handle(U, "Найди слова для выступления")
    assert r.events and r.events[0].title == "Подготовить презентацию"


MODEL_CACHE = os.getenv("MODEL_CACHE")


@pytest.mark.skipif(not MODEL_CACHE, reason="задайте MODEL_CACHE с загруженной моделью, чтобы проверить настоящие эмбеддинги")
def test_real_embeddings():
    from app.semantic import Embedder
    emb = Embedder(cache_dir=MODEL_CACHE)
    emb.load()
    assert emb.ready
    sims = emb.similarities("слайды для выступления", ["Подготовить презентацию", "Купить билеты"])
    assert sims[0] > 0.6 > sims[1]


def test_change_time_of_last_record_by_context(bot):
    say(bot, "Добавь встречу с Иваном завтра в 12")
    say(bot, "Измени время на 14:30")
    assert bot.storage.all(U)[0].time == time(14, 30)


def test_recently_discussed_record_wins_over_other_matches(seeded_bot):
    seeded_bot.handle("demo", "Добавь встречу с Иваном завтра в 12")
    seeded_bot.handle("demo", "Переименуй её в Созвон с Иваном по CRM")
    r = seeded_bot.handle("demo", "Отметь созвон с Иваном выполненным")
    assert r.intent == "complete" and r.awaiting is None and "Созвон с Иваном по CRM" in r.text


def test_conflict_answer_with_new_time_moves_the_new_meeting(bot):
    say(bot, "Добавь встречу с командой завтра в 12")
    r = say(bot, "Добавь встречу с Иваном завтра в 12")
    assert r.awaiting == "confirm_conflict"
    r = say(bot, "Измени время на 14:30")
    assert "Встреча с Иваном" in r.text and "14:30" in r.text
    times = sorted(e.time for e in bot.storage.all(U))
    assert times == [time(12, 0), time(14, 30)]
    say(bot, "Переименуй её в Созвон с Иваном")
    assert {e.title for e in bot.storage.all(U)} == {"Встреча с командой", "Созвон с Иваном"}
