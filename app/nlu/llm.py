"""LLM-парсер (Claude). Включается, если задан ANTHROPIC_API_KEY.

LLM только понимает запрос и возвращает структуру ParsedCommand через
принудительный tool call. Действия с данными выполняет детерминированная
бизнес-логика — LLM ничего не пишет в базу сама.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

from .. import config
from ..models import EventType, Intent, ParsedCommand, Period, Recurrence

log = logging.getLogger(__name__)

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

TOOL = {
    "name": "parse_command",
    "description": "Структурированный разбор запроса пользователя к ассистенту задач и встреч.",
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": [i.value for i in Intent],
                       "description": "create — создать задачу/напоминание/встречу; list — что запланировано на период; "
                                      "search — найти конкретную старую запись; reschedule — перенести; complete — отметить "
                                      "выполненной; delete — удалить; rename — изменить название записи (новое название в title, "
                                      "старая запись — в query или refers_to_last); confirm/reject — ответ да/нет; help; unknown"},
            "event_type": {"type": ["string", "null"], "enum": ["task", "meeting", "reminder", None],
                           "description": "Для create — тип новой записи. Для list/search — фильтр по типу, если пользователь его назвал."},
            "title": {"type": ["string", "null"], "description": "Для create: краткое название с заглавной буквы, без даты и "
                      "служебных слов. «напомни завтра позвонить Ивану» → «Позвонить Ивану»; «встречу с командой» → «Встреча с командой»."},
            "date": {"type": ["string", "null"], "description": "YYYY-MM-DD — дата записи (create) или новая дата (reschedule)."},
            "time": {"type": ["string", "null"], "description": "HH:MM, 24-часовой формат. null, если время не названо."},
            "deadline": {"type": "boolean", "description": "true, если дата — срок («до пятницы», «до конца недели»)."},
            "duration_min": {"type": ["integer", "null"]},
            "recurrence": {"type": ["string", "null"], "enum": ["daily", "weekly", "weekdays", None]},
            "period_start": {"type": ["string", "null"], "description": "YYYY-MM-DD начала периода для list/search."},
            "period_end": {"type": ["string", "null"], "description": "YYYY-MM-DD конца периода (включительно)."},
            "period_label": {"type": ["string", "null"], "description": "Как назвать период в ответе: «сегодня», «на этой неделе»."},
            "query": {"type": ["string", "null"], "description": "Для search/reschedule/complete/delete: ключевые слова записи "
                      "в начальной форме, без служебных слов («Иван», «презентация», «договор»)."},
            "tense": {"type": ["string", "null"], "enum": ["past", "future", None],
                      "description": "past — пользователь спрашивает о прошедшем («когда была»), future — о будущем."},
            "refers_to_last": {"type": "boolean", "description": "true, если пользователь ссылается на запись из прошлой реплики («её», «эту встречу»)."},
        },
        "required": ["intent"],
    },
}


def _calendar(now: datetime) -> str:
    lines = []
    for k in range(-7, 15):
        d = now.date() + timedelta(days=k)
        mark = " ← сегодня" if k == 0 else ""
        lines.append(f"{d.isoformat()} {WEEKDAYS[d.weekday()]}{mark}")
    return "\n".join(lines)


def _history_block(history: list[dict] | None) -> str:
    """Последние реплики диалога — чтобы модель понимала «её», «а на завтра?» и т.п."""
    if not history:
        return ""
    lines = [f"{'Пользователь' if m['role'] == 'user' else 'Ассистент'}: {m['text'][:300]}" for m in history]
    return "\nНедавний диалог (контекст, не выполняй его повторно):\n" + "\n".join(lines)


def _system(now: datetime, last_title: str | None, history: list[dict] | None = None) -> str:
    ctx = f"Последняя запись, о которой шла речь: «{last_title}»." if last_title else "Контекста предыдущей записи нет."
    ctx += _history_block(history)
    return (
        "Ты — модуль понимания речи голосового ассистента задач и встреч. Пользователь говорит по-русски, "
        "текст получен распознаванием речи и может содержать ошибки. Разбери запрос и вызови parse_command.\n"
        f"Сейчас {now:%Y-%m-%d %H:%M}, {WEEKDAYS[now.weekday()]}. Неделя начинается с понедельника.\n"
        f"Календарь:\n{_calendar(now)}\n"
        "Правила: «в пятницу» — ближайшая пятница (сегодня, если сегодня пятница); «в следующую пятницу» — пятница "
        "следующей недели; «через 2 часа» — от текущего времени; «в 3» без уточнения — 15:00; «утром» — 09:00, "
        "«вечером» — 19:00. Не выдумывай время и дату, если их нет. Вопросы «что у меня…», «какие планы…», «есть ли "
        "встречи…» — list. «Найди…», «когда была…» — search. Перенос записи — reschedule, новая дата/время в date/time.\n"
        + ctx
    )


def _d(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _t(s: str | None) -> time | None:
    try:
        return time.fromisoformat(s) if s else None
    except ValueError:
        return None


class LLMParser:
    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, timeout=config.LLM_TIMEOUT_S, max_retries=1)

    def parse(self, text: str, now: datetime, last_title: str | None = None,
              history: list[dict] | None = None) -> ParsedCommand | None:
        try:
            resp = self.client.messages.create(
                model=config.LLM_MODEL,
                max_tokens=600,
                system=_system(now, last_title, history),
                tools=[TOOL],
                tool_choice={"type": "tool", "name": "parse_command"},
                messages=[{"role": "user", "content": text}],
            )
            data = next(b.input for b in resp.content if b.type == "tool_use")
        except Exception as exc:  # сеть, лимиты, неожиданный ответ — откатываемся на правила
            log.warning("LLM parse failed, fallback to rules: %s", exc)
            return None
        return self._to_command(data)

    @staticmethod
    def _to_command(data: dict) -> ParsedCommand | None:
        try:
            intent = Intent(data.get("intent", "unknown"))
        except ValueError:
            return None
        cmd = ParsedCommand(intent=intent, source="llm")
        if data.get("event_type") in ("task", "meeting", "reminder"):
            cmd.event_type = EventType(data["event_type"])
        cmd.title = (data.get("title") or "").strip() or None
        cmd.date, cmd.time = _d(data.get("date")), _t(data.get("time"))
        cmd.has_deadline = bool(data.get("deadline"))
        if data.get("recurrence") in ("daily", "weekly", "weekdays"):
            cmd.recurrence = Recurrence(data["recurrence"])
        ps, pe = _d(data.get("period_start")), _d(data.get("period_end"))
        if ps:
            cmd.period = Period(ps, pe or ps, data.get("period_label") or "")
        elif cmd.date and intent in (Intent.LIST, Intent.SEARCH):
            cmd.period = Period(cmd.date, cmd.date, data.get("period_label") or "")
        cmd.query = (data.get("query") or "").strip() or None
        cmd.tense = data.get("tense") if data.get("tense") in ("past", "future") else None
        cmd.refers_to_last = bool(data.get("refers_to_last"))
        if data.get("duration_min"):
            cmd.extras["duration_min"] = int(data["duration_min"])
        return cmd
