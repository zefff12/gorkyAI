"""Rule-based понимание запросов: работает без сети и без LLM.

Интент определяется по леммам (а не по точным словам), поэтому
«Что у меня сегодня?», «Какие планы на сегодня?» и «Покажи мои встречи
на сегодня» дают одно и то же действие.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher, get_close_matches

from ..models import EventType, Intent, ParsedCommand, Period
from . import dates
from .text import Token, _morph, join_tokens, lemma, tokenize  # noqa: F401 (lemma реэкспортируется)

YES = {"да", "ага", "угу", "конечно", "давай", "подтверждаю", "верно", "ок", "окей", "ok", "хорошо", "добавляй", "оставь", "точно", "правильно"}
NO = {"нет", "не", "отмена", "отмени", "отменить", "стоп", "неверно", "неправильно", "передумал", "передумала"}
DELETE_LEMMAS = {"удалить", "убрать", "стереть", "вычеркнуть"}
RESCHEDULE_LEMMAS = {"перенести", "передвинуть", "сдвинуть", "переставить", "перенос", "отложить"}
SEARCH_LEMMAS = {"найти", "искать", "поискать", "отыскать", "найтись", "вспомнить"}
LIST_LEMMAS = {"показать", "покажи", "расписание", "список", "перечислить", "рассказать", "зачитать", "план", "планы", "дело", "занятой", "занятый"}
QUESTION_STARTS = {"что", "какой", "какие", "какая", "каких", "чем", "сколько", "есть", "покажи", "покажите", "расскажи", "скажи", "чего"}
MEETING_LEMMAS = {"встреча", "встретиться", "созвон", "созвониться", "совещание", "планёрка", "планерка", "демо",
                  "митинг", "переговоры", "собрание", "конференция", "звонок", "собеседование", "обед", "ужин", "стендап", "ретро", "интервью"}
TASK_LEMMAS = {"задача", "задачка", "todo", "туду"}
CREATE_LEMMAS = {"поставить", "добавить", "создать", "запланировать", "записать", "назначить", "внести", "завести", "запомнить"}
PAST_MARKERS = {"была", "был", "было", "были", "ставил", "ставила", "ставили", "прошла", "прошел", "проходила", "встречался",
                "встречалась", "созванивался", "созванивалась", "когда-то", "раньше", "прошлом", "уже", "добавлял", "добавляла", "создавал"}
FUTURE_MARKERS = {"будет", "будут", "предстоит", "следующая", "ближайшая", "ближайший", "следующий", "скоро"}
PRONOUN_REFS = {"ее", "её", "его", "их", "эту", "этот", "это", "эти", "последнюю", "последний", "последнее", "она", "он", "оно"}
DONE_FORMS = {"сделал", "сделала", "сделано", "выполнил", "выполнила", "выполнено", "закончил", "закончила", "готово",
              "выполнена", "сделана", "закрыл", "закрыла", "завершил", "завершила"}

# слова, которые срезаются с начала/конца остатка фразы при сборке названия
LEADING_FILLER = {
    "напомни", "напомните", "напомнить", "мне", "нам", "пожалуйста", "поставь", "поставьте", "поставить", "добавь",
    "добавьте", "добавить", "создай", "создайте", "создать", "запиши", "запишите", "записать", "запланируй",
    "запланировать", "назначь", "назначить", "внеси", "заведи", "запомни", "задачу", "задача", "задачку", "новую", "новая",
    "напоминание", "нужно", "надо", "необходимо", "не", "забыть", "забудь", "у", "меня", "я", "что", "чтобы", "о",
    "об", "про", "должен", "должна", "буду", "хочу", "будет", "есть", "ещё", "еще", "а", "и", "ну", "так", "вот",
    "срочно", "сделать", "мол", "это", "этот", "эту", "то", "там", "тут", "плюс", "еще", "ещё",
}
TRAILING_FILLER = {"пожалуйста", "плиз", "срочно", "а", "и", "в", "на", "к", "до", "с", "по"}
PLACE_PHRASES = [("в", "календарь"), ("в", "список"), ("в", "задачи"), ("в", "планы"), ("в", "план"), ("в", "ежедневник")]
SEARCH_STOP = {
    "найди", "найти", "поищи", "найдите", "поиск", "искать", "вспомни", "покажи", "когда", "у", "меня", "я", "мне", "была", "был",
    "было", "были", "будет", "будут", "ставил", "ставила", "задачу", "задача", "задачи", "запись", "записи", "напоминание",
    "про", "о", "об", "по", "с", "со", "ее", "её", "его", "их", "когда-то", "какую", "какая", "какой", "какие", "где", "что",
    "мою", "мои", "мой", "моя", "там", "то", "эту", "этот", "это", "раньше", "в", "на", "и", "а", "уже", "ли", "есть",
    "все", "всё", "всех", "событие", "событие", "дело", "был", "кажется", "вроде", "какую-то", "какой-то", "одну", "один",
    "перенеси", "перенести", "удали", "удалить", "убери", "отметь", "отметить", "как", "выполненную", "выполненной",
    "выполнено", "сделал", "сделала", "выполнил", "выполнила", "сдвинь", "отмени", "отменить", "добавлял", "создавал",
    "прошла", "прошел", "встречался", "встречалась", "сделано", "готово", "закрой", "закрыть", "пожалуйста", "можешь",
    "сможешь", "нужно", "надо", "нужна", "нужен", "мероприятие", "планы", "план", "запланировано", "запланированы",
    "расписание", "дела", "мое", "моё", "предстоит", "предстоящие", "намечено", "скажи", "расскажи", "покажите", "сколько",
    "чем", "занят", "занята", "свободен", "свободна", "список", "ну", "ещё", "еще", "кстати", "запланировал",
    "выполненным", "выполненная", "готовой", "сделанной", "сделанным", "что-то", "что-нибудь", "всё", "все",
    "какое-нибудь", "какую-нибудь", "что-либо", "измени", "измените", "изменить", "поменяй", "поменяйте", "время",
    "передвинь", "перенесите", "сдвиньте", "отложи",
}
# императивы, которые pymorphy иногда лемматизирует неверно
DELETE_FORMS = {"удали", "удалите", "удалить", "убери", "уберите", "сотри", "вычеркни", "удаляй"}
RESCHEDULE_FORMS = {"перенеси", "перенесите", "сдвинь", "передвинь", "отложи", "перенести"}
SEARCH_FORMS = {"найди", "найдите", "поищи", "поищите", "отыщи", "вспомни"}


def _type_from_lemmas(lems: set[str]) -> EventType | None:
    if lems & TASK_LEMMAS:
        return EventType.TASK
    if "напомнить" in lems or "напоминание" in lems:
        return EventType.REMINDER
    if lems & MEETING_LEMMAS:
        return EventType.MEETING
    return None


def _to_nominative(word: str) -> str:
    """«встречу» → «встреча» (для названия, начинающегося с существительного)."""
    for p in _morph.parse(word):
        if "NOUN" in p.tag and "accs" in p.tag and p.score > 0.1:
            nom = p.inflect({"nomn"})
            if nom:
                w = nom.word
                return w.capitalize() if word[:1].isupper() else w
    return word


def build_title(tokens: list[Token]) -> str | None:
    """Название = остаток фразы без даты, триггеров и вводных слов."""
    toks = [t for t in tokens if not t.used]
    # «в календарь», «в список» — где угодно
    k = 0
    while k + 1 < len(toks):
        if (toks[k].norm, toks[k + 1].norm) in PLACE_PHRASES:
            del toks[k:k + 2]
        else:
            k += 1
    while toks and (toks[0].norm in LEADING_FILLER or not toks[0].norm[0].isalnum()):
        toks.pop(0)
    while toks and (toks[-1].norm in TRAILING_FILLER or not toks[-1].norm[-1].isalnum()):
        toks.pop()
    if not toks:
        return None
    toks[0] = Token(_to_nominative(toks[0].orig), toks[0].norm)
    title = join_tokens(toks)
    return title[:1].upper() + title[1:] if title else None


def _is_stopword(word: str) -> bool:
    """Служебное слово — в том числе с опечаткой распознавания («меняй» вместо «меня»)."""
    if word in SEARCH_STOP:
        return True
    if len(word) < 4:
        return False
    return any(len(s) >= 4 and abs(len(s) - len(word)) <= 1 and SequenceMatcher(None, s, word).ratio() >= 0.85
               for s in SEARCH_STOP)


def search_query(tokens: list[Token]) -> str:
    words = [t.orig for t in tokens if not t.used and t.norm[0].isalnum() and not _is_stopword(t.norm)]
    return " ".join(words).strip()


# Ключевые слова, опечатки в которых исправляем до разбора: «напомин», «встрчу», «завтро», «найд».
TRIGGER_VOCAB = sorted({
    "напомни", "напомните", "напомнить", "найди", "найдите", "поищи", "перенеси", "перенести", "удали", "удалить",
    "покажи", "добавь", "поставь", "запланируй", "назначь", "создай", "запиши", "переименуй", "отметь",
    "встреча", "встречу", "встречи", "встреч", "встрече", "созвон", "совещание", "планёрка", "планерку", "планерка",
    "задача", "задачу", "задачи", "задач", "напоминание", "напоминания",
    "сегодня", "завтра", "послезавтра", "вчера", "неделе", "неделю", "следующей", "следующую", "выходных",
    "понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье",
    "запланировано", "расписание", "планы", "выполненной", "выполненным", "когда",
})


def _fix_typos(tokens: list[Token]) -> None:
    """Исправить опечатку в ключевом слове, если слова нет в словаре pymorphy."""
    for t in tokens:
        w = t.norm
        if len(w) < 4 or not w.isalpha() or w in TRIGGER_VOCAB or _morph.word_is_known(w):
            continue
        match = get_close_matches(w, TRIGGER_VOCAB, n=1, cutoff=0.8)
        if match:
            t.norm = match[0]
            t.orig = match[0].capitalize() if t.orig[:1].isupper() else match[0]


def detect_intent(norms: list[str], lems: list[str], raw: str) -> Intent:
    words = [w for w in norms if w[0].isalnum()]
    lemset = set(lems)
    if not words:
        return Intent.UNKNOWN
    first = words[0]

    # короткие ответы на уточняющие вопросы
    if len(words) <= 4 and (first in YES or " ".join(words[:3]) in ("все равно добавь", "все равно", "всё равно")):
        if not (lemset & (DELETE_LEMMAS | RESCHEDULE_LEMMAS)):
            return Intent.CONFIRM
    if len(words) <= 3 and (first in NO or words == ["не", "надо"]):
        return Intent.REJECT

    if lemset & {"помощь", "помочь", "уметь"} or "умеешь" in words:
        return Intent.HELP
    wordset = set(words)
    if lemset & DELETE_LEMMAS or wordset & DELETE_FORMS or (first in ("отмени", "отменить") and len(words) > 1):
        return Intent.DELETE
    if wordset & DONE_FORMS or ("отметить" in lemset and any(w.startswith("выполн") or w.startswith("сделан") for w in words)) \
            or (lemset & {"закрыть", "завершить"} and lemset & TASK_LEMMAS):
        return Intent.COMPLETE
    if lemset & RESCHEDULE_LEMMAS or wordset & RESCHEDULE_FORMS or ("изменить" in lemset and "время" in lemset):
        return Intent.RESCHEDULE
    if lemset & SEARCH_LEMMAS or wordset & SEARCH_FORMS or first == "когда" or "когда-то" in words:
        return Intent.SEARCH

    is_question = raw.strip().endswith("?") or first in QUESTION_STARTS or "ли" in words
    has_create = bool(lemset & CREATE_LEMMAS) or "напомнить" in lemset or first in ("нужно", "надо", "необходимо")
    if (lemset & LIST_LEMMAS and not (has_create and not is_question)) or (is_question and not has_create) \
            or (lemset & {"запланировать"} and is_question):
        return Intent.LIST
    if has_create or lemset & MEETING_LEMMAS or lemset & TASK_LEMMAS or wordset & {"нужно", "надо", "необходимо", "должен", "должна"}:
        return Intent.CREATE
    # «Купить молоко», «через два часа позвонить маме» — фраза с инфинитивом = задача
    if not is_question and any("INFN" in _morph.parse(w)[0].tag for w in words if w.isalpha()):
        return Intent.CREATE
    return Intent.UNKNOWN


RENAME_RE = re.compile(
    r"^(?:(?:а|и|ну|теперь)\s+)?(?:пожалуйста\s+)?"
    r"(?:переименуй(?:те)?|назови|(?:измени(?:те)?|поменяй(?:те)?|исправь(?:те)?)\s+(?:название|текст|формулировку))"
    r"(?:\s+(?P<rest>.*))?$",
    re.IGNORECASE,
)
NEW_TITLE_SPLIT = re.compile(r"(?:^|\s)(?:в|на)\s+", re.IGNORECASE)


def parse_rename(text: str) -> ParsedCommand | None:
    """«Переименуй её в Позвонить Петру», «Измени название встречи с Иваном на Созвон с Иваном»."""
    m = RENAME_RE.match(text.strip().rstrip(".!"))
    if not m:
        return None
    rest = (m.group("rest") or "").strip()
    split = NEW_TITLE_SPLIT.search(rest)
    target, new_title = (rest[:split.start()], rest[split.end():]) if split else (rest, "")
    new_title = new_title.strip(" «»\"'")
    cmd = ParsedCommand(intent=Intent.RENAME, source="rules")
    cmd.title = new_title[:1].upper() + new_title[1:] if new_title else None
    tokens = tokenize(target)
    type_words = {lemma(t.orig) for t in tokens if lemma(t.orig) in (TASK_LEMMAS | MEETING_LEMMAS | {"напоминание"})}
    cmd.event_type = _type_from_lemmas(type_words) if type_words else None
    cmd.query = search_query(tokens) or None
    cmd.refers_to_last = cmd.query is None
    return cmd


FOLLOW_UP_STARTS = {"а", "и", "ну", "тогда", "также", "ещё", "еще"}


def parse(text: str, now: datetime, context=None) -> ParsedCommand:
    """context — app.memory.DialogContext прошлого запроса (или None)."""
    renamed = parse_rename(text)
    if renamed:
        return renamed
    tokens = tokenize(text)
    _fix_typos(tokens)
    norms = [t.norm for t in tokens]
    lems = [lemma(t.orig) for t in tokens]
    intent = detect_intent(norms, lems, text)
    lemset = set(lems)
    words = set(norms)

    tense = None
    if words & PAST_MARKERS:
        tense = "past"
    elif words & FUTURE_MARKERS:
        tense = "future"

    info = dates.extract(tokens, now, prefer_past=tense == "past", future_bias=intent is Intent.CREATE)
    if intent is Intent.CREATE and info.weekday_is_today and info.time and info.time <= now.time():
        # «в четверг в 11», сказанное в четверг в 11:20, — это следующий четверг
        info.date += timedelta(days=7)
    cmd = ParsedCommand(intent=intent, tense=tense, source="rules")
    cmd.date, cmd.time, cmd.period = info.date, info.time, info.period
    cmd.has_deadline, cmd.recurrence = info.deadline, info.recurrence
    if info.duration_min:
        cmd.extras["duration_min"] = info.duration_min

    if intent is Intent.CREATE:
        cmd.event_type = _type_from_lemmas(lemset) or EventType.TASK
        if cmd.event_type is EventType.REMINDER and lemset & MEETING_LEMMAS and "напомнить" not in lemset:
            cmd.event_type = EventType.MEETING
        cmd.title = build_title(tokens)
        if cmd.event_type is EventType.MEETING and cmd.title and lemma(cmd.title.split()[0]) not in MEETING_LEMMAS:
            # «назначь с Иваном на завтра» → «Встреча с Иваном»
            if cmd.title.lower().startswith(("с ", "со ")):
                cmd.title = "Встреча " + cmd.title
    elif intent in (Intent.SEARCH, Intent.LIST, Intent.DELETE, Intent.COMPLETE, Intent.RESCHEDULE):
        content = [t for t in tokens if not t.used and t.norm[0].isalnum()]
        type_words = {t.norm for t in content if lemma(t.orig) in (TASK_LEMMAS | MEETING_LEMMAS | {"напоминание"})}
        cmd.event_type = _type_from_lemmas({lemma(w) for w in type_words}) if type_words else None
        if intent is Intent.LIST:
            # «задачи» / «встречи» в запросе списка — это фильтр, а не текст
            for t in tokens:
                if t.norm in type_words:
                    t.used = True
        cmd.query = search_query(tokens) or None
        if cmd.query and cmd.event_type and lemma(cmd.query) in (TASK_LEMMAS | MEETING_LEMMAS):
            cmd.query = None
        if intent in (Intent.DELETE, Intent.COMPLETE, Intent.RESCHEDULE):
            content_words = [t.norm for t in tokens if not t.used and t.norm[0].isalnum() and not _is_stopword(t.norm)]
            cmd.refers_to_last = bool(words & PRONOUN_REFS) and not content_words
            if not content_words and not cmd.query:
                cmd.refers_to_last = True
    return _apply_context(cmd, norms, text, context)


def _apply_context(cmd: ParsedCommand, norms: list[str], text: str, context) -> ParsedCommand:
    """Короткая реплика продолжает прошлый запрос списка или поиска.

    «Что у меня сегодня?» → «а на завтра?»     — тот же список, другой период;
    «Какие встречи на неделе?» → «а задачи?»   — тот же период, другой тип;
    «Когда была встреча с Иваном?» → «а с Марией?» — тот же поиск, другой человек.
    """
    if context is None or context.intent not in (Intent.LIST, Intent.SEARCH):
        return cmd
    words = [w for w in norms if w[0].isalnum()]
    follow_up = bool(words) and words[0] in FOLLOW_UP_STARTS
    if cmd.intent not in (Intent.UNKNOWN, Intent.LIST, Intent.SEARCH) or len(words) > 6:
        return cmd
    if cmd.intent is not Intent.UNKNOWN and not follow_up:
        return cmd
    new_period = cmd.period or (Period(cmd.date, cmd.date, "") if cmd.date else None)
    query = cmd.query
    if cmd.intent is Intent.UNKNOWN and not query:
        # «а с Марией» — без предлогов в начале остатка уже не осталось
        query = " ".join(w for w in words if w not in FOLLOW_UP_STARTS and not _is_stopword(w)) or None
    if not (new_period or cmd.event_type or query):
        return cmd
    merged = ParsedCommand(intent=context.intent, source=cmd.source, tense=cmd.tense or context.tense)
    merged.event_type = cmd.event_type or (context.event_type if not query or context.intent is Intent.SEARCH else None)
    merged.period = new_period or context.period
    merged.date = cmd.date
    merged.query = query if query else (context.query if not cmd.event_type else None)
    if context.intent is Intent.SEARCH and not query and not new_period:
        merged.query = context.query
    merged.extras = {**cmd.extras, "from_context": True}
    return merged
