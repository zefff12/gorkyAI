// Клиент: голос (Web Speech API) → /api/message → ответ текстом и голосом.
// Распознавание и синтез речи выполняет браузер; сервер получает только текст.
(() => {
  const $ = (id) => document.getElementById(id);
  const log = $("log"), input = $("input"), mic = $("mic"), statusEl = $("status");
  const speakToggle = $("speakToggle"), userInput = $("userId");
  const sessionId = Math.random().toString(36).slice(2);

  const store = {
    get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch {} },
  };
  // У каждого браузера свой случайный ID — посетители не видят записей друг друга
  const newId = () => "u-" + (crypto.randomUUID?.() || Math.random().toString(36).slice(2) + Date.now().toString(36)).replace(/-/g, "").slice(0, 12);
  let savedId = store.get("userId", "");
  if (!savedId) { savedId = newId(); store.set("userId", savedId); }
  userInput.value = savedId;
  speakToggle.checked = store.get("speak", "1") === "1";
  speakToggle.onchange = () => { store.set("speak", speakToggle.checked ? "1" : "0"); if (!speakToggle.checked) speechSynthesis?.cancel(); };
  const userId = () => userInput.value.trim() || savedId;
  userInput.onchange = () => { store.set("userId", userId()); log.innerHTML = ""; greet(); loadAgenda(); };

  // ---------- чат ----------
  function addMsg(text, who, extra = "") {
    const el = document.createElement("div");
    el.className = `msg ${who} ${extra}`.trim();
    el.textContent = text;
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
    return el;
  }

  async function send(text) {
    text = text.trim();
    if (!text) return;
    addMsg(text, "user");
    input.value = "";
    statusEl.textContent = "Думаю…";
    try {
      const res = await fetch("/api/message", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-User-Id": userId() },
        body: JSON.stringify({ text, session_id: sessionId }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const el = addMsg(data.text, "bot", data.awaiting ? "ask" : "");
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = `${data.intent}${data.parser ? " · " + data.parser : ""}`;
      el.appendChild(tag);
      speak(data.speech);
      statusEl.textContent = data.awaiting ? "Жду уточнения — ответьте голосом или текстом" : "Готов";
      loadAgenda(data.events.map((e) => e.id));
    } catch (err) {
      addMsg("Не удалось связаться с сервером. Проверьте, что он запущен.", "bot");
      statusEl.textContent = "Ошибка соединения";
    }
  }

  $("form").onsubmit = (e) => { e.preventDefault(); send(input.value); };
  $("chips").onclick = (e) => { if (e.target.tagName === "BUTTON") send(e.target.textContent); };

  // ---------- синтез речи ----------
  let ruVoice = null;
  function pickVoice() {
    const voices = window.speechSynthesis?.getVoices() || [];
    ruVoice = voices.find((v) => v.lang?.startsWith("ru") && /google|natural|online/i.test(v.name))
      || voices.find((v) => v.lang?.startsWith("ru")) || null;
  }
  if ("speechSynthesis" in window) { pickVoice(); speechSynthesis.onvoiceschanged = pickVoice; }

  function speak(text) {
    if (!speakToggle.checked || !("speechSynthesis" in window) || !text) return;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text.replace(/[«»•]/g, ""));
    u.lang = "ru-RU";
    if (ruVoice) u.voice = ruVoice;
    u.rate = 1.05;
    speechSynthesis.speak(u);
  }

  // ---------- распознавание речи ----------
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let rec = null, recording = false, interimEl = null;
  if (!SR) {
    mic.disabled = true;
    mic.title = "Браузер не поддерживает распознавание речи — используйте Chrome, Edge или Safari, либо пишите текстом";
  } else {
    rec = new SR();
    rec.lang = "ru-RU";
    rec.interimResults = true;
    rec.continuous = false;
    rec.onstart = () => { recording = true; mic.classList.add("recording"); statusEl.textContent = "Слушаю…"; speechSynthesis?.cancel(); };
    rec.onend = () => {
      recording = false; mic.classList.remove("recording");
      if (interimEl) { interimEl.remove(); interimEl = null; }
      if (statusEl.textContent === "Слушаю…") statusEl.textContent = "Готов";
    };
    rec.onerror = (e) => {
      const msg = { "not-allowed": "Нет доступа к микрофону — разрешите его в настройках браузера",
                    "no-speech": "Ничего не услышал, попробуйте ещё раз",
                    "network": "Распознавание речи недоступно без интернета" }[e.error] || `Ошибка распознавания: ${e.error}`;
      statusEl.textContent = msg;
    };
    rec.onresult = (e) => {
      let interim = "", final = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        (r.isFinal ? (final += r[0].transcript) : (interim += r[0].transcript));
      }
      if (interim) {
        interimEl = interimEl || addMsg("", "user", "interim");
        interimEl.textContent = interim;
      }
      if (final) {
        if (interimEl) { interimEl.remove(); interimEl = null; }
        send(final);
      }
    };
    mic.onclick = () => (recording ? rec.stop() : rec.start());
    // пробел с зажатым Ctrl — быстрый старт записи
    document.addEventListener("keydown", (e) => { if (e.ctrlKey && e.code === "Space") { e.preventDefault(); mic.click(); } });
  }

  // ---------- боковая панель ----------
  const WD = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
  const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];

  async function loadAgenda(highlight = []) {
    const box = $("agenda");
    try {
      const res = await fetch("/api/events", { headers: { "X-User-Id": userId() } });
      const events = await res.json();
      box.innerHTML = "";
      $("demoData").hidden = !!events.length || !(await hasNoRecords());
      if (!events.length) { box.innerHTML = '<p class="empty">Ничего не запланировано.</p>'; return; }
      let day = null;
      for (const e of events) {
        if (e.date !== day) {
          day = e.date;
          const d = new Date(e.date + "T00:00");
          const h = document.createElement("div");
          h.className = "day";
          h.textContent = `${WD[d.getDay()]}, ${d.getDate()} ${MONTHS[d.getMonth()]}`;
          box.appendChild(h);
        }
        const row = document.createElement("div");
        row.className = `ev ${e.type}${e.done ? " done" : ""}${highlight.includes(e.id) ? " flash" : ""}`;
        const t = document.createElement("time");
        t.textContent = e.time || "—";
        const title = document.createElement("span");
        title.className = "t";
        title.textContent = e.title + (e.recurrence ? " ↻" : "");
        row.append(t, title);
        box.appendChild(row);
      }
    } catch { box.innerHTML = '<p class="empty">Не удалось загрузить.</p>'; }
  }

  async function hasNoRecords() {
    try {
      const res = await fetch("/api/stats", { headers: { "X-User-Id": userId() } });
      return res.ok && (await res.json()).total === 0;
    } catch { return false; }
  }

  $("reset").onclick = async () => {
    if (!confirm("Удалить все ваши задачи, напоминания и встречи?")) return;
    await fetch("/api/reset", { method: "POST", headers: { "X-User-Id": userId() } });
    log.innerHTML = ""; greet(); loadAgenda();
  };

  $("demoData").onclick = async () => {
    const res = await fetch("/api/demo-data", { method: "POST", headers: { "X-User-Id": userId() } });
    const data = await res.json();
    addMsg(res.ok ? `Загрузил ${data.imported} тестовых записей из задания. Попробуйте: «Что у меня на этой неделе?» или «Когда у меня была встреча с Иваном?»` : data.detail, "bot");
    loadAgenda();
  };

  async function greet() {
    addMsg("Привет! Я веду задачи, напоминания и встречи. Скажите или напишите, что нужно сделать.", "bot");
    try {
      const h = await (await fetch("/api/health")).json();
      $("meta").textContent = `Понимание запросов: ${h.nlu === "rules" ? "встроенные правила" : h.nlu} · `
        + `поиск: ${h.semantic_search ? "лексический + семантический" : "лексический"} · сейчас ${h.now.replace("T", " ")}`;
    } catch {}
  }

  greet();
  loadAgenda();
})();
