"""Диалоговый менеджер: понимание → уточнение → действие → ответ.

Здесь вся бизнес-логика. Модуль не знает ни про HTTP, ни про голос:
на вход — текст реплики, на выход — Reply с текстом для экрана и
коротким текстом для озвучивания.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from typing import Callable

from . import config
from .formatting import (TYPE_ACC, TYPE_NAMES, WEEKDAYS_NOM, count_phrase, describe, human_date, human_time,
                         human_when, plural)
from .models import Event, EventType, Intent, ParsedCommand, Period
from .nlu import dates, rules
from .nlu.text import tokenize
from .search import search
from .semantic import Embedder
from .storage import Storage

log = logging.getLogger(__name__)

ORDINALS = {"первую": 0, "первый": 0, "первое": 0, "1": 0, "вторую": 1, "второй": 1, "второе": 1, "2": 1,
            "третью": 2, "третий": 2, "третье": 2, "3": 2, "четвертую": 3, "четвертый": 3, "4": 3,
            "пятую": 4, "пятый": 4, "5": 4, "последнюю": -1, "последний": -1, "последнее": -1}
# «без срока», «не важно», «в течение дня», «когда-нибудь» — пользователь отказывается называть время
NO_TIME_WORDS = {"без", "неважно", "важно", "любое", "любой", "весь", "течение", "никогда", "когда-нибудь", "потом", "знаю", "любое"}
MAX_SPOKEN = 5
MAX_SHOWN = 30

HELP_TEXT = (
    "Я веду ваши задачи, напоминания и встречи. Можно сказать, например: «Напомни завтра в 10 позвонить Ивану», "
    "«Добавь встречу с командой в пятницу в 15:00», «Что у меня сегодня?», «Найди задачу про презентацию», "
    "«Перенеси её на понедельник», «Переименуй её в Созвон с Иваном», «Отметь как выполненную», "
    "«Удали встречу с дизайнером», «Каждый понедельник в 10 планёрка»."
)


@dataclass
class Reply:
    text: str
    speech: str | None = None
    events: list[Event] = field(default_factory=list)
    intent: str = ""
    parser: str = ""
    awaiting: str | None = None     # что ассистент ждёт от пользователя (уточнение)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "speech": self.speech or self.text,
            "events": [e.to_dict() for e in self.events],
            "intent": self.intent,
            "parser": self.parser,
            "awaiting": self.awaiting,
        }


@dataclass
class Pending:
    kind: str                         # need_title | need_time | need_new_time | need_new_title | confirm_conflict | confirm_delete | choose
    draft: Event | None = None
    action: str | None = None         # для choose: reschedule | complete | delete | rename
    candidates: list[Event] = field(default_factory=list)
    cmd: ParsedCommand | None = None
    deadline: bool = False            # дата черновика — срок («до пятницы»), время не нужно


@dataclass
class Session:
    last_ids: list[int] = field(default_factory=list)
    pending: Pending | None = None


class Assistant:
    def __init__(self, storage: Storage, llm=None, clock: Callable[[], datetime] = config.now,
                 embedder: Embedder | None = None):
        self.storage = storage
        self.llm = llm
        self.embedder = embedder
        self.clock = clock
        self.sessions: dict[tuple[str, str], Session] = {}

    # ------------------------------------------------------------------ entry

    def handle(self, user_id: str, text: str, session_id: str = "default") -> Reply:
        text = (text or "").strip()
        session = self.sessions.setdefault((user_id, session_id), Session())
        if not text:
            return Reply("Я вас не расслышал. Повторите, пожалуйста.", intent="empty")
        now = self.clock()

        small_talk = _small_talk(text)
        if small_talk:
            return Reply(small_talk, intent="smalltalk", parser="rules")

        try:
            rule_cmd = rules.parse(text, now)
            if session.pending:
                reply = self._continue_pending(user_id, session, text, rule_cmd, now)
                if reply is not None:
                    reply.parser = "rules"      # ответы на уточнения разбираются правилами
                    return reply
                session.pending = None          # пользователь сменил тему — начинаем заново

            cmd = self._understand(user_id, session, text, rule_cmd, now)
            reply = self._dispatch(user_id, session, cmd, now)
        except Exception:
            log.exception("failed to handle %r", text)
            session.pending = None
            return Reply("Что-то пошло не так при выполнении. Попробуйте сформулировать иначе.", intent="error")
        reply.intent = reply.intent or cmd.intent.value
        reply.parser = cmd.source
        return reply

    def _understand(self, user_id: str, session: Session, text: str, rule_cmd: ParsedCommand, now: datetime) -> ParsedCommand:
        if self.llm is None or rule_cmd.intent in (Intent.CONFIRM, Intent.REJECT, Intent.HELP):
            return rule_cmd
        last = self.storage.get(user_id, session.last_ids[0]) if len(session.last_ids) == 1 else None
        llm_cmd = self.llm.parse(text, now, last.title if last else None)
        return llm_cmd or rule_cmd

    def _dispatch(self, user_id: str, s: Session, cmd: ParsedCommand, now: datetime) -> Reply:
        handlers = {
            Intent.CREATE: self._create,
            Intent.LIST: self._list,
            Intent.SEARCH: self._search,
            Intent.RESCHEDULE: self._modify,
            Intent.COMPLETE: self._modify,
            Intent.DELETE: self._modify,
            Intent.RENAME: self._modify,
        }
        if cmd.intent in handlers:
            return handlers[cmd.intent](user_id, s, cmd, now)
        if cmd.intent is Intent.HELP:
            return Reply(HELP_TEXT)
        if cmd.intent in (Intent.CONFIRM, Intent.REJECT):
            return Reply("Хорошо. Чем ещё помочь?" if cmd.intent is Intent.CONFIRM else "Хорошо, ничего не делаю.")
        return Reply("Не совсем понял запрос. " + HELP_TEXT, speech="Не совсем понял. Скажите, например: что у меня сегодня, "
                     "или: напомни завтра в 10 позвонить Ивану.")

    # ------------------------------------------------------------- уточнения

    def _continue_pending(self, user_id: str, s: Session, text: str, rule_cmd: ParsedCommand, now: datetime) -> Reply | None:
        p = s.pending
        if rule_cmd.intent is Intent.REJECT:
            s.pending = None
            return Reply("Хорошо, отменил.", intent="reject")

        if p.kind == "need_title":
            if rule_cmd.intent in (Intent.LIST, Intent.SEARCH, Intent.DELETE, Intent.RESCHEDULE, Intent.COMPLETE):
                return None
            title = rule_cmd.title if rule_cmd.intent is Intent.CREATE and rule_cmd.title else rules.build_title(tokenize(text)) or text
            draft = replace(p.draft, title=title[:1].upper() + title[1:])
            if rule_cmd.date and not draft.date:
                draft.date = rule_cmd.date
            if rule_cmd.time and not draft.time:
                draft.time = rule_cmd.time
            s.pending = None
            return self._finalize_create(user_id, s, draft, now, deadline=p.deadline or rule_cmd.has_deadline)

        if p.kind in ("need_time", "need_new_time"):
            # новая полноценная команда прерывает уточнение
            if rule_cmd.intent not in (Intent.UNKNOWN, Intent.CONFIRM, Intent.CREATE) or (
                    rule_cmd.intent is Intent.CREATE and (rule_cmd.title or rule_cmd.event_type is not p.draft.type)):
                return None
            tokens = tokenize(text)
            info = dates.extract(tokens, now, future_bias=True)
            no_time = p.kind == "need_time" and bool({t.norm for t in tokens} & NO_TIME_WORDS)
            if info.time is None and info.date is None:
                # голое число: «в 11» без предлога уже покрыто; «11», «15:30» — тоже время
                words = [t.norm for t in tokens if t.norm[0].isalnum()]
                if len(words) == 1 and words[0].isdigit() and 0 <= int(words[0]) <= 23:
                    info.time = dates._fix_hour(int(words[0]), 0, None)
                elif no_time:
                    s.pending = None
                    return self._finalize_create(user_id, s, p.draft, now, allow_no_time=True)
                elif p.kind == "need_time" and rule_cmd.intent in (Intent.UNKNOWN, Intent.CREATE) and _is_generic(p.draft.title):
                    # «Добавь встречу» → «с бухгалтером»: это уточнение названия, время спросим ещё раз
                    extra = rules.build_title(tokenize(text)) or text
                    p.draft.title = f"{p.draft.title} {extra[:1].lower() + extra[1:]}".strip()
                    return Reply(f"Хорошо, «{p.draft.title}». На какой день и время?", awaiting="need_time", intent="create")
                else:
                    return None if rule_cmd.intent is not Intent.UNKNOWN else Reply(
                        "Не понял время. Скажите, например: «в 15:00» или «завтра в 10 утра».", awaiting=p.kind)
            if p.kind == "need_time":
                draft = replace(p.draft, date=info.date or p.draft.date, time=info.time or p.draft.time)
                if info.duration_min:
                    draft.duration_min = info.duration_min
                s.pending = None
                return self._finalize_create(user_id, s, draft, now, allow_no_time=no_time,
                                             deadline=p.deadline or info.deadline)
            target = p.draft
            s.pending = None
            return self._apply_reschedule(user_id, s, target, info.date, info.time, now)

        if p.kind == "need_new_title":
            if rule_cmd.intent not in (Intent.UNKNOWN, Intent.CREATE):
                return None
            title = text.strip(" «»\"'.!")
            s.pending = None
            return self._apply_rename(user_id, s, p.draft, title)

        if p.kind == "confirm_conflict" and rule_cmd.intent in (Intent.RESCHEDULE, Intent.UNKNOWN)                 and rule_cmd.query is None and (rule_cmd.date or rule_cmd.time):
            # «Давай на 14:30», «Измени время на 14:30» — двигаем новую встречу и проверяем снова
            draft = replace(p.draft, date=rule_cmd.date or p.draft.date, time=rule_cmd.time or p.draft.time)
            s.pending = None
            return self._finalize_create(user_id, s, draft, now)

        if p.kind in ("confirm_conflict", "confirm_delete"):
            if rule_cmd.intent is Intent.CONFIRM:
                s.pending = None
                if p.kind == "confirm_conflict":
                    return self._save(user_id, s, p.draft, now)
                self.storage.delete(user_id, p.draft.id)
                s.last_ids = []
                return Reply(f"Удалил {TYPE_ACC[p.draft.type]} «{p.draft.title}».", intent="delete")
            if rule_cmd.intent is Intent.UNKNOWN:
                return Reply("Скажите «да» или «нет».", awaiting=p.kind)
            return None

        if p.kind == "choose":
            words = [t.norm for t in tokenize(text) if t.norm[0].isalnum()]
            idx = next((ORDINALS[w] for w in words if w in ORDINALS), None)
            chosen = None
            if idx is not None and -len(p.candidates) <= idx < len(p.candidates):
                chosen = p.candidates[idx]
            elif rule_cmd.intent in (Intent.UNKNOWN, Intent.SEARCH):
                hits = search(p.candidates, text, now.date())
                if len(hits) == 1:
                    chosen = hits[0].event
            if chosen is None:
                if rule_cmd.intent is Intent.UNKNOWN:
                    return Reply("Назовите номер: «первую», «вторую» и так далее.", awaiting="choose")
                return None
            s.pending = None
            s.last_ids = [chosen.id]
            return self._do_action(user_id, s, p.action, chosen, p.cmd, now)
        return None

    # ---------------------------------------------------------------- create

    def _create(self, user_id: str, s: Session, cmd: ParsedCommand, now: datetime) -> Reply:
        etype = cmd.event_type or EventType.TASK
        draft = Event(
            id=None, user_id=user_id, type=etype, title=cmd.title or "", date=cmd.date, time=cmd.time,
            recurrence=cmd.recurrence, duration_min=cmd.extras.get("duration_min") or 60,
        )
        if not draft.title:
            s.pending = Pending("need_title", draft=draft, deadline=cmd.has_deadline)
            q = {EventType.MEETING: "С кем или о чём встреча?", EventType.REMINDER: "О чём напомнить?",
                 EventType.TASK: "Как назвать задачу?"}[etype]
            return Reply(q, awaiting="need_title", intent="create")
        return self._finalize_create(user_id, s, draft, now, deadline=cmd.has_deadline)

    def _finalize_create(self, user_id: str, s: Session, draft: Event, now: datetime,
                         allow_no_time: bool = False, deadline: bool = False) -> Reply:
        # только время без даты → сегодня, а если время уже прошло — завтра
        if draft.time and not draft.date:
            draft.date = now.date() if draft.time > now.time() else now.date() + timedelta(days=1)

        if draft.type is EventType.MEETING and not allow_no_time and (draft.date is None or draft.time is None):
            s.pending = Pending("need_time", draft=draft)
            if draft.date is None and draft.time is None:
                q = "На какой день и время поставить встречу?"
            elif draft.time is None:
                q = f"На какое время поставить встречу {human_date(draft.date, now.date())}?"
            else:
                q = "На какой день поставить встречу?"
            return Reply(q, awaiting="need_time", intent="create")
        if draft.type is EventType.REMINDER and draft.time is None and not allow_no_time:
            s.pending = Pending("need_time", draft=draft)
            q = "Когда напомнить?" if draft.date is None else f"Во сколько напомнить {human_date(draft.date, now.date())}?"
            return Reply(q, awaiting="need_time", intent="create")
        # задача без времени: спрашиваем «когда и во сколько», кроме сроков («до пятницы») и повторов
        if draft.type is EventType.TASK and draft.time is None and not (allow_no_time or deadline or draft.recurrence):
            s.pending = Pending("need_time", draft=draft)
            if draft.date is None:
                q = f"Когда и во сколько нужно «{draft.title}»? Можно ответить «без срока»."
            else:
                q = f"Во сколько {human_date(draft.date, now.date())}? Можно ответить «в течение дня»."
            return Reply(q, awaiting="need_time", intent="create")

        if draft.type is EventType.MEETING and draft.time:
            clashes = self.storage.conflicts(user_id, draft.date, draft.time, draft.duration_min)
            if clashes:
                s.pending = Pending("confirm_conflict", draft=draft)
                c = clashes[0]
                more = f" и ещё {len(clashes) - 1}" if len(clashes) > 1 else ""
                return Reply(
                    f"В это время у вас уже есть встреча «{c.title}» в {human_time(c.time)}{more}. Всё равно добавить?",
                    events=clashes, awaiting="confirm_conflict", intent="create",
                )
        return self._save(user_id, s, draft, now)

    def _save(self, user_id: str, s: Session, draft: Event, now: datetime) -> Reply:
        draft.created_at = now
        saved = self.storage.add(draft)
        s.last_ids = [saved.id]
        when = human_when(saved, now.date())
        if saved.type is EventType.REMINDER:
            text = f"Хорошо, напомню {when}: «{saved.title}»."
        elif saved.type is EventType.MEETING:
            text = f"Добавил встречу «{saved.title}» — {when}."
        elif saved.time or saved.recurrence:
            text = f"Записал задачу «{saved.title}» — {when}."
        elif saved.date:
            text = f"Записал задачу «{saved.title}», срок — {when}."
        else:
            text = f"Записал задачу «{saved.title}» без срока."
        if saved.start and saved.time and saved.start < now and not saved.recurrence:
            text += " Обратите внимание: это время уже прошло."
        return Reply(text, events=[saved], intent="create")

    # ------------------------------------------------------------------ list

    def _list(self, user_id: str, s: Session, cmd: ParsedCommand, now: datetime) -> Reply:
        today = now.date()
        if cmd.query and not cmd.period and not cmd.date:
            # «были ли встречи с Альфой», «что там с CRM» — это поиск, а не список на период
            return self._search(user_id, s, cmd, now)
        period = cmd.period or (Period(cmd.date, cmd.date, human_date(cmd.date, today)) if cmd.date else None)
        if period is None:
            period = Period(today, today + timedelta(days=6), "в ближайшие 7 дней")
        events = self.storage.in_range(user_id, period.start, period.end)
        if cmd.event_type is EventType.MEETING:
            events = [e for e in events if e.type is EventType.MEETING]
        elif cmd.event_type in (EventType.TASK, EventType.REMINDER):
            events = [e for e in events if e.type is not EventType.MEETING]
        if cmd.query:
            events = [h.event for h in search(events, cmd.query, today, limit=len(events) or 1)]
        label = _period_label(period, today)
        what = {EventType.MEETING: "встреч", EventType.TASK: "задач", EventType.REMINDER: "задач"}.get(cmd.event_type, "")
        s.last_ids = [e.id for e in events]
        if not events:
            text = f"{label.capitalize()} {what} нет." if what else f"{label.capitalize()} ничего не запланировано."
            return Reply(text, intent="list")

        single_day = period.start == period.end
        lines, spoken = [], []
        current_day = None
        for e in events[:MAX_SHOWN]:
            if not single_day and e.date != current_day:
                current_day = e.date
                lines.append(f"\n{WEEKDAYS_NOM[e.date.weekday()].capitalize()}, {human_date(e.date, today, with_weekday=False)}:")
            t = human_time(e.time) if e.time else "в течение дня"
            lines.append(f"• {t} — {TYPE_NAMES[e.type]} «{e.title}»" + (" ✓" if e.done else ""))
        for e in events[:MAX_SPOKEN]:
            day = "" if single_day else human_date(e.date, today) + " "
            t = f"в {human_time(e.time)}" if e.time else ""
            spoken.append(f"{day}{t} {e.title}".strip())

        header = f"{label.capitalize()} у вас {count_phrase(events)}:"
        rest = len(events) - MAX_SHOWN
        text = header + "\n" + "\n".join(lines).strip("\n") + (f"\n…и ещё {rest}." if rest > 0 else "")
        speech = header.rstrip(":") + ". " + "; ".join(spoken)
        if len(events) > MAX_SPOKEN:
            speech += f". И ещё {len(events) - MAX_SPOKEN} — смотрите на экране."
        return Reply(text, speech=speech, events=events[:MAX_SHOWN], intent="list")

    # ---------------------------------------------------------------- search

    def _find(self, user_id: str, cmd: ParsedCommand, now: datetime, query: str | None = None):
        events = self.storage.all(user_id)
        q = query if query is not None else (cmd.query or "")
        attempts = [(cmd.event_type, cmd.tense, cmd.period), (None, cmd.tense, cmd.period), (None, None, cmd.period), (None, None, None)]
        for etype, tense, period in attempts:
            hits = search(events, q, now.date(), event_type=etype, tense=tense, period=period, limit=1000,
                          embedder=self.embedder)
            if hits:
                return hits
        return []

    def _search(self, user_id: str, s: Session, cmd: ParsedCommand, now: datetime) -> Reply:
        today = now.date()
        if not cmd.query:
            if cmd.period or cmd.event_type:
                return self._list(user_id, s, cmd, now)
            return Reply("Что найти? Назовите, о чём была задача или встреча.", intent="search")
        hits = self._find(user_id, cmd, now)
        if not hits:
            s.last_ids = []
            return Reply(f"Не нашёл записей по запросу «{cmd.query}». Попробуйте сказать иначе.", intent="search")
        found = [h.event for h in hits]
        s.last_ids = [e.id for e in found]
        top = found[0]
        if cmd.tense == "past":
            past = [e for e in found if e.date and e.date <= today]
            top = past[0] if past else top
            head = f"Последний раз — {human_when(top, today)}: {TYPE_NAMES[top.type]} «{top.title}»."
        elif len(found) == 1:
            head = f"Нашёл {describe(top, today)}."
        else:
            head = f"Нашёл {len(found)} {plural(len(found), 'запись', 'записи', 'записей')}. Ближайшая — {describe(top, today)}."
        lines = [f"• {describe(e, today)}" for e in found[:10]]
        text = head + ("\n" + "\n".join(lines) if len(found) > 1 else "")
        if len(found) > 10:
            text += f"\n…и ещё {len(found) - 10}."
        if len(found) == 1:
            s.last_ids = [top.id]
        return Reply(text, speech=head, events=found[:10], intent="search")

    # ------------------------------------------------ reschedule / complete / delete

    def _modify(self, user_id: str, s: Session, cmd: ParsedCommand, now: datetime) -> Reply:
        action = cmd.intent.value
        candidates: list[Event] = []
        if cmd.query and not cmd.refers_to_last:
            # при переносе новая дата не должна фильтровать поиск старой записи
            lookup = replace(cmd, period=None if cmd.intent is Intent.RESCHEDULE else cmd.period)
            hits = self._find(user_id, lookup, now)
            if cmd.intent is Intent.COMPLETE:
                hits = [h for h in hits if not h.event.done] or hits
            # среди равных по релевантности берём предстоящие
            upcoming = [h for h in hits if h.event.date is None or h.event.date >= now.date()]
            if cmd.intent is not Intent.DELETE and upcoming:
                hits = upcoming
            candidates = [h.event for h in hits]
            # удаление необратимо — при нескольких совпадениях всегда спрашиваем
            if cmd.intent is not Intent.DELETE:
                # запись, о которой только что говорили, важнее остальных совпадений
                recent = [e for e in candidates if e.id in s.last_ids]
                if len(recent) == 1:
                    candidates = recent
                # явный лидер по релевантности — без лишнего вопроса «какую?»
                elif len(hits) > 1 and hits[0].score >= 1.3 * hits[1].score:
                    candidates = [hits[0].event]
        elif s.last_ids:
            candidates = [e for e in (self.storage.get(user_id, i) for i in s.last_ids[:5]) if e]

        verb = {"reschedule": "перенести", "complete": "отметить выполненной", "delete": "удалить",
                "rename": "переименовать"}[action]
        if not candidates:
            if cmd.query:
                return Reply(f"Не нашёл записи «{cmd.query}», которую нужно {verb}.", intent=action)
            return Reply(f"Какую запись {verb}? Назовите её, например: «{verb} встречу с Иваном».", intent=action)
        if len(candidates) > 1:
            shown = candidates[:5]
            s.pending = Pending("choose", action=action, candidates=shown, cmd=cmd)
            s.last_ids = [e.id for e in shown]
            lines = [f"{k + 1}) {describe(e, now.date())}" for k, e in enumerate(shown)]
            return Reply(f"Нашёл несколько записей. Какую {verb}?\n" + "\n".join(lines),
                         speech=f"Нашёл несколько записей. Какую {verb}? " + "; ".join(lines),
                         events=shown, awaiting="choose", intent=action)
        target = candidates[0]
        s.last_ids = [target.id]
        return self._do_action(user_id, s, action, target, cmd, now)

    def _do_action(self, user_id: str, s: Session, action: str, target: Event, cmd: ParsedCommand | None, now: datetime) -> Reply:
        today = now.date()
        if action == "complete":
            target.done = True
            self.storage.update(target)
            return Reply(f"Отметил выполненной: {describe(target, today)}.", events=[target], intent="complete")
        if action == "rename":
            if not (cmd and cmd.title):
                s.pending = Pending("need_new_title", draft=target)
                return Reply(f"Как назвать {TYPE_ACC[target.type]} «{target.title}»?", awaiting="need_new_title", intent="rename")
            return self._apply_rename(user_id, s, target, cmd.title)
        if action == "delete":
            s.pending = Pending("confirm_delete", draft=target)
            return Reply(f"Удалить {TYPE_ACC[target.type]} «{target.title}» ({human_when(target, today)})?", events=[target], awaiting="confirm_delete", intent="delete")
        new_date, new_time = (cmd.date, cmd.time) if cmd else (None, None)
        if new_date is None and new_time is None:
            s.pending = Pending("need_new_time", draft=target)
            return Reply(f"На когда перенести {TYPE_ACC[target.type]} «{target.title}»?", awaiting="need_new_time", intent="reschedule")
        return self._apply_reschedule(user_id, s, target, new_date, new_time, now)

    def _apply_rename(self, user_id: str, s: Session, target: Event, new_title: str) -> Reply:
        stored = self.storage.get(user_id, target.id) or target
        old = stored.title
        stored.title = new_title[:1].upper() + new_title[1:]
        self.storage.update(stored)
        s.last_ids = [stored.id]
        return Reply(f"Переименовал: «{old}» → «{stored.title}».", events=[stored], intent="rename")

    def _apply_reschedule(self, user_id: str, s: Session, target: Event, new_date: date | None, new_time: time | None, now: datetime) -> Reply:
        # повторяющиеся записи переносим целиком (меняется опорная дата серии)
        stored = self.storage.get(user_id, target.id) or target
        stored.date = new_date or stored.date or now.date()
        if new_time:
            stored.time = new_time
        self.storage.update(stored)
        s.last_ids = [stored.id]
        text = f"Перенёс {TYPE_ACC[stored.type]} «{stored.title}», теперь — {human_when(stored, now.date())}."
        if stored.type is EventType.MEETING and stored.time:
            clashes = self.storage.conflicts(user_id, stored.date, stored.time, stored.duration_min, exclude_id=stored.id)
            if clashes:
                text += f" Учтите: в это время уже есть «{clashes[0].title}»."
        return Reply(text, events=[stored], intent="reschedule")


GREETINGS = {"привет", "здравствуй", "здравствуйте", "добрый день", "доброе утро", "добрый вечер", "хай", "алло"}
THANKS = {"спасибо", "благодарю", "спс", "отлично", "супер", "класс"}


def _small_talk(text: str) -> str | None:
    words = [t.norm for t in tokenize(text) if t.norm[0].isalnum()]
    phrase = " ".join(words)
    if not words or len(words) > 3:
        return None
    if phrase in GREETINGS or words[0] in GREETINGS:
        return "Привет! Чем помочь? Можно спросить, что у вас сегодня, или попросить что-нибудь запланировать."
    if words[0] in THANKS:
        return "Пожалуйста! Обращайтесь."
    return None


def _is_generic(title: str) -> bool:
    """Название из одного слова-типа: «Встреча», «Созвон»."""
    return len(title.split()) <= 1 and rules.lemma(title or "встреча") in rules.MEETING_LEMMAS


def _period_label(p: Period, today: date) -> str:
    if p.start == p.end:
        delta = (p.start - today).days
        if -1 <= delta < 7:
            return human_date(p.start, today)          # «завтра», «в понедельник, 19 октября»
        return "на " + human_date(p.start, today, False)
    if p.label:
        return p.label
    return f"с {human_date(p.start, today, False)} по {human_date(p.end, today, False)}"
