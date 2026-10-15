"""Понимание запросов rule-based парсером: все формулировки из ТЗ + перефразировки."""
from datetime import date, time

import pytest

from app.models import EventType, Intent
from app.nlu.rules import parse
from conftest import NOW


@pytest.mark.parametrize("text, etype, title, d, t", [
    # примеры из задания
    ("Напомни мне завтра в 10 утра позвонить Ивану", EventType.REMINDER, "Позвонить Ивану", date(2026, 10, 16), time(10, 0)),
    ("В пятницу нужно отправить отчет", EventType.TASK, "Отправить отчет", date(2026, 10, 16), None),
    ("Поставь задачу купить билеты до конца недели", EventType.TASK, "Купить билеты", date(2026, 10, 18), None),
    ("Добавь встречу с командой завтра в 15:00", EventType.MEETING, "Встреча с командой", date(2026, 10, 16), time(15, 0)),
    ("В четверг в 11 у меня встреча с дизайнером", EventType.MEETING, "Встреча с дизайнером", date(2026, 10, 22), time(11, 0)),
    # перефразировки
    ("Не забыть купить молоко", EventType.TASK, "Купить молоко", None, None),
    ("Купить подарок маме", EventType.TASK, "Купить подарок маме", None, None),
    ("через два часа позвонить в банк", EventType.TASK, "Позвонить в банк", date(2026, 10, 15), time(13, 20)),
    ("Запланируй созвон с Альфой во вторник в 12", EventType.MEETING, "Созвон с Альфой", date(2026, 10, 20), time(12, 0)),
    ("Мне нужно подготовить презентацию к пятнице", EventType.TASK, "Подготовить презентацию", date(2026, 10, 16), None),
    ("Создай напоминание выпить таблетки в 9 вечера", EventType.REMINDER, "Выпить таблетки", None, time(21, 0)),
    ("Назначь встречу с клиентом послезавтра с 14 до 16", EventType.MEETING, "Встреча с клиентом", date(2026, 10, 17), time(14, 0)),
])
def test_create(text, etype, title, d, t):
    cmd = parse(text, NOW)
    assert cmd.intent is Intent.CREATE
    assert cmd.event_type is etype
    assert cmd.title == title
    assert cmd.date == d
    assert cmd.time == t


@pytest.mark.parametrize("text", [
    "Что у меня сегодня?",
    "Какие планы на сегодня?",
    "Покажи мои встречи на сегодня",
    "Есть ли у меня встречи сегодня?",
    "Что у меня на сегодня",
    "Расскажи, что сегодня запланировано",
])
def test_same_action_for_paraphrases(text):
    cmd = parse(text, NOW)
    assert cmd.intent is Intent.LIST
    assert cmd.period.start == cmd.period.end == date(2026, 10, 15)


@pytest.mark.parametrize("text, etype, start, end", [
    ("Какие задачи у меня на завтра?", EventType.TASK, date(2026, 10, 16), date(2026, 10, 16)),
    ("Что запланировано на эту неделю?", None, date(2026, 10, 12), date(2026, 10, 18)),
    ("Покажи встречи на следующей неделе", EventType.MEETING, date(2026, 10, 19), date(2026, 10, 25)),
    ("Что у меня послезавтра", None, date(2026, 10, 17), date(2026, 10, 17)),
])
def test_list_period_and_type(text, etype, start, end):
    cmd = parse(text, NOW)
    assert cmd.intent is Intent.LIST
    assert cmd.event_type is etype
    assert (cmd.period.start, cmd.period.end) == (start, end)


@pytest.mark.parametrize("text, query_word, etype, tense", [
    ("Найди задачу про презентацию", "презентацию", EventType.TASK, None),
    ("Когда у меня была встреча с Иваном?", "Иваном", EventType.MEETING, "past"),
    ("Я когда-то ставил задачу по договору, найди ее", "договору", EventType.TASK, "past"),
    ("Поищи что-нибудь про бюджет", "бюджет", None, None),
])
def test_search(text, query_word, etype, tense):
    cmd = parse(text, NOW)
    assert cmd.intent is Intent.SEARCH
    assert query_word in cmd.query
    assert cmd.event_type is etype
    assert cmd.tense == tense


@pytest.mark.parametrize("text, intent", [
    ("А перенеси ее на завтра", Intent.RESCHEDULE),
    ("Сдвинь встречу с командой на 17:00", Intent.RESCHEDULE),
    ("Удали задачу про презентацию", Intent.DELETE),
    ("Убери встречу с дизайнером", Intent.DELETE),
    ("Отметь её выполненной", Intent.COMPLETE),
    ("Я сделал задачу про отчёт", Intent.COMPLETE),
    ("Да", Intent.CONFIRM),
    ("да, всё равно добавь", Intent.CONFIRM),
    ("Нет", Intent.REJECT),
    ("Что ты умеешь?", Intent.HELP),
])
def test_other_intents(text, intent):
    assert parse(text, NOW).intent is intent


def test_reschedule_by_pronoun_uses_context():
    cmd = parse("А перенеси ее на завтра", NOW)
    assert cmd.refers_to_last and cmd.date == date(2026, 10, 16)
