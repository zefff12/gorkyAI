from datetime import date, time

import pytest

from app.models import Recurrence
from app.nlu.dates import extract
from app.nlu.text import join_tokens, tokenize
from conftest import NOW  # четверг 15.10.2026 11:20


def run(text, **kw):
    tokens = tokenize(text)
    info = extract(tokens, NOW, **kw)
    return info, join_tokens(tokens)


@pytest.mark.parametrize("text, d, t", [
    ("сегодня", date(2026, 10, 15), None),
    ("завтра в 10 утра", date(2026, 10, 16), time(10, 0)),
    ("послезавтра в 15:30", date(2026, 10, 17), time(15, 30)),
    ("в пятницу", date(2026, 10, 16), None),
    ("в понедельник в 9", date(2026, 10, 19), time(9, 0)),
    ("в следующую пятницу в 3 дня", date(2026, 10, 23), time(15, 0)),
    ("в 7 вечера", None, time(19, 0)),
    ("в 11", None, time(11, 0)),
    ("в 3", None, time(15, 0)),
    ("в полдень", None, time(12, 0)),
    ("в час дня", None, time(13, 0)),
    ("через два часа", date(2026, 10, 15), time(13, 20)),
    ("через полчаса", date(2026, 10, 15), time(11, 50)),
    ("через неделю", date(2026, 10, 22), None),
    ("через 3 дня", date(2026, 10, 18), None),
    ("20 октября в 10:00", date(2026, 10, 20), time(10, 0)),
    ("15.11 в 7 вечера", date(2026, 11, 15), time(19, 0)),
    ("двадцать третьего ноября", date(2026, 11, 23), None),
    ("двадцатого числа", date(2026, 10, 20), None),
    ("десятого числа", date(2026, 11, 10), None),
    ("третьего ноября в десять тридцать", date(2026, 11, 3), time(10, 30)),
])
def test_date_and_time(text, d, t):
    info, _ = run(text, future_bias=True)
    assert info.date == d
    assert info.time == t


def test_deadline_end_of_week():
    info, rest = run("купить билеты до конца недели")
    assert info.date == date(2026, 10, 18) and info.deadline
    assert rest == "купить билеты"


def test_deadline_weekday():
    info, rest = run("подготовить презентацию к пятнице")
    assert info.date == date(2026, 10, 16) and info.deadline
    assert rest == "подготовить презентацию"


@pytest.mark.parametrize("text, start, end", [
    ("на этой неделе", date(2026, 10, 12), date(2026, 10, 18)),
    ("на эту неделю", date(2026, 10, 12), date(2026, 10, 18)),
    ("на следующей неделе", date(2026, 10, 19), date(2026, 10, 25)),
    ("на прошлой неделе", date(2026, 10, 5), date(2026, 10, 11)),
    ("в этом месяце", date(2026, 10, 1), date(2026, 10, 31)),
    ("на выходных", date(2026, 10, 17), date(2026, 10, 18)),
])
def test_periods(text, start, end):
    info, _ = run(text)
    assert (info.period.start, info.period.end) == (start, end)


def test_time_range_gives_duration():
    info, _ = run("с 14 до 16")
    assert info.time == time(14, 0) and info.duration_min == 120


@pytest.mark.parametrize("text, rec", [
    ("каждый день в 9", Recurrence.DAILY),
    ("по понедельникам", Recurrence.WEEKLY),
    ("каждую пятницу", Recurrence.WEEKLY),
    ("по будням", Recurrence.WEEKDAYS),
])
def test_recurrence(text, rec):
    info, _ = run(text)
    assert info.recurrence is rec


def test_past_weekday_when_asking_about_past():
    info, _ = run("в понедельник", prefer_past=True)
    assert info.date == date(2026, 10, 12)


def test_date_tokens_are_removed_from_title():
    _, rest = run("Напомни мне завтра в 10 утра позвонить Ивану")
    assert rest == "Напомни мне позвонить Ивану"
