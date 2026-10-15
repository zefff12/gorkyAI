"""Сценарии диалога целиком: реплика → действие с данными → ответ."""
from datetime import date, time

from app.assistant import Assistant
from app.models import EventType, Recurrence
from app.storage import Storage
from conftest import NOW

U = "u1"


def say(bot, text, user=U):
    return bot.handle(user, text)


def test_create_reminder_and_confirm(bot):
    r = say(bot, "Напомни мне завтра в 10 утра позвонить Ивану")
    assert "напомню завтра в 10:00" in r.text and "Позвонить Ивану" in r.text
    [ev] = bot.storage.all(U)
    assert (ev.type, ev.date, ev.time) == (EventType.REMINDER, date(2026, 10, 16), time(10, 0))
    assert ev.created_at == NOW


def test_meeting_without_time_asks_clarification(bot):
    r = say(bot, "Поставь встречу с Иваном завтра")
    assert r.text == "На какое время поставить встречу завтра?" and r.awaiting == "need_time"
    assert bot.storage.all(U) == []
    r = say(bot, "в 12")
    assert "Встреча с Иваном" in r.text and "12:00" in r.text
    [ev] = bot.storage.all(U)
    assert (ev.date, ev.time) == (date(2026, 10, 16), time(12, 0))


def test_meeting_clarification_accepts_bare_number_and_title(bot):
    say(bot, "Добавь встречу")
    r = say(bot, "с бухгалтером")
    assert "Встреча с бухгалтером" in r.text and r.awaiting == "need_time"
    say(bot, "послезавтра в 16")
    [ev] = bot.storage.all(U)
    assert ev.title == "Встреча с бухгалтером" and ev.date == date(2026, 10, 17) and ev.time == time(16, 0)


def test_reminder_without_time_asks_when(bot):
    r = say(bot, "Напомни купить хлеб")
    assert r.awaiting == "need_time"
    say(bot, "в 7 вечера")
    [ev] = bot.storage.all(U)
    assert ev.time == time(19, 0) and ev.title == "Купить хлеб"


def test_time_only_in_past_moves_to_tomorrow(bot):
    say(bot, "Напомни в 9 утра сделать зарядку")
    [ev] = bot.storage.all(U)
    assert ev.date == date(2026, 10, 16)


def test_clarification_can_be_cancelled(bot):
    say(bot, "Поставь встречу с Иваном завтра")
    r = say(bot, "отмена")
    assert "отменил" in r.text.lower()
    assert bot.storage.all(U) == []


def test_new_command_during_clarification_switches_topic(bot):
    say(bot, "Поставь встречу с Иваном завтра")
    r = say(bot, "Что у меня на этой неделе?")
    assert r.intent == "list"


def test_list_today_and_week(bot):
    say(bot, "Добавь встречу с командой сегодня в 15:00")
    say(bot, "Сегодня нужно отправить отчет")
    say(bot, "в течение дня")
    say(bot, "В субботу в 12 обед с родителями")
    r = say(bot, "Что у меня сегодня?")
    assert "1 встреча и 1 задача" in r.text and "15:00" in r.text
    r = say(bot, "Есть ли у меня встречи сегодня?")
    assert len(r.events) == 1 and r.events[0].type is EventType.MEETING
    r = say(bot, "Что запланировано на эту неделю?")
    assert len(r.events) == 3
    r = say(bot, "Какие задачи у меня на завтра?")
    assert r.events == [] and "нет" in r.text


def test_conflicting_meeting_requires_confirmation(bot):
    say(bot, "Добавь встречу с командой завтра в 15:00")
    r = say(bot, "Назначь созвон с клиентом завтра в 15:30")
    assert r.awaiting == "confirm_conflict" and "Встреча с командой" in r.text
    assert len(bot.storage.all(U)) == 1
    say(bot, "да")
    assert len(bot.storage.all(U)) == 2


def test_context_reschedule_and_complete(bot):
    say(bot, "Поставь задачу подготовить отчёт на пятницу в 10")
    r = say(bot, "А перенеси её на понедельник")
    assert "понедельник" in r.text
    [ev] = bot.storage.all(U)
    assert ev.date == date(2026, 10, 19)
    say(bot, "отметь её выполненной")
    assert bot.storage.all(U)[0].done


def test_reschedule_without_new_time_asks(bot):
    say(bot, "Добавь встречу с дизайнером завтра в 11")
    r = say(bot, "перенеси встречу с дизайнером")
    assert r.awaiting == "need_new_time"
    say(bot, "на 14:00")
    assert bot.storage.all(U)[0].time == time(14, 0)


def test_delete_requires_confirmation(bot):
    say(bot, "Добавь встречу с дизайнером завтра в 11")
    r = say(bot, "Удали встречу с дизайнером")
    assert r.awaiting == "confirm_delete"
    assert len(bot.storage.all(U)) == 1
    say(bot, "да")
    assert bot.storage.all(U) == []


def test_ambiguous_target_asks_to_choose(bot):
    say(bot, "Поставь задачу отправить отчёт Ивану в пятницу в 10")
    say(bot, "Поставь задачу отправить отчёт Марии в понедельник в 10")
    r = say(bot, "Удали задачу про отчёт")
    assert r.awaiting == "choose" and "1)" in r.text and "2)" in r.text
    r = say(bot, "вторую")
    assert r.awaiting == "confirm_delete"
    say(bot, "да")
    [left] = bot.storage.all(U)
    assert "Ивану" in left.title


