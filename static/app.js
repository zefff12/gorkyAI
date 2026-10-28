// Клиент: голос (Web Speech API) → /api/message → ответ текстом и голосом.
// Распознавание и синтез речи выполняет браузер; сервер получает только текст.
(() => {
  const $ = (id) => document.getElementById(id);
  const log = $("log"), input = $("input"), mic = $("mic"), stopBtn = $("stop"), statusEl = $("status");
  const speakToggle = $("speakToggle"), userInput = $("userId");

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
  speakToggle.onchange = () => { store.set("speak", speakToggle.checked ? "1" : "0"); if (!speakToggle.checked) stopSpeaking(); };
  const userId = () => userInput.value.trim() || savedId;
  userInput.onchange = () => { store.set("userId", userId()); start(); };

  const api = (path, opts = {}) => fetch(path, {
    ...opts, headers: { "Content-Type": "application/json", "X-User-Id": userId(), ...(opts.headers || {}) },
  });

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
    stopSpeaking();                       // новая реплика прерывает чтение прошлого ответа
    addMsg(text, "user");
    input.value = "";
    statusEl.textContent = "Думаю…";
    try {
      const res = await api("/api/message", { method: "POST", body: JSON.stringify({ text }) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const el = addMsg(data.text, "bot", data.awaiting ? "ask" : "");
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = `${data.intent}${data.parser ? " · " + data.parser : ""}`;
      el.appendChild(tag);
      speak(data.speech);
      statusEl.textContent = data.awaiting ? "Жду уточнения — ответьте голосом или текстом" : "Готов";
      await loadAgenda(data.events.map((e) => e.id));
      saveBackup();
    } catch (err) {
      addMsg("Не удалось связаться с сервером. Если сайт долго не открывался, подождите минуту — бесплатный хостинг просыпается.", "bot");
      statusEl.textContent = "Ошибка соединения";
    }
  }

  $("form").onsubmit = (e) => { e.preventDefault(); send(input.value); };
  $("chips").onclick = (e) => { if (e.target.tagName === "BUTTON") send(e.target.textContent); };

  // ---------- синтез речи + кнопка «Стоп» ----------
  let ruVoice = null;
  function pickVoice() {
    const voices = window.speechSynthesis?.getVoices() || [];
    ruVoice = voices.find((v) => v.lang?.startsWith("ru") && /google|natural|online/i.test(v.name))
      || voices.find((v) => v.lang?.startsWith("ru")) || null;
  }
  if ("speechSynthesis" in window) { pickVoice(); speechSynthesis.onvoiceschanged = pickVoice; }

  function stopSpeaking() {
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    stopBtn.hidden = true;
  }

  function speak(text) {
    if (!speakToggle.checked || !("speechSynthesis" in window) || !text) return;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text.replace(/[«»•]/g, ""));
    u.lang = "ru-RU";
    if (ruVoice) u.voice = ruVoice;
    u.rate = 1.05;
    u.onstart = () => { stopBtn.hidden = false; };
    u.onend = u.onerror = () => { stopBtn.hidden = true; };
    stopBtn.hidden = false;
    speechSynthesis.speak(u);
  }

  stopBtn.onclick = stopSpeaking;
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") stopSpeaking(); });

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
    rec.onstart = () => { recording = true; mic.classList.add("recording"); statusEl.textContent = "Слушаю…"; stopSpeaking(); };
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

  // ---------- боковая панель: ближайшие 7 дней + без срока ----------
  const WD = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
  const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];

  function iconButton(label, title, onClick) {
    const b = document.createElement("button");
    b.className = "icon";
    b.type = "button";
    b.textContent = label;
    b.title = title;
    b.setAttribute("aria-label", title);
    b.onclick = onClick;
    return b;
  }

  async function loadAgenda(highlight = []) {
    const box = $("agenda");
    try {
      const res = await api("/api/events");
      const events = await res.json();
      box.innerHTML = "";
      $("demoData").hidden = !!events.length || (await recordCount()) !== 0;
      if (!events.length) { box.innerHTML = '<p class="empty">Ничего не запланировано.</p>'; return; }
      let day;
      for (const e of events) {
        if (e.date !== day) {
          day = e.date;
          const h = document.createElement("div");
          h.className = "day";
          if (e.date) {
            const d = new Date(e.date + "T00:00");
            h.textContent = `${WD[d.getDay()]}, ${d.getDate()} ${MONTHS[d.getMonth()]}`;
          } else {
            h.textContent = "Без срока";
          }
          box.appendChild(h);
        }
        const row = document.createElement("div");
        row.className = `ev ${e.type}${e.done ? " done" : ""}${highlight.includes(e.id) ? " flash" : ""}`;
        const t = document.createElement("time");
        t.textContent = e.time || "—";
        const title = document.createElement("span");
        title.className = "t";
        title.textContent = e.title + (e.recurrence ? " ↻" : "");
        const actions = document.createElement("span");
        actions.className = "actions";
        actions.append(
          iconButton(e.done ? "↺" : "✓", e.done ? "Вернуть в работу" : "Отметить выполненной", () => toggleDone(e)),
          iconButton("×", "Удалить", () => removeEvent(e)),
        );
        row.append(t, title, actions);
        box.appendChild(row);
      }
    } catch { box.innerHTML = '<p class="empty">Не удалось загрузить.</p>'; }
  }

  async function toggleDone(e) {
    await api(`/api/events/${e.id}`, { method: "PATCH", body: JSON.stringify({ done: !e.done }) });
    await loadAgenda([e.id]);
    saveBackup();
  }

  async function removeEvent(e) {
    const extra = e.recurrence ? " Это повторяющаяся запись — удалятся все повторы." : "";
    if (!confirm(`Удалить «${e.title}»?${extra}`)) return;
    const res = await api(`/api/events/${e.id}`, { method: "DELETE" });
    if (res.ok) addMsg(`Удалил «${e.title}».`, "bot");
    await loadAgenda();
    saveBackup();
  }

  async function recordCount() {
    try {
      const res = await api("/api/stats");
      return res.ok ? (await res.json()).total : -1;
    } catch { return -1; }
  }

  $("reset").onclick = async () => {
    if (!confirm("Удалить все ваши задачи, напоминания, встречи и историю разговора?")) return;
    await api("/api/reset", { method: "POST" });
    store.set(backupKey(), "");
    log.innerHTML = "";
    greet();
    loadAgenda();
  };

  $("demoData").onclick = async () => {
    // тестовые данные — только в пустой список: сначала проверяем, иначе сервер ответит 409
    const total = await recordCount();
    if (total > 0) {
      addMsg("Тестовые данные загружаются только в пустой список. Нажмите «очистить», если хотите начать заново.", "bot");
      $("demoData").hidden = true;
    } else {
      const res = await api("/api/demo-data", { method: "POST" });
      const data = await res.json();
      addMsg(res.ok ? `Загрузил ${data.imported} тестовых записей из задания. Попробуйте: «Что у меня на этой неделе?» или «Когда у меня была встреча с Иваном?»` : data.detail, "bot");
      await loadAgenda();
      saveBackup();
    }
  };

  // ---------- память между визитами ----------
  // Сервер хранит записи, историю и контекст диалога в базе. Бесплатный хостинг теряет базу
  // при перезапуске, поэтому браузер держит резервную копию и возвращает её в пустой профиль.
  const backupKey = () => `backup:${userId()}`;

  async function saveBackup() {
    try {
      const res = await api("/api/export");
      if (res.ok) store.set(backupKey(), await res.text());
    } catch {}
  }

  async function restoreIfNeeded() {
    const raw = store.get(backupKey(), "");
    if (!raw || (await recordCount()) !== 0) return false;
    try {
      const res = await api("/api/import", { method: "POST", body: raw });
      return res.ok;
    } catch { return false; }
  }

  async function loadHistory() {
    try {
      const res = await api("/api/history?limit=60");
      const items = res.ok ? await res.json() : [];
      for (const m of items) addMsg(m.text, m.role === "user" ? "user" : "bot", "old");
      return items.length;
    } catch { return 0; }
  }

  async function greet() {
    addMsg("Привет! Я веду задачи, напоминания и встречи. Скажите или напишите, что нужно сделать.", "bot");
  }

  async function showMeta() {
    try {
      const h = await (await fetch("/api/health")).json();
      $("meta").textContent = `Понимание запросов: ${h.nlu === "rules" ? "встроенные правила" : h.nlu} · `
        + `поиск: ${h.semantic_search ? "лексический + семантический" : "лексический"} · сейчас ${h.now.replace("T", " ")}`;
    } catch {}
  }

  async function start() {
    log.innerHTML = "";
    stopSpeaking();
    const restored = await restoreIfNeeded();
    const count = await loadHistory();
    if (restored) addMsg("Восстановил ваши записи и наш разговор после перезапуска сервера.", "bot");
    else if (!count) greet();
    else addMsg("С возвращением! Я помню наш разговор — можно продолжать.", "bot");
    await loadAgenda();
    showMeta();
  }

  start();
})();
