"""LLM-слой без сети: проверяем преобразование ответа модели и откат на правила."""
from datetime import date, time

from app.assistant import Assistant
from app.models import EventType, Intent
from app.nlu.llm import LLMParser
from app.storage import Storage
from conftest import NOW


def test_tool_output_to_command():
    cmd = LLMParser._to_command({
        "intent": "create", "event_type": "meeting", "title": "Встреча с дизайнером",
        "date": "2026-10-22", "time": "11:00", "deadline": False, "duration_min": 90,
    })
    assert cmd.intent is Intent.CREATE and cmd.event_type is EventType.MEETING
    assert (cmd.date, cmd.time, cmd.extras["duration_min"]) == (date(2026, 10, 22), time(11, 0), 90)
    assert cmd.source == "llm"


def test_invalid_tool_output_is_rejected():
    assert LLMParser._to_command({"intent": "launch_rockets"}) is None
    cmd = LLMParser._to_command({"intent": "list", "date": "не дата", "period_start": "2026-10-15"})
    assert cmd.date is None and cmd.period.start == date(2026, 10, 15)


class FakeLLM:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def parse(self, text, now, last_title=None, history=None):
        self.calls += 1
        return self.result


def test_assistant_uses_llm_result():
    llm = FakeLLM(LLMParser._to_command({"intent": "create", "event_type": "task", "title": "Сдать отчёт", "date": "2026-10-20", "time": "10:00"}))
    bot = Assistant(Storage(":memory:"), llm=llm, clock=lambda: NOW)
    r = bot.handle("u", "надо бы отчёт сдать во вторник")
    assert r.parser == "llm" and "Сдать отчёт" in r.text
    assert bot.storage.all("u")[0].date == date(2026, 10, 20)


def test_assistant_falls_back_to_rules_when_llm_fails():
    llm = FakeLLM(None)
    bot = Assistant(Storage(":memory:"), llm=llm, clock=lambda: NOW)
    r = bot.handle("u", "Напомни мне завтра в 10 утра позвонить Ивану")
    assert llm.calls == 1 and r.parser == "rules"
    assert bot.storage.all("u")[0].title == "Позвонить Ивану"


def test_short_answers_skip_llm():
    llm = FakeLLM(None)
    bot = Assistant(Storage(":memory:"), llm=llm, clock=lambda: NOW)
    bot.handle("u", "да")
    assert llm.calls == 0
