"""Исправления по замечаниям проверяющего.

1. Память и контекст: контекст переживает перезапуск, история упоминаний накапливается,
   короткие реплики («а на завтра?») продолжают прошлый запрос.
2. Ручные формулировки и опечатки работают так же, как заготовленные фразы.
3. Задачи без даты попадают в списки; «ближайшие N дней» считается по фразе.
4. Удаление одной записи; резервная копия; дубликаты.
"""
from datetime import date, datetime, time

import pytest
from fastapi.testclient import TestClient

from app.assistant import Assistant
from app.main import app
from app.models import EventType
from app.storage import Storage
from conftest import NOW

U = "rev"


def say(bot, text, user=U):
    return bot.handle(user, text)


# ---------------------------------------------------------------- 1. память

def test_context_survives_restart(tmp_path):
    db = tmp_path / "m.db"
    Assistant(Storage(db), clock=lambda: NOW).handle(U, "Добавь встречу с Иваном завтра в 12")
    bot = Assistant(Storage(db), clock=lambda: NOW)         # «перезапуск сервера»
    r = bot.handle(U, "А перенеси её на понедельник")
    assert r.intent == "reschedule" and "понедельник" in r.text
    assert bot.storage.all(U)[0].date == date(2026, 10, 19)


def test_unfinished_clarification_survives_restart(tmp_path):
    db = tmp_path / "m.db"
    r = Assistant(Storage(db), clock=lambda: NOW).handle(U, "Поставь встречу с Иваном завтра")
    assert r.awaiting == "need_time"
    bot = Assistant(Storage(db), clock=lambda: NOW)
    r = bot.handle(U, "в 12")
    assert "Встреча с Иваном" in r.text and bot.storage.all(U)[0].time == time(12, 0)


def test_chat_history_is_stored():
    bot = Assistant(Storage(":memory:"), clock=lambda: NOW)
    say(bot, "Напомни завтра в 10 позвонить Ивану")
    history = bot.storage.messages(U)
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert "Позвонить Ивану" in history[1]["text"]


def test_mention_history_accumulates(bot):
    """Запись, упомянутая несколько ответов назад, всё ещё в контексте (раньше last_ids перезаписывался)."""
    say(bot, "Добавь встречу с Иваном завтра в 12")
    say(bot, "Добавь встречу с Иваном по бюджету в пятницу в 16")
    say(bot, "Напомни купить хлеб в 19:00")
    say(bot, "Что у меня на следующей неделе?")        # фокус сменился, история — нет
    r = say(bot, "Перенеси встречу с Иваном по бюджету на 18:00")
    assert r.awaiting is None and "по бюджету" in r.text
    s = bot.session(U)
    assert len(s.history) >= 3


def test_pronoun_falls_back_to_history_when_focus_empty(bot):
    say(bot, "Добавь встречу с дизайнером завтра в 11")
    say(bot, "Найди задачу про несуществующее")         # пустой ответ очищает фокус
    r = say(bot, "Перенеси её на 13:00")
    assert r.intent == "reschedule" and bot.storage.all(U)[0].time == time(13, 0)


@pytest.mark.parametrize("first, follow, check", [
    ("Что у меня сегодня?", "а на завтра?", lambda r: r.text.startswith("Завтра")),
    ("Что у меня сегодня?", "а завтра", lambda r: r.text.startswith("Завтра")),
    ("Какие встречи на этой неделе?", "а на следующей?", None),
    ("Какие встречи на этой неделе?", "а задачи?", lambda r: "встреча" not in r.text.split(":", 1)[-1]),
])
def test_short_follow_ups_continue_previous_request(bot, first, follow, check):
    say(bot, "Добавь встречу с командой завтра в 15:00")
    say(bot, "Поставь задачу отправить отчёт сегодня в 18")
    say(bot, first)
    r = say(bot, follow)
    assert r.intent == "list", r.text
    if check:
        assert check(r), r.text


def test_follow_up_search_with_other_person(seeded_bot):
    seeded_bot.handle("demo", "Когда у меня была встреча с Иваном?")
    r = seeded_bot.handle("demo", "а с Марией?")
    assert r.intent == "search" and r.events and all("Мари" in e.title for e in r.events)


def test_follow_up_type_keeps_period(bot):
    say(bot, "Добавь встречу с командой завтра в 15:00")
    say(bot, "Поставь задачу отправить отчёт завтра в 18")
    say(bot, "Какие встречи завтра?")
    r = say(bot, "а задачи?")
    assert r.text.startswith("Завтра") and len(r.events) == 1 and r.events[0].type is EventType.TASK


