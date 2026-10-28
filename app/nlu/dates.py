"""Извлечение дат, времени, периодов и повторов из русской фразы.

Работает над списком токенов: распознанные токены помечаются used=True,
чтобы из остатка фразы можно было собрать название задачи.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from ..models import Period, Recurrence
from .text import Token

WEEKDAYS = {
    "понедельник": 0, "понедельника": 0, "понедельнику": 0, "понедельникам": 0,
    "вторник": 1, "вторника": 1, "вторнику": 1, "вторникам": 1,
    "среда": 2, "среду": 2, "среды": 2, "средам": 2, "среде": 2,
    "четверг": 3, "четверга": 3, "четвергу": 3, "четвергам": 3,
    "пятница": 4, "пятницу": 4, "пятницы": 4, "пятницам": 4, "пятнице": 4,
    "суббота": 5, "субботу": 5, "субботы": 5, "субботам": 5, "субботе": 5,
    "воскресенье": 6, "воскресенья": 6, "воскресеньям": 6, "воскресенью": 6,
}
MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
# «что у меня?» без периода и «в ближайшие дни» — сегодня и ещё 6 дней
UPCOMING_DAYS = 7
UPCOMING_WORDS = {"ближайшие", "ближайшую", "ближайший", "ближайшее", "предстоящие", "следующие", "грядущие"}
SPAN_UNITS = {"день": 1, "дня": 1, "дней": 1, "дни": 1, "неделю": 7, "недели": 7, "недель": 7, "неделя": 7,
              "месяц": 30, "месяца": 30, "месяцев": 30}

WEEKDAYS_ACC = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
RELATIVE_DAYS = {"позавчера": -2, "вчера": -1, "сегодня": 0, "сегодняшний": 0, "завтра": 1, "послезавтра": 2}
PREPOSITIONS = {"в", "во", "на", "к", "ко", "до", "с", "со", "за", "по", "около", "примерно", "где-то"}
NEXT_WORDS = {"следующий", "следующую", "следующее", "следующей", "следующего", "будущий", "будущую", "будущей", "следующем", "будущем"}
THIS_WORDS = {"этот", "эту", "это", "этой", "текущий", "текущую", "текущей", "этом", "текущем"}
PREV_WORDS = {"прошлый", "прошлую", "прошлое", "прошлой", "прошедший", "прошедшую", "прошедшей", "прошлом"}
EVERY_WORDS = {"каждый", "каждую", "каждое", "каждые"}
WEEK_WORDS = {"неделя", "неделю", "недели", "неделе"}
MONTH_WORDS = {"месяц", "месяца", "месяце"}
DAY_PARTS = {"утром": time(9, 0), "днем": time(13, 0), "вечером": time(19, 0)}
TIME_RE = re.compile(r"^(\d{1,2})[:.](\d{2})$")
DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?$")
UNIT_MINUTES = {
    "минуту": 1, "минуты": 1, "минут": 1,
    "час": 60, "часа": 60, "часов": 60,
}
UNIT_DAYS = {"день": 1, "дня": 1, "дней": 1, "неделю": 7, "недели": 7, "недель": 7}


@dataclass
class DateInfo:
    date: date | None = None
    time: time | None = None
    period: Period | None = None
    deadline: bool = False
    recurrence: Recurrence | None = None
    duration_min: int | None = None
    weekday_is_today: bool = False   # «в четверг», сказанное в четверг

    @property
    def found(self) -> bool:
        return any([self.date, self.time, self.period, self.recurrence])


def week_bounds(day: date) -> tuple[date, date]:
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def _consume(tokens: list[Token], start: int, end: int) -> None:
    """Пометить токены [start, end) и предлог перед ними как использованные."""
    for k in range(start, end):
        tokens[k].used = True
    if start > 0 and tokens[start - 1].norm in PREPOSITIONS and not tokens[start - 1].used:
        tokens[start - 1].used = True


def _fix_hour(hour: int, minute: int, qualifier: str | None) -> time | None:
    if qualifier in ("дня", "вечера") and hour < 12:
        hour += 12
    elif qualifier == "ночи" and hour == 12:
        hour = 0
    elif qualifier is None and 1 <= hour <= 7:
        # «в 3» в деловом контексте почти всегда 15:00
        hour += 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour, minute)


def _parse_time_at(tokens: list[Token], i: int) -> tuple[time, int] | None:
    """Время, начинающееся с токена i (после «в»/«к»). Возвращает (time, число токенов)."""
    n = [t.norm for t in tokens]
    j = i
    m = TIME_RE.match(n[j])
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        j += 1
        explicit = True
    elif n[j].isdigit() and int(n[j]) <= 24:
        hour, minute = int(n[j]), 0
        j += 1
        explicit = False
        if j < len(n) and n[j].isdigit() and int(n[j]) < 60 and len(n[j]) == 2:   # «в 10 30»
            minute = int(n[j])
            j += 1
            explicit = True
    elif n[j] == "час":                      # «в час дня»
        hour, minute, explicit = 1, 0, False
        j += 1
    elif n[j] == "полдень":
        return time(12, 0), 1
    elif n[j] == "полночь":
        return time(0, 0), 1
    else:
        return None
    if j < len(n) and n[j] in ("час", "часа", "часов"):
        j += 1
    if j < len(n) and n[j].isdigit() and j + 1 < len(n) and n[j + 1] in ("минут", "минуты", "минуту"):
        minute = int(n[j])
        j += 2
    qualifier = None
    if j < len(n) and n[j] in ("утра", "дня", "вечера", "ночи"):
        qualifier = n[j]
        j += 1
    if explicit and qualifier is None:
        qualifier = "explicit"                # «15:00» и «7:30» не трогаем
    t = _fix_hour(hour, minute, qualifier if qualifier != "explicit" else "утра")
    return (t, j - i) if t else None


def _resolve_weekday(today: date, weekday: int, modifier: str | None, prefer_past: bool) -> date:
    if modifier == "next":
        monday_next = week_bounds(today)[0] + timedelta(days=7)
        return monday_next + timedelta(days=weekday)
    if modifier == "this":
        return week_bounds(today)[0] + timedelta(days=weekday)
    if modifier == "prev" or prefer_past:
        delta = (today.weekday() - weekday) % 7
        return today - timedelta(days=delta or (7 if modifier == "prev" else 0))
    return today + timedelta(days=(weekday - today.weekday()) % 7)


def extract(tokens: list[Token], now: datetime, prefer_past: bool = False, future_bias: bool = False) -> DateInfo:
    today = now.date()
    info = DateInfo()
    n = [t.norm for t in tokens]
    i = 0
    while i < len(n):
        w = n[i]
        nxt = n[i + 1] if i + 1 < len(n) else ""
        prev = n[i - 1] if i > 0 else ""

        # --- повторы: «каждый день», «по понедельникам», «ежедневно», «по будням»
        if w in ("ежедневно",) or (w in EVERY_WORDS and nxt in ("день", "утро", "вечер")):
            info.recurrence = Recurrence.DAILY
            info.date = info.date or today
            _consume(tokens, i, i + (1 if w == "ежедневно" else 2))
            i += 1
            continue
        if w == "еженедельно" or (w in EVERY_WORDS and nxt in WEEK_WORDS):
            info.recurrence = Recurrence.WEEKLY
            info.date = info.date or today
            _consume(tokens, i, i + (1 if w == "еженедельно" else 2))
            i += 1
            continue
        if (w == "по" and nxt in ("будням", "рабочим")) or (w in EVERY_WORDS and nxt in ("будний", "рабочий")):
            info.recurrence = Recurrence.WEEKDAYS
            info.date = info.date or today
            end = i + 2 + (1 if i + 2 < len(n) and n[i + 2] in ("дням", "день") else 0)
            _consume(tokens, i, end)
            i = end
            continue
        if (w in EVERY_WORDS or w == "по") and nxt in WEEKDAYS and (w != "по" or nxt.endswith("ам")):
            info.recurrence = Recurrence.WEEKLY
            info.date = _resolve_weekday(today, WEEKDAYS[nxt], None, False)
            _consume(tokens, i, i + 2)
            i += 2
            continue

        # --- «до конца недели / месяца / дня»
        if w == "до" and nxt == "конца" and i + 2 < len(n):
            unit = n[i + 2]
            end = i + 3
            if unit in ("этой", "текущей", "следующей") and i + 3 < len(n):
                unit_mod, unit = unit, n[i + 3]
                end += 1
            else:
                unit_mod = None
            if unit in ("недели",):
                base = today + timedelta(days=7) if unit_mod == "следующей" else today
                info.date = week_bounds(base)[1]
            elif unit in ("месяца",):
                info.date = today.replace(day=calendar.monthrange(today.year, today.month)[1])
            elif unit in ("дня",):
                info.date = today
            else:
                i += 1
                continue
            info.deadline = True
            for k in range(i, end):
                tokens[k].used = True
            i = end
            continue

        # --- «через 2 часа», «через полчаса», «через неделю», «через пару дней»
        if w == "через" and i + 1 < len(n):
            j = i + 1
            amount = 1
            if n[j].isdigit():
                amount = int(n[j]); j += 1
            elif n[j] in ("пару", "пара"):
                amount = 2; j += 1
            elif n[j] == "полчаса":
                target = now + timedelta(minutes=30)
                info.date, info.time = target.date(), target.time().replace(second=0)
                _consume(tokens, i, j + 1); i = j + 1
                continue
            if j < len(n) and n[j] in UNIT_MINUTES:
                target = now + timedelta(minutes=amount * UNIT_MINUTES[n[j]])
                info.date, info.time = target.date(), target.time().replace(second=0, microsecond=0)
                _consume(tokens, i, j + 1); i = j + 1
                continue
            if j < len(n) and n[j] in UNIT_DAYS:
                info.date = today + timedelta(days=amount * UNIT_DAYS[n[j]])
                _consume(tokens, i, j + 1); i = j + 1
                continue

        # --- сегодня / завтра / послезавтра / вчера
        if w in RELATIVE_DAYS:
            info.date = today + timedelta(days=RELATIVE_DAYS[w])
            label = {"сегодняшний": "сегодня"}.get(w, w)
            info.period = Period(info.date, info.date, label)
            if prev in ("до", "к"):
                info.deadline = True
            _consume(tokens, i, i + 1)
            i += 1
            continue

        # --- «в ближайшие дни», «на ближайшие 3 дня», «ближайшую неделю», «следующие две недели»
        if w in UPCOMING_WORDS:
            j, amount = i + 1, 1
            if j < len(n) and n[j].isdigit():
                amount, j = int(n[j]), j + 1
            elif j < len(n) and n[j] in ("пару", "пара"):
                amount, j = 2, j + 1
            if j < len(n) and (n[j] in SPAN_UNITS or n[j] == "время"):
                days = UPCOMING_DAYS if n[j] in ("время", "дни") and amount == 1 else amount * SPAN_UNITS.get(n[j], 1)
                days = max(1, min(days, 366))
                info.period = Period(today, today + timedelta(days=days - 1),
                                     "в ближайшие дни" if n[j] in ("время", "дни") and amount == 1
                                     else f"в ближайшие {days} {'день' if days == 1 else 'дня' if 2 <= days % 10 <= 4 and not 12 <= days % 100 <= 14 else 'дней'}")
                _consume(tokens, i, j + 1)
                i = j + 1
                continue

        # --- неделя / месяц / выходные как период
        if w in WEEK_WORDS or w in MONTH_WORDS:
            modifier = prev if prev in NEXT_WORDS | THIS_WORDS | PREV_WORDS else None
            start_idx = i - 1 if modifier else i
            if w in WEEK_WORDS:
                if modifier in NEXT_WORDS:
                    s, e = week_bounds(today + timedelta(days=7)); label = "на следующей неделе"
                elif modifier in PREV_WORDS:
                    s, e = week_bounds(today - timedelta(days=7)); label = "на прошлой неделе"
                elif modifier in THIS_WORDS or prev in ("на", "эту", "всю") or w == "неделе":
                    s, e = week_bounds(today); label = "на этой неделе"
                else:
                    i += 1
                    continue
            else:
                if modifier in NEXT_WORDS:
                    first = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
                    label = "в следующем месяце"
                elif modifier in PREV_WORDS:
                    first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
                    label = "в прошлом месяце"
                elif modifier in THIS_WORDS:
                    first = today.replace(day=1); label = "в этом месяце"
                else:
                    i += 1
                    continue
                s, e = first, first.replace(day=calendar.monthrange(first.year, first.month)[1])
            info.period = Period(s, e, label)
            _consume(tokens, start_idx, i + 1)
            i += 1
            continue
        if w in ("выходные", "выходных"):
            sat = today + timedelta(days=(5 - today.weekday()) % 7) if today.weekday() < 6 else today - timedelta(days=1)
            if prev in NEXT_WORDS:
                sat += timedelta(days=7)
            info.period = Period(sat, sat + timedelta(days=1), "на выходных")
            _consume(tokens, i - 1 if prev in NEXT_WORDS | THIS_WORDS else i, i + 1)
            i += 1
            continue
        # --- день недели: «в пятницу», «в следующий вторник», «до четверга»
        if w in WEEKDAYS:
            modifier = None
            start_idx = i
            if prev in NEXT_WORDS:
                modifier, start_idx = "next", i - 1
            elif prev in THIS_WORDS:
                modifier, start_idx = "this", i - 1
            elif prev in PREV_WORDS:
                modifier, start_idx = "prev", i - 1
            info.date = _resolve_weekday(today, WEEKDAYS[w], modifier, prefer_past)
            info.weekday_is_today = modifier is None and info.date == today
            info.period = Period(info.date, info.date, ("во " if WEEKDAYS[w] == 1 else "в ") + WEEKDAYS_ACC[WEEKDAYS[w]])
            if start_idx > 0 and n[start_idx - 1] in ("до", "к", "ко"):
                info.deadline = True
            _consume(tokens, start_idx, i + 1)
            i += 1
            continue

        # --- «15 октября [2026]», «15-го октября»
        if w.isdigit() and 1 <= int(w) <= 31:
            j = i + 1
            if j < len(n) and n[j] in ("-го", "го", "числа"):
                j += 1
            if n[j - 1] == "числа" and (j >= len(n) or n[j] not in MONTHS):
                # «15 числа» — ближайшее 15-е
                try:
                    d = today.replace(day=int(w))
                    if d < today and not prefer_past:
                        d = (today.replace(day=1) + timedelta(days=32)).replace(day=int(w))
                except ValueError:
                    i += 1
                    continue
                info.date = d
                info.period = Period(d, d, f"{int(w)} числа")
                _consume(tokens, i, j)
                i = j
                continue
            if j < len(n) and n[j] in MONTHS:
                year = today.year
                end = j + 1
                if end < len(n) and n[end].isdigit() and len(n[end]) == 4:
                    year = int(n[end]); end += 1
                    if end < len(n) and n[end] in ("года", "г", "год"):
                        end += 1
                try:
                    d = date(year, MONTHS[n[j]], int(w))
                except ValueError:
                    i += 1
                    continue
                if future_bias and end == j + 1 and d < today:
                    d = d.replace(year=year + 1)
                info.date = d
                info.period = Period(d, d, f"{int(w)} {n[j]}")
                if i > 0 and n[i - 1] == "до":
                    info.deadline = True
                _consume(tokens, i, end)
                i = end
                continue

        # --- «15.10» и «15.10.2026» (но «15.30» после «в» — это время)
        m = DATE_RE.match(w)
        if m and not (prev in ("в", "к", "до") and TIME_RE.match(w) and int(m.group(2)) > 12):
            day_num, month_num, year_s = int(m.group(1)), int(m.group(2)), m.group(3)
            if 1 <= month_num <= 12 and not (prev in ("в", "к") and year_s is None):
                year = int(year_s) + (2000 if year_s and len(year_s) == 2 else 0) if year_s else today.year
                try:
                    d = date(year, month_num, day_num)
                    if future_bias and not year_s and d < today:
                        d = d.replace(year=year + 1)
                    info.date = d
                    info.period = Period(d, d, d.strftime("%d.%m.%Y"))
                    _consume(tokens, i, i + 1)
                    i += 1
                    continue
                except ValueError:
                    pass

        # --- «с 10 до 12»
        if w in ("с", "со") and i + 1 < len(n):
            start_t = _parse_time_at(tokens, i + 1)
            if start_t:
                j = i + 1 + start_t[1]
                if j < len(n) and n[j] == "до" and j + 1 < len(n):
                    end_t = _parse_time_at(tokens, j + 1)
                    if end_t:
                        info.time = start_t[0]
                        if end_t[0] <= start_t[0] and end_t[0].hour < 12:
                            end_t = (time(end_t[0].hour + 12, end_t[0].minute), end_t[1])
                        delta = datetime.combine(today, end_t[0]) - datetime.combine(today, start_t[0])
                        if delta.total_seconds() > 0:
                            info.duration_min = int(delta.total_seconds() // 60)
                        _consume(tokens, i, j + 1 + end_t[1])
                        i = j + 1 + end_t[1]
                        continue

        # --- время: «в 10 утра», «к 15:00», «в 15.30», «в полдень», голое «15:00»
        if w in ("в", "во", "к", "ко", "на", "около") and i + 1 < len(n):
            parsed = _parse_time_at(tokens, i + 1)
            if parsed and (n[i + 1] not in ("час",) or (i + 2 < len(n) and n[i + 2] in ("дня", "ночи"))):
                # «на 2 часа» — это длительность, а не время
                if w == "на" and i + 2 < len(n) and n[i + 2] in ("часа", "часов", "минут"):
                    i += 1
                    continue
                info.time = parsed[0]
                _consume(tokens, i, i + 1 + parsed[1])
                i = i + 1 + parsed[1]
                continue
        if TIME_RE.match(w) and info.time is None and int(TIME_RE.match(w).group(2)) < 60:
            parsed = _parse_time_at(tokens, i)
            if parsed:
                info.time = parsed[0]
                _consume(tokens, i, i + parsed[1])
                i += parsed[1]
                continue

        # --- длительность: «на 2 часа», «на 30 минут», «на полчаса»
        if w == "на" and i + 1 < len(n):
            if n[i + 1] == "полчаса":
                info.duration_min = 30
                _consume(tokens, i, i + 2); i += 2
                continue
            if n[i + 1] == "час":
                info.duration_min = 60
                _consume(tokens, i, i + 2); i += 2
                continue
            if n[i + 1].isdigit() and i + 2 < len(n) and n[i + 2] in UNIT_MINUTES:
                info.duration_min = int(n[i + 1]) * UNIT_MINUTES[n[i + 2]]
                _consume(tokens, i, i + 3); i += 3
                continue

        i += 1

    # «утром» / «вечером» — только если точного времени нет
    if info.time is None:
        for k, t in enumerate(tokens):
            if t.norm in DAY_PARTS and not t.used:
                info.time = DAY_PARTS[t.norm]
                t.used = True
                break
    return info