def test_recurring_meeting_shows_every_week(bot):
    say(bot, "Каждый понедельник в 10 планёрка с командой")
    [ev] = bot.storage.all(U)
    assert ev.recurrence is Recurrence.WEEKLY
    r = say(bot, "Что у меня в следующем месяце?")
    assert len(r.events) == 5      # понедельники ноября 2026: 2, 9, 16, 23, 30


def test_unknown_request_gives_help(bot):
    r = say(bot, "абракадабра")
    assert "Например" in r.text or "например" in r.text


def test_users_are_isolated(bot):
    say(bot, "Купить молоко", user="alice")
    say(bot, "без срока", user="alice")
    assert len(bot.storage.all("alice")) == 1
    assert bot.storage.all("bob") == []
    r = bot.handle("bob", "Найди задачу про молоко")
    assert "Не нашёл" in r.text


def test_data_persists_between_restarts(tmp_path):
    db = tmp_path / "a.db"
    Assistant(Storage(db), clock=lambda: NOW).handle(U, "Напомни завтра в 10 позвонить Ивану")
    r = Assistant(Storage(db), clock=lambda: NOW).handle(U, "Что у меня завтра?")
    assert "Позвонить Ивану" in r.text


# --- поиск по импортированному датасету -----------------------------------------

def test_search_task_about_presentation(seeded_bot):
    r = seeded_bot.handle("demo", "Найди задачу про презентацию")
    assert r.events and all("презентац" in e.title.lower() for e in r.events)
    assert all(e.type is not EventType.MEETING for e in r.events)


def test_search_last_meeting_with_ivan(seeded_bot):
    r = seeded_bot.handle("demo", "Когда у меня была встреча с Иваном?")
    assert r.text.startswith("Последний раз")
    top = r.events[0]
    assert top.type is EventType.MEETING and "Иван" in top.title and top.date <= NOW.date()
    past_ivan = [e for e in seeded_bot.storage.all("demo")
                 if e.type is EventType.MEETING and "Иван" in e.title and e.date <= NOW.date()]
    assert top.date == max(e.date for e in past_ivan)


def test_search_contract_finds_dogovor_by_synonym_and_case(seeded_bot):
    r = seeded_bot.handle("demo", "Я когда-то ставил задачу по контракту, найди её")
    assert r.events and all("договор" in e.title.lower() for e in r.events)


def test_search_tolerates_speech_recognition_typo(seeded_bot):
    r = seeded_bot.handle("demo", "найди задачу про презинтацию")
    assert r.events and "презентац" in r.events[0].title.lower()


def test_list_on_seeded_data(seeded_bot):
    r = seeded_bot.handle("demo", "Что запланировано на эту неделю?")
    expected = [e for e in seeded_bot.storage.all("demo") if date(2026, 10, 12) <= e.date <= date(2026, 10, 18)]
    assert len(r.events) == len(expected) > 0


def test_new_command_interrupts_time_clarification(bot):
    say(bot, "встреча с инвестором 3 ноября")
    r = say(bot, "поставь напоминание на завтра")
    assert r.text == "О чём напомнить?"
    say(bot, "полить цветы")
    r = say(bot, "в 8 вечера")
    reminder = [e for e in bot.storage.all(U) if e.type is EventType.REMINDER]
    assert reminder and reminder[0].title == "Полить цветы" and reminder[0].time == time(20, 0)


def test_small_talk(bot):
    assert "Чем помочь" in say(bot, "Привет").text
    assert "Пожалуйста" in say(bot, "спасибо").text


def test_question_about_topic_without_period_is_search(seeded_bot):
    r = seeded_bot.handle("demo", "Были ли встречи с Альфой?")
    assert r.intent == "search" and all("Альф" in e.title for e in r.events)


def test_search_reports_true_total(seeded_bot):
    r = seeded_bot.handle("demo", "найди всё про хакатон")
    total = sum("хакатон" in e.title.lower() for e in seeded_bot.storage.all("demo"))
    assert f"Нашёл {total} " in r.text


def test_task_without_date_asks_when_and_what_time(bot):
    r = say(bot, "рулить хотеть")
    assert r.awaiting == "need_time" and "Когда и во сколько" in r.text
    assert bot.storage.all(U) == []
    say(bot, "завтра в 12")
    [ev] = bot.storage.all(U)
    assert (ev.title, ev.date, ev.time) == ("Рулить хотеть", date(2026, 10, 16), time(12, 0))


def test_task_with_date_asks_only_time(bot):
    r = say(bot, "рулить хотеть завтра")
    assert r.text.startswith("Во сколько завтра")
    say(bot, "на 12")
    assert bot.storage.all(U)[0].time == time(12, 0)


def test_task_date_only_answer_asks_time_again(bot):
    say(bot, "купить подарок")
    r = say(bot, "в субботу")
    assert r.awaiting == "need_time" and r.text.startswith("Во сколько")
    say(bot, "в течение дня")
    [ev] = bot.storage.all(U)
    assert ev.date == date(2026, 10, 17) and ev.time is None


def test_task_can_be_saved_without_deadline(bot):
    say(bot, "купить молоко")
    r = say(bot, "без срока")
    assert "без срока" in r.text
    [ev] = bot.storage.all(U)
    assert ev.date is None and ev.time is None


def test_deadline_task_is_saved_without_asking(bot):
    r = say(bot, "Поставь задачу купить билеты до конца недели")
    assert r.awaiting is None and "срок" in r.text
    assert bot.storage.all(U)[0].date == date(2026, 10, 18)