# ------------------------------------- 2. ручные формулировки = заготовленные фразы

CHIP_VARIANTS = {
    "Что у меня на этой неделе?": [
        "что у меня на этой неделе", "Что у меня на этой недели?", "что у меняй на этой неделе?",
        "какие планы на эту неделю", "расписание на неделю", "что запланировано на этой неделе",
        "покажи всё на этой неделе", "чем я занят на этой неделе",
    ],
}


def _seed_week(bot):
    say(bot, "Добавь встречу с командой завтра в 15:00")
    say(bot, "Напомни послезавтра в 10 позвонить Ивану")


@pytest.mark.parametrize("text", CHIP_VARIANTS["Что у меня на этой неделе?"])
def test_week_list_variants(bot, text):
    _seed_week(bot)
    r = say(bot, text)
    assert r.intent == "list" and len(r.events) == 2, (text, r.text)


@pytest.mark.parametrize("text", [
    "что у меня завтра?", "что у меняй завтра?", "Что у меня завтра", "что завтра", "какие планы на завтра",
    "что у меня на завтро", "покажи завтра", "а что завтра?", "есть что-нибудь завтра?",
])
def test_tomorrow_variants(bot, text):
    _seed_week(bot)
    r = say(bot, text)
    assert r.intent == "list" and len(r.events) == 1 and r.events[0].title == "Встреча с командой", (text, r.text)


@pytest.mark.parametrize("text", [
    "Напомни завтра в 10 утра позвонить Ивану", "напомни завтра в 10 позвонить ивану",
    "напомин завтра в 10 утра позвонить Ивану", "завтра в 10 утра напомни позвонить Ивану",
    "напомни мне позвонить ивану завтра в 10:00", "Напомните завтра в десять утра позвонить Ивану",
])
def test_reminder_variants(bot, text):
    r = say(bot, text)
    [ev] = bot.storage.all(U)
    assert (ev.type, ev.date, ev.time) == (EventType.REMINDER, date(2026, 10, 16), time(10, 0)), (text, r.text)
    assert ev.title.lower() == "позвонить ивану"


@pytest.mark.parametrize("text", [
    "Добавь встречу с командой в пятницу в 15:00", "добавь встречу с командой в пятницу в 15",
    "добав встречу с командой в пятницу в 15:00", "в пятницу в 15 встреча с командой",
    "запланируй встречу с командой на пятницу в 15:00", "добавь встрчу с командой в пятницу в 15:00",
])
def test_meeting_variants(bot, text):
    r = say(bot, text)
    [ev] = bot.storage.all(U)
    assert (ev.type, ev.date, ev.time) == (EventType.MEETING, date(2026, 10, 16), time(15, 0)), (text, r.text)
    assert ev.title.lower() == "встреча с командой"


@pytest.mark.parametrize("text", [
    "Поставь встречу с Иваном завтра", "поставь встречу с иваном на завтра", "встреча с иваном завтра",
    "поставь встрчу с Иваном завтра", "Назначь встречу с Иваном завтра",
])
def test_meeting_without_time_variants(bot, text):
    r = say(bot, text)
    assert r.awaiting == "need_time", (text, r.text)
    say(bot, "в 12")
    [ev] = bot.storage.all(U)
    assert ev.time == time(12, 0) and ev.date == date(2026, 10, 16)


@pytest.mark.parametrize("text", [
    "Найди задачу про презентацию", "найди задачу про презентации", "найди задачи о презентации",
    "поищи презентацию", "найди задачу про презинтацию", "найди про презентацию",
])
def test_search_presentation_variants(seeded_bot, text):
    r = seeded_bot.handle("demo", text)
    assert r.intent == "search" and r.events, (text, r.text)
    assert all("презентац" in e.title.lower() for e in r.events)


@pytest.mark.parametrize("text", [
    "Когда у меня была встреча с Иваном?", "когда была встреча с иваном", "когда я встречался с Иваном",
    "когда последний раз была встреча с Иваном", "когда у меня был созвон с Иваном?",
])
def test_search_ivan_variants(seeded_bot, text):
    r = seeded_bot.handle("demo", text)
    assert r.intent == "search" and r.text.startswith("Последний раз"), (text, r.text)
    assert "Иван" in r.events[0].title


