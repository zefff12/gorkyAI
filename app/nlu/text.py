"""Токенизация, числительные словами и лемматизация русского текста."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import pymorphy3

_morph = pymorphy3.MorphAnalyzer()

TOKEN_RE = re.compile(r"\d{1,2}[:.]\d{2}(?:[.]\d{2,4})?|\d+|[^\W\d_]+(?:-[^\W\d_]+)*|\S", re.UNICODE)

UNITS = {
    "ноль": 0, "один": 1, "одна": 1, "одну": 1, "одного": 1, "первого": 1,
    "два": 2, "две": 2, "двух": 2, "три": 3, "трех": 3, "четыре": 4, "четырех": 4,
    "пять": 5, "пяти": 5, "шесть": 6, "шести": 6, "семь": 7, "семи": 7,
    "восемь": 8, "восьми": 8, "девять": 9, "девяти": 9,
}
TEENS = {
    "десять": 10, "десяти": 10, "одиннадцать": 11, "одиннадцати": 11, "двенадцать": 12, "двенадцати": 12,
    "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "пятнадцати": 15, "шестнадцать": 16,
    "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
}
ORDINALS_GEN = {
    "первого": 1, "второго": 2, "третьего": 3, "четвертого": 4, "пятого": 5, "шестого": 6, "седьмого": 7,
    "восьмого": 8, "девятого": 9, "десятого": 10, "одиннадцатого": 11, "двенадцатого": 12, "тринадцатого": 13,
    "четырнадцатого": 14, "пятнадцатого": 15, "шестнадцатого": 16, "семнадцатого": 17, "восемнадцатого": 18,
    "девятнадцатого": 19, "двадцатого": 20, "тридцатого": 30,
}
TENS = {"двадцать": 20, "двадцати": 20, "тридцать": 30, "тридцати": 30, "сорок": 40, "пятьдесят": 50}


MONTH_NAMES = {"января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
               "ноября", "декабря"}


@dataclass
class Token:
    orig: str
    norm: str
    used: bool = False

    @property
    def is_num(self) -> bool:
        return self.norm.isdigit()


def norm(word: str) -> str:
    return word.lower().replace("ё", "е")


def tokenize(text: str) -> list[Token]:
    tokens = [Token(t, norm(t)) for t in TOKEN_RE.findall(text)]
    return _merge_numerals(tokens)


def _merge_numerals(tokens: list[Token]) -> list[Token]:
    """«двадцать три» → один токен с norm="23"; «пятнадцать» → "15"."""
    out: list[Token] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.norm in TENS:
            value, orig = TENS[t.norm], t.orig
            nxt = tokens[i + 1].norm if i + 1 < len(tokens) else ""
            if UNITS.get(nxt, 0) > 0 or 0 < ORDINALS_GEN.get(nxt, 0) < 10:
                value += UNITS.get(nxt) or ORDINALS_GEN[nxt]
                orig += " " + tokens[i + 1].orig
                i += 1
            out.append(Token(orig, str(value)))
        elif t.norm in ORDINALS_GEN and i + 1 < len(tokens) and (tokens[i + 1].norm in MONTH_NAMES or tokens[i + 1].norm == "числа"):
            out.append(Token(t.orig, str(ORDINALS_GEN[t.norm])))
        elif t.norm in TEENS:
            out.append(Token(t.orig, str(TEENS[t.norm])))
        elif t.norm in UNITS and t.norm not in ("одна", "одну", "одного", "первого"):
            out.append(Token(t.orig, str(UNITS[t.norm])))
        else:
            out.append(t)
        i += 1
    return out


@lru_cache(maxsize=50_000)
def lemma(word: str) -> str:
    w = norm(word)
    if not w.isalpha():
        return w
    return norm(_morph.parse(w)[0].normal_form)


def lemmas(text: str) -> list[str]:
    return [lemma(t.orig) for t in tokenize(text) if t.norm[0].isalnum()]


def join_tokens(tokens: list[Token]) -> str:
    """Склеивает неиспользованные токены обратно в текст."""
    text = " ".join(t.orig for t in tokens if not t.used)
    text = re.sub(r"\s+([,.!?:;])", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" ,.;:-–—!?")
