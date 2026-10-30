"""Аккаунты (вход/регистрация из макета), выборка всех задач по датам, категории."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.categories import category_of_title
from app.main import app

GOOD_PASSWORD = "Secret#2026"


def new_client():
    return TestClient(app)


def register(client, email, name="Иванова Ирина", password=GOOD_PASSWORD, headers=None, **extra):
    body = {"name": name, "email": email, "password": password, "password2": password, "accept": True} | extra
    return client.post("/api/auth/register", json=body, headers=headers or {})


# ------------------------------------------------------------------ пароли

def test_password_hash_roundtrip():
    h = auth.hash_password(GOOD_PASSWORD)
    assert h.startswith("scrypt$") and GOOD_PASSWORD not in h
    assert auth.verify_password(GOOD_PASSWORD, h)
    assert not auth.verify_password("wrong", h)
    assert auth.hash_password(GOOD_PASSWORD) != h          # соль разная


@pytest.mark.parametrize("pw, problem", [
    ("Ab#1", "минимум 8 символов"), ("Abcdefg#", "цифры"), ("abcdefg#1", "заглавные и строчные буквы"),
    ("Abcdefg12", "специальный символ"),
])
def test_password_rules_from_design(pw, problem):
    assert problem in auth.password_problems(pw)


def test_initials():
    assert auth.Account(1, "Иванова Ирина", "a@b.ru").initials == "ИИ"
    assert auth.Account(1, "Иванова И.И.", "a@b.ru").initials == "ИИ"


# ------------------------------------------------------------------ регистрация и вход

def test_register_login_logout_flow():
    c = new_client()
    r = register(c, "flow@example.com")
    assert r.status_code == 200 and r.json()["initials"] == "ИИ"
    assert "va_session" in r.cookies
    assert c.get("/api/auth/me").json()["email"] == "flow@example.com"
    c.post("/api/message", json={"text": "Добавь встречу с командой завтра в 15:00"})
    c.post("/api/auth/logout")
    assert c.get("/api/auth/me").json() == {"authenticated": False}

    other = new_client()
    assert other.post("/api/auth/login", json={"email": "FLOW@example.com", "password": "nope"}).status_code == 401
    r = other.post("/api/auth/login", json={"email": "FLOW@example.com", "password": GOOD_PASSWORD, "remember": True})
    assert r.status_code == 200
    titles = [e["title"] for e in other.get("/api/items").json()]
    assert titles == ["Встреча с командой"]                 # данные аккаунта доступны с другого устройства


def test_register_validation_errors():
    c = new_client()
    r = c.post("/api/auth/register", json={"name": "", "email": "bad", "password": "short", "password2": "x", "accept": False})
    errors = r.json()["detail"]["errors"]
    assert r.status_code == 422 and {"name", "email", "password", "accept"} <= set(errors)
    r = register(c, "mismatch@example.com", password2="Other#2026")
    assert r.json()["detail"]["errors"]["password2"] == "Пароли не совпадают"


def test_duplicate_email():
    assert register(new_client(), "dup@example.com").status_code == 200
    r = register(new_client(), "Dup@Example.com")
    assert r.status_code == 409 and "email" in r.json()["detail"]["errors"]


def test_guest_data_moves_to_new_account():
    c = new_client()
    guest = {"X-User-Id": "u-guestmove"}
    c.post("/api/message", json={"text": "Напомни завтра в 10 позвонить Ивану"}, headers=guest)
    r = register(c, "move@example.com", headers=guest)
    assert r.json()["moved"] == 1
    assert [e["title"] for e in c.get("/api/items").json()] == ["Позвонить Ивану"]
    assert c.get("/api/history").json()                      # история разговора тоже переехала
    assert new_client().get("/api/stats", headers=guest).json() == {"total": 0}


def test_accounts_are_isolated_and_guest_cannot_impersonate():
    a, b = new_client(), new_client()
    register(a, "iso-a@example.com")
    register(b, "iso-b@example.com")
    a.post("/api/message", json={"text": "Купить молоко"})
    a.post("/api/message", json={"text": "без срока"})
    assert b.get("/api/items").json() == []
    acc_id = a.get("/api/auth/me").json()["user_id"]
    r = new_client().get("/api/items", headers={"X-User-Id": acc_id})
    assert r.status_code == 400                               # «acc-…» нельзя подставить заголовком


def test_login_rate_limit():
    c = new_client()
    register(c, "brute@example.com")
    for _ in range(auth.MAX_FAILS):
        c.post("/api/auth/login", json={"email": "brute@example.com", "password": "wrong"})
    r = c.post("/api/auth/login", json={"email": "brute@example.com", "password": GOOD_PASSWORD})
    assert r.status_code == 429


def test_pages_are_served():
    c = new_client()
    for path in ("/", "/login", "/register"):
        assert c.get(path).status_code == 200


# ------------------------------------------------------------------ все задачи по датам

def test_items_filters_by_dates_types_status():
    c = new_client()
    h = {"X-User-Id": "u-items"}
    for text in ["Добавь встречу с командой 20 ноября в 15:00", "Поставь задачу сдать отчёт 5 декабря в 10",
                 "Напомни 25 ноября в 9 позвонить Ивану", "Купить молоко", "без срока"]:
        c.post("/api/message", json={"text": text}, headers=h)
    everything = c.get("/api/items", headers=h).json()
    assert len(everything) == 4 and everything[-1]["date"] is None      # без срока — в конце

    nov = c.get("/api/items", params={"start": "2026-11-01", "end": "2026-11-30", "undated": False}, headers=h).json()
    assert [e["title"] for e in nov] == ["Встреча с командой", "Позвонить Ивану"]

    meetings = c.get("/api/items", params={"types": "meeting"}, headers=h).json()
    assert [e["type"] for e in meetings] == ["meeting"]

    report = c.get("/api/items", params={"q": "отчёт"}, headers=h).json()
    assert [e["title"] for e in report] == ["Сдать отчёт"]

    eid = report[0]["id"]
    c.patch(f"/api/events/{eid}", json={"done": True}, headers=h)
    done = c.get("/api/items", params={"status": "done"}, headers=h).json()
    assert [e["id"] for e in done] == [eid]
    assert eid not in [e["id"] for e in c.get("/api/items", params={"status": "active"}, headers=h).json()]


def test_items_expands_recurring_in_range():
    c = new_client()
    h = {"X-User-Id": "u-recur"}
    c.post("/api/message", json={"text": "Каждый понедельник в 10 планёрка с командой"}, headers=h)
    nov = c.get("/api/items", params={"start": "2026-11-01", "end": "2026-11-30"}, headers=h).json()
    assert len(nov) == 5                                       # 2, 9, 16, 23, 30 ноября


def test_quick_search():
    c = new_client()
    h = {"X-User-Id": "u-qs"}
    c.post("/api/message", json={"text": "Добавь встречу с дизайнером завтра в 11"}, headers=h)
    hits = c.get("/api/search", params={"q": "дизайнер"}, headers=h).json()
    assert hits and hits[0]["title"] == "Встреча с дизайнером"


# ------------------------------------------------------------------ категории для иконок

@pytest.mark.parametrize("title, etype, expected", [
    ("Созвон с Иваном", "meeting", "call"), ("Позвонить Ивану", "reminder", "call"),
    ("Встреча с командой", "meeting", "team"), ("Обсуждение проекта", "meeting", "discussion"),
    ("Планирование спринта", "meeting", "sprint"), ("Вечеринка у Пети", "meeting", "party"),
    ("День рождения мамы", "task", "birthday"), ("Йога", "task", "yoga"), ("Сдать отчёт", "task", "report"),
    ("Купить хлеб", "task", None), ("Встреча с дизайнером", "meeting", "team"),
])
def test_categories(title, etype, expected):
    assert category_of_title(title, etype) == expected


def test_event_dict_has_category():
    c = new_client()
    h = {"X-User-Id": "u-cat"}
    r = c.post("/api/message", json={"text": "Добавь созвон с Иваном завтра в 12"}, headers=h).json()
    assert r["events"][0]["category"] == "call"