@pytest.mark.parametrize("text", [
    "А перенеси её на понедельник", "перенеси ее на понедельник", "перенеси на понедельник",
    "сдвинь её на понедельник", "а перенеси её на понедельник пожалуйста",
])
def test_reschedule_variants(bot, text):
    say(bot, "Добавь встречу с Иваном завтра в 12")
    r = say(bot, text)
    assert r.intent == "reschedule" and bot.storage.all(U)[0].date == date(2026, 10, 19), (text, r.text)


@pytest.mark.parametrize("text", [
    "Переименуй её в Созвон с Иваном", "переименуй ее в созвон с иваном",
    "измени название на Созвон с Иваном", "поменяй название на созвон с Иваном",
])
def test_rename_variants(bot, text):
    say(bot, "Добавь встречу с Иваном завтра в 12")
    say(bot, text)
    assert bot.storage.all(U)[0].title.lower() == "созвон с иваном", text


@pytest.mark.parametrize("text", [
    "Каждый понедельник в 10 планёрка", "каждый понедельник в 10 планерка",
    "по понедельникам в 10 планёрка", "планёрка каждый понедельник в 10",
])
def test_recurring_variants(bot, text):
    say(bot, text)
    [ev] = bot.storage.all(U)
    assert ev.recurrence is not None and ev.time == time(10, 0) and ev.date.weekday() == 0, text


@pytest.mark.parametrize("text", ["Найди слайды для выступления", "найди слайды", "поищи слайды для доклада"])
def test_slides_variants(seeded_bot, text):
    r = seeded_bot.handle("demo", text)
    assert r.events and any("презентац" in e.title.lower() for e in r.events), (text, r.text)


@pytest.mark.parametrize("text, title, recurring", [
    ("Йога каждый понедельник в 8 утра", "Йога", True),
    ("День рождения Маши в субботу в 19", "День рождения Маши", False),
    ("Стоматолог завтра в 9:30", "Стоматолог", False),
])
def test_statement_with_date_creates_record(bot, text, title, recurring):
    r = say(bot, text)
    [ev] = bot.storage.all(U)
    assert ev.title == title and (ev.recurrence is not None) == recurring and ev.time is not None, r.text


def test_follow_up_with_date_is_not_a_new_record(bot):
    say(bot, "Что у меня сегодня?")
    r = say(bot, "а завтра")
    assert r.intent == "list" and bot.storage.all(U) == []


def test_typo_in_query_does_not_filter_out_results(bot):
    """Скриншот проверяющего: «что у меняй завтра?» отвечало «ничего не запланировано»."""
    _seed_week(bot)
    assert "ничего" in say(bot, "что у меня в воскресенье?").text      # контроль: пустой день
    r = say(bot, "что у меняй завтра?")
    assert len(r.events) == 1


# ------------------------------------------- 3. задачи без даты, «ближайшие N дней»

def test_undated_tasks_are_listed(bot):
    say(bot, "Купить молоко")
    say(bot, "без срока")
    r = say(bot, "Что у меня сегодня?")
    assert "Без срока" in r.text and "Купить молоко" in r.text
    r = say(bot, "Что запланировано на эту неделю?")
    assert "Купить молоко" in r.text
    r = say(bot, "Что у меня на следующей неделе?")
    assert "Купить молоко" not in r.text                          # будущий период без «сегодня»
    r = say(bot, "Какие встречи сегодня?")
    assert "Купить молоко" not in r.text                          # фильтр по встречам


def test_done_undated_tasks_are_hidden(bot):
    say(bot, "Купить молоко")
    say(bot, "без срока")
    say(bot, "отметь её выполненной")
    assert "Купить молоко" not in say(bot, "Что у меня сегодня?").text


def test_reviewer_scenario_task_on_monday_asked_on_wednesday(tmp_path):
    """Создали в понедельник задачу на следующую неделю, спросили в среду — задача находится."""
    db = tmp_path / "w.db"
    monday = datetime(2026, 10, 12, 9, 0)
    wednesday = datetime(2026, 10, 14, 9, 0)
    Assistant(Storage(db), clock=lambda: monday).handle(U, "Поставь задачу купить подарок в следующую среду в 10")
    bot = Assistant(Storage(db), clock=lambda: wednesday)
    r = bot.handle(U, "Какие задачи у меня на следующей неделе?")
    assert [e.title for e in r.events] == ["Купить подарок"] and r.events[0].date == date(2026, 10, 21)
    # 21-е — восьмой день, считая сегодняшнюю среду 14-го
    assert bot.handle(U, "Что у меня в ближайшие 7 дней?").events == []
    r = bot.handle(U, "Что у меня в ближайшие 8 дней?")
    assert r.events and r.events[0].date == date(2026, 10, 21)
    r = bot.handle(U, "Что у меня в ближайшие две недели?")
    assert r.events and r.events[0].date == date(2026, 10, 21)


