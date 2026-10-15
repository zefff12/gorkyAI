from collections import Counter

from fastapi.testclient import TestClient

from app.main import app
from app.models import EventType
from app.seed import classify, fix_grammar, load_csv
from conftest import CSV

client = TestClient(app)


def test_seed_import():
    events = load_csv(CSV)
    assert len(events) == 1000
    types = Counter(e.type for e in events)
    assert types[EventType.MEETING] > 0 and types[EventType.TASK] > 0 and types[EventType.REMINDER] > 0
    assert all(e.date and e.time for e in events)


def test_seed_classification():
    assert classify("Созвон с Альфой по интеграции") is EventType.MEETING
    assert classify("Провести планёрку по CRM") is EventType.MEETING
    assert classify("Обсудить договору с Сергеем") is EventType.MEETING
    assert classify("Напомнить Анной про CRM") is EventType.REMINDER
    assert classify("Подготовиться к встрече с Вектором") is EventType.TASK


def test_seed_grammar_fixes():
    assert fix_grammar("Позвонить Иваном") == "Позвонить Ивану"
    assert fix_grammar("Отправить итоги встречи Марией") == "Отправить итоги встречи Марии"
    assert fix_grammar("Напомнить Дмитрием про мобильному приложению") == "Напомнить Дмитрию про мобильное приложение"
    assert fix_grammar("Подготовить вопросы для Натальей") == "Подготовить вопросы для Натальи"
    assert fix_grammar("Обсудить хакатону с Еленой") == "Обсудить хакатон с Еленой"
    assert fix_grammar("Проверить сроки по сайту") == "Проверить сроки по сайту"


def test_api_message_flow():
    h = {"X-User-Id": "api_user"}
    r = client.post("/api/message", json={"text": "Поставь встречу с Иваном завтра", "session_id": "s1"}, headers=h)
    assert r.status_code == 200 and r.json()["awaiting"] == "need_time"
    r = client.post("/api/message", json={"text": "в 12", "session_id": "s1"}, headers=h)
    body = r.json()
    assert body["events"][0]["time"] == "12:00" and body["speech"]
    events = client.get("/api/events", headers=h).json()
    assert any(e["title"] == "Встреча с Иваном" for e in events)


def test_api_health_and_validation():
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.post("/api/message", json={"text": "привет"}, headers={"X-User-Id": "bad id!"}).status_code == 400
    assert client.get("/").status_code == 200


def test_api_demo_data_only_into_empty_list():
    h = {"X-User-Id": "demo_loader"}
    assert client.get("/api/stats", headers=h).json() == {"total": 0}
    assert client.post("/api/demo-data", headers=h).json() == {"imported": 1000}
    assert client.get("/api/stats", headers=h).json() == {"total": 1000}
    assert client.post("/api/demo-data", headers=h).status_code == 409
    assert client.get("/api/stats", headers={"X-User-Id": "someone_else"}).json() == {"total": 0}
    assert client.post("/api/reset", headers=h).json()["deleted"] == 1000


def test_no_demo_data_loaded_by_default():
    assert client.get("/api/stats", headers={"X-User-Id": "demo"}).json() == {"total": 0}


def test_api_reset():
    h = {"X-User-Id": "reset_user"}
    client.post("/api/message", json={"text": "Купить молоко"}, headers=h)
    client.post("/api/message", json={"text": "без срока"}, headers=h)
    client.post("/api/reset", headers=h)
    r = client.post("/api/message", json={"text": "Найди задачу про молоко"}, headers=h)
    assert "Не нашёл" in r.json()["text"]
