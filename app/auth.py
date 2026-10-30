"""Аккаунты: регистрация, вход, сессии.

Пароли хранятся только как scrypt-хэш с солью. Браузер получает случайный
токен в HttpOnly-cookie, в базе лежит лишь его SHA-256 — утечка базы не
даёт войти в чужой аккаунт. Без входа работает гостевой режим (X-User-Id).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
SESSION_DAYS_REMEMBER = 30
SESSION_HOURS_SHORT = 24
ACCOUNT_PREFIX = "acc-"          # id данных пользователя с аккаунтом; гостям такой id запрещён

# Защита от перебора пароля: не больше 8 неудачных попыток на почту за 15 минут
MAX_FAILS, FAIL_WINDOW_S = 8, 15 * 60
_fails: dict[str, list[float]] = {}


@dataclass
class Account:
    id: int
    name: str
    email: str

    @property
    def data_id(self) -> str:
        return f"{ACCOUNT_PREFIX}{self.id}"

    @property
    def initials(self) -> str:
        parts = [p for p in re.split(r"[\s.]+", self.name) if p]
        return ("".join(p[0] for p in parts[:2]) or self.email[0]).upper()

    def to_dict(self) -> dict:
        return {"authenticated": True, "name": self.name, "email": self.email,
                "initials": self.initials, "user_id": self.data_id}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return hmac.compare_digest(digest.hex(), digest_hex)


def password_problems(password: str) -> list[str]:
    """Требования из макета регистрации."""
    problems = []
    if len(password) < 8:
        problems.append("минимум 8 символов")
    if not re.search(r"\d", password):
        problems.append("цифры")
    if not (re.search(r"[a-zа-яё]", password) and re.search(r"[A-ZА-ЯЁ]", password)):
        problems.append("заглавные и строчные буквы")
    if not re.search(r"[^\w\s]", password):
        problems.append("специальный символ")
    return problems


def normalize_email(email: str) -> str:
    return email.strip().lower()


def valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(email))


def new_token() -> tuple[str, str]:
    """(токен для cookie, его хэш для базы)."""
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def too_many_failures(email: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _fails.get(email, []) if now - t < FAIL_WINDOW_S]
    _fails[email] = recent
    return len(recent) >= MAX_FAILS


def register_failure(email: str) -> None:
    _fails.setdefault(email, []).append(time.monotonic())


def clear_failures(email: str) -> None:
    _fails.pop(email, None)