@pytest.mark.parametrize("text, days", [
    ("в ближайшие дни", 7), ("на ближайшие 3 дня", 3), ("ближайшую неделю", 7),
    ("в ближайшие две недели", 14), ("в ближайший месяц", 30), ("следующие 10 дней", 10),
])
def test_upcoming_span(text, days):
    from app.nlu.dates import extract
    from app.nlu.text import tokenize
    info = extract(tokenize(text), NOW)
    assert (info.period.end - info.period.start).days + 1 == days, text


# ----------------------------------------------- 4. удаление по одной, копия, дубли

client = TestClient(app)


def test_delete_single_event_via_api():
    h = {"X-User-Id": "single_del"}
    client.post("/api/message", json={"text": "Добавь встречу с Иваном завтра в 12"}, headers=h)
    client.post("/api/message", json={"text": "Добавь встречу с Марией завтра в 15"}, headers=h)
    events = client.get("/api/events", headers=h).json()
    ivan = next(e for e in events if "Иван" in e["title"])
    assert client.delete(f"/api/events/{ivan['id']}", headers=h).json() == {"ok": True}
    left = client.get("/api/events", headers=h).json()
    assert [e["title"] for e in left] == ["Встреча с Марией"]
    assert client.delete(f"/api/events/{ivan['id']}", headers=h).status_code == 404
    # удалённая запись пропала и из контекста: «её» теперь не на что сослаться
    r = client.post("/api/message", json={"text": "перенеси её на пятницу"}, headers=h).json()
    assert "Встреча с Иваном" not in r["text"]


def test_other_user_cannot_delete():
    h = {"X-User-Id": "owner1"}
    client.post("/api/message", json={"text": "Добавь встречу с Иваном завтра в 12"}, headers=h)
    eid = client.get("/api/events", headers=h).json()[0]["id"]
    assert client.delete(f"/api/events/{eid}", headers={"X-User-Id": "intruder"}).status_code == 404


def test_mark_done_via_api():
    h = {"X-User-Id": "done_api"}
    client.post("/api/message", json={"text": "Напомни завтра в 10 позвонить Ивану"}, headers=h)
    eid = client.get("/api/events", headers=h).json()[0]["id"]
    assert client.patch(f"/api/events/{eid}", json={"done": True}, headers=h).json()["done"] is True


def test_history_endpoint():
    h = {"X-User-Id": "hist_api"}
    client.post("/api/message", json={"text": "Привет"}, headers=h)
    hist = client.get("/api/history", headers=h).json()
    assert [m["role"] for m in hist] == ["user", "assistant"]


def test_backup_restores_data_and_context():
    h = {"X-User-Id": "backup_src"}
    client.post("/api/message", json={"text": "Добавь встречу с Иваном завтра в 12"}, headers=h)
    backup = client.get("/api/export", headers=h).json()
    client.post("/api/reset", headers=h)                       # «Render перезапустился, база пуста»
    assert client.get("/api/stats", headers=h).json() == {"total": 0}
    restored = client.post("/api/import", json=backup, headers=h).json()
    assert restored == {"events": 1, "messages": 2}
    assert client.post("/api/import", json=backup, headers=h).status_code == 409   # без дублей
    r = client.post("/api/message", json={"text": "перенеси её на понедельник"}, headers=h).json()
    assert r["intent"] == "reschedule" and "Встреча с Иваном" in r["text"]          # контекст тоже вернулся


def test_duplicate_requires_confirmation(bot):
    say(bot, "Напомни завтра в 10 утра позвонить Ивану")
    r = say(bot, "Напомни завтра в 10 утра позвонить Ивану")
    assert r.awaiting == "confirm_duplicate" and len(bot.storage.all(U)) == 1
    say(bot, "нет")
    assert len(bot.storage.all(U)) == 1
    say(bot, "Напомни завтра в 10 утра позвонить Ивану")
    say(bot, "да")
    assert len(bot.storage.all(U)) == 2
