"""Гибридный поиск по старым записям.

Лексический слой: леммы + синонимы + нечёткое совпадение + BM25-веса.
Семантический слой (необязательный, app/semantic.py): эмбеддинги предложений
добавляют записи, близкие по смыслу, но без общих слов с запросом.

Почему не точное совпадение: в речи «договору», «договор» и «контракт» —
одно и то же, а распознавание речи иногда ошибается в окончаниях и буквах.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher

from .models import Event, EventType, Period
from .nlu.text import lemmas

SYNONYM_GROUPS = [
    {"презентация", "слайд", "питч", "деко", "презентовать"},
    {"договор", "контракт", "соглашение", "договорённость"},
    {"встреча", "созвон", "совещание", "митинг", "планёрка", "планерка", "собрание", "конференция", "встретиться", "созвониться", "переговоры"},
    {"позвонить", "звонок", "набрать", "созвониться", "звонить"},
    {"отчёт", "отчет", "отчётность", "доклад", "репорт"},
    {"смета", "бюджет", "расход", "финансы", "деньги", "стоимость"},
    {"тз", "техзадание"},
    {"crm", "црм", "срм"},
    {"сайт", "лендинг", "веб-сайт", "портал"},
    {"приложение", "апп", "мобилка"},
    {"аналитика", "метрика", "статистика", "дашборд", "отчётность"},
    {"согласовать", "утвердить", "согласование", "одобрить"},
    {"демо", "демонстрация", "показ", "показать"},
    {"кп", "оффер"},
    {"обратный", "фидбек", "фидбэк", "отзыв"},
    {"протокол", "итог", "резюме", "саммари"},
    {"купить", "покупка", "приобрести", "заказать"},
    {"билет", "авиабилет", "поездка"},
]
SYNONYMS: dict[str, set[str]] = {}
for group in SYNONYM_GROUPS:
    for word in group:
        SYNONYMS.setdefault(word, set()).update(group - {word})

# составные термины: «техническое задание» ≈ «тз», «коммерческое предложение» ≈ «кп»
PHRASES = {("технический", "задание"): "тз", ("коммерческий", "предложение"): "кп", ("обратный", "связь"): "обратный"}

STOP_LEMMAS = {"с", "по", "для", "на", "в", "к", "о", "об", "про", "и", "а", "у", "я", "мой", "это", "тот", "этот", "который",
               "весь", "что", "какой", "когда", "быть", "ставить", "найти", "задача", "запись", "мочь", "свой", "он", "она", "они"}
TYPE_WORDS = {"задача": EventType.TASK, "напоминание": EventType.REMINDER}


def _terms(text: str) -> list[str]:
    lem = lemmas(text)
    out = []
    i = 0
    while i < len(lem):
        if i + 1 < len(lem) and (lem[i], lem[i + 1]) in PHRASES:
            out.append(PHRASES[(lem[i], lem[i + 1])])
            i += 2
            continue
        if lem[i] not in STOP_LEMMAS:
            out.append(lem[i])
        i += 1
    return out


def _doc_terms(ev: Event) -> set[str]:
    terms = set(_terms(ev.title))
    # тип записи тоже ищется: «встреча с Иваном» найдёт «Созвон с Иваном»
    terms.add({EventType.MEETING: "встреча", EventType.TASK: "задача", EventType.REMINDER: "напоминание"}[ev.type])
    return terms


@dataclass
class Hit:
    event: Event
    score: float


def _fuzzy(a: str, b: str) -> bool:
    if len(a) < 5 or len(b) < 5 or a[:2] != b[:2]:
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.84


def search(
    events: list[Event],
    query: str,
    today: date,
    event_type: EventType | None = None,
    tense: str | None = None,
    period: Period | None = None,
    limit: int = 10,
    embedder=None,
) -> list[Hit]:
    # если тип уже фильтрует встречи, слово «встреча» в запросе ничего не добавляет
    q_terms = [t for t in _terms(query) if not (t == "встреча" and event_type is EventType.MEETING)]
    candidates = []
    for ev in events:
        if event_type is EventType.MEETING and ev.type is not EventType.MEETING:
            continue
        if event_type in (EventType.TASK, EventType.REMINDER) and ev.type is EventType.MEETING:
            continue
        if period and (ev.date is None or not (period.start <= ev.date <= period.end)):
            continue
        if tense == "past" and (ev.date is None or ev.date > today):
            continue
        if tense == "future" and ev.date is not None and ev.date < today:
            continue
        candidates.append((ev, _doc_terms(ev)))
    if not candidates:
        return []
    if not q_terms:
        hits = [Hit(ev, 1.0) for ev, _ in candidates]
        return _order(hits, today, tense)[:limit]

    df = Counter(t for _, terms in candidates for t in terms)
    n_docs = len(candidates)

    def idf(term: str) -> float:
        return math.log(1 + (n_docs - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))

    hits: list[Hit] = []
    for ev, terms in candidates:
        score = 0.0
        matched = 0
        for qt in q_terms:
            if qt in terms:
                score += idf(qt); matched += 1
            elif SYNONYMS.get(qt, set()) & terms:
                syn = next(iter(SYNONYMS[qt] & terms))
                score += 0.7 * idf(syn); matched += 1
            elif any(_fuzzy(qt, t) for t in terms):
                score += 0.5 * idf(qt); matched += 1
        if matched:
            # бонус за совпадение всех слов запроса
            score *= 1 + 0.5 * (matched / len(q_terms) == 1)
            hits.append(Hit(ev, score))
    if hits:
        best = max(h.score for h in hits)
        hits = [h for h in hits if h.score >= best * 0.6]
    hits += _semantic_extra(query, candidates, hits, embedder)
    return _order(hits, today, tense)[:limit]


SEM_ONLY_THRESHOLD = 0.5       # если слова не совпали ни с одной записью
SEM_EXTRA_THRESHOLD = 0.7      # дополнительно к лексическим совпадениям — только очень близкие


def _semantic_extra(query: str, candidates, lexical: list[Hit], embedder) -> list[Hit]:
    """Записи, найденные только по смыслу. Ранжируются ниже лексических совпадений,
    а их оценка округляется, чтобы среди похожих работала сортировка по дате."""
    if embedder is None or not getattr(embedder, "ready", False):
        return []
    found = {h.event.id for h in lexical}
    rest = [ev for ev, _ in candidates if ev.id not in found]
    sims = embedder.similarities(query, [ev.title for ev in rest])
    if not sims:
        return []
    threshold = SEM_EXTRA_THRESHOLD if lexical else SEM_ONLY_THRESHOLD
    ceiling = min(h.score for h in lexical) * 0.99 if lexical else 1.0
    extra = []
    for ev, s in zip(rest, sims):
        if s >= threshold:
            extra.append(Hit(ev, round(s, 1) * ceiling))
    if extra and not lexical:
        top = max(h.score for h in extra)
        extra = [h for h in extra if h.score >= top - 0.1]
    return extra


def _order(hits: list[Hit], today: date, tense: str | None) -> list[Hit]:
    """Сначала самые релевантные; среди равных — ближайшие ко «сегодня»."""
    def key(h: Hit):
        d = h.event.date
        if d is None:
            distance = 10_000
        elif tense == "past":
            distance = (today - d).days
        elif d >= today:
            distance = (d - today).days
        else:
            distance = 5_000 + (today - d).days   # прошлые — после будущих
        return (-round(h.score, 3), distance)
    return sorted(hits, key=key)
