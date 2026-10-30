// Клиент ассистента: разделы по макету Figma, голос (Web Speech API), чат и списки записей.
// Распознавание и синтез речи выполняет браузер; сервер получает только текст.
(() => {
  const $ = (id) => document.getElementById(id);
  const ICONS = "/static/icons.svg";
  const store = {
    get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch {} },
  };

  // ------------------------------------------------------------ пользователь
  let me = { authenticated: false };
  let guestId = store.get("userId", "");
  if (!guestId) {
    guestId = "u-" + (crypto.randomUUID?.() || Math.random().toString(36).slice(2) + Date.now().toString(36)).replace(/-/g, "").slice(0, 12);
    store.set("userId", guestId);
  }
  const api = (path, opts = {}) => fetch(path, {
    ...opts, headers: { "Content-Type": "application/json", "X-User-Id": guestId, ...(opts.headers || {}) },
  });
  const getJSON = async (path) => { const r = await api(path); if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); };

  // ------------------------------------------------------------ даты
  let TODAY = new Date();                              // уточняется по времени сервера
  const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
  const MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];
  const WD_SHORT = ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"];
  const WD_LONG = ["воскресенье", "понедельник", "вторник", "среда", "четверг", "пятница", "суббота"];
  const pad = (n) => String(n).padStart(2, "0");
  const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const parse = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
  const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
  const todayIso = () => iso(TODAY);
  function dayLabel(s, long = false) {
    const d = parse(s), diff = Math.round((d - parse(todayIso())) / 864e5);
    const base = `${d.getDate()} ${MONTHS_GEN[d.getMonth()]}${d.getFullYear() !== TODAY.getFullYear() ? " " + d.getFullYear() : ""}`;
    if (diff === 0) return long ? `Сегодня, ${base}` : "Сегодня";
    if (diff === 1) return long ? `Завтра, ${base}` : "Завтра";
    if (diff === -1) return long ? `Вчера, ${base}` : "Вчера";
    return long ? `${WD_LONG[d.getDay()]}, ${base}` : base;
  }
  function timeRange(e) {
    if (!e.time) return e.date ? "Весь день" : "Без срока";
    if (e.type !== "meeting") return e.time;
    const [h, m] = e.time.split(":").map(Number), end = h * 60 + m + (e.duration_min || 60);
    return `${e.time} – ${pad(Math.floor(end / 60) % 24)}:${pad(end % 60)}`;
  }
  const TYPE_NAME = { task: "Задача", meeting: "Встреча", reminder: "Напоминание" };
  const isOverdue = (e) => !e.done && e.date && !e.recurrence && e.date < todayIso();

  // ------------------------------------------------------------ мелкие элементы
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function icon(name, cls = "icon") {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("class", cls);
    s.setAttribute("aria-hidden", "true");
    const u = document.createElementNS("http://www.w3.org/2000/svg", "use");
    u.setAttribute("href", `${ICONS}#${name}`);
    s.appendChild(u);
    return s;
  }
  function catImg(e, cls = "cat-icon") {
    if (!e.category) return null;
    const img = el("img", `${cls} blend`);
    img.src = `/static/img/cat-${e.category}.webp`;
    img.alt = "";
    img.loading = "lazy";
    return img;
  }
  let toastTimer;
  function toast(text) {
    let t = document.querySelector(".toast");
    if (!t) { t = el("div", "toast"); document.body.appendChild(t); }
    t.textContent = text;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 3200);
  }

  // одно открытое меню за раз; клик мимо закрывает
  let openMenu = null;
  function toggleMenu(menu, fill) {
    const willOpen = menu.hidden;
    closeMenus();
    if (willOpen) { if (fill) fill(menu); menu.hidden = false; openMenu = menu; }
  }
  function closeMenus() { if (openMenu) { openMenu.hidden = true; openMenu = null; } }
  document.addEventListener("click", (e) => {
    if (openMenu && !openMenu.contains(e.target) && !e.target.closest("[data-menu-trigger]")) closeMenus();
  });

  // ------------------------------------------------------------ действия с записями
  async function setDone(e, done) {
    await api(`/api/events/${e.id}`, { method: "PATCH", body: JSON.stringify({ done }) });
    await refresh();
  }
  async function removeEvent(e) {
    const extra = e.recurrence ? " Это повторяющаяся запись — удалятся все повторы." : "";
    if (!confirm(`Удалить «${e.title}»?${extra}`)) return;
    const r = await api(`/api/events/${e.id}`, { method: "DELETE" });
    if (r.ok) toast(`Удалил «${e.title}»`);
    await refresh();
  }
  function moreButton(e, host) {
    const btn = el("button", "icon-btn more");
    btn.setAttribute("aria-label", "Действия");
    btn.dataset.menuTrigger = "1";
    btn.appendChild(icon("more"));
    const menu = el("div", "menu");
    menu.hidden = true;
    btn.onclick = (ev) => {
      ev.stopPropagation();
      toggleMenu(menu, (m) => {
        m.replaceChildren();
        const done = el("button");
        done.append(icon(e.done ? "undo" : "check", "icon sm"), e.done ? "Вернуть в работу" : "Отметить выполненной");
        done.onclick = () => { closeMenus(); setDone(e, !e.done); };
        const say = el("button");
        say.append(icon("mic", "icon sm"), "Перенести голосом");
        say.onclick = () => { closeMenus(); go("#/"); prefill(`Перенеси «${e.title}» на `); };
        const del = el("button", "danger");
        del.append(icon("trash", "icon sm"), "Удалить");
        del.onclick = () => { closeMenus(); removeEvent(e); };
        m.append(done, say, el("hr"), del);
      });
    };
    host.appendChild(menu);
    return btn;
  }

  function rowEl(e, { showDate = false } = {}) {
    const row = el("div", `row${e.done ? " done" : ""}`);
    const cb = el("input", "check");
    cb.type = "checkbox";
    cb.checked = e.done;
    cb.setAttribute("aria-label", e.done ? "Снять отметку" : "Отметить выполненной");
    cb.onchange = () => setDone(e, cb.checked);
    const title = el("div", "title");
    title.append(el("span", "t", e.title + (e.recurrence ? " ↻" : "")), el("span", `pill ${e.type}`, TYPE_NAME[e.type]));
    if (isOverdue(e)) title.append(el("span", "pill overdue", "Просрочено"));
    const when = el("div", "when");
    when.append(icon(e.date ? "clock" : "calendar", "icon sm"),
      showDate && e.date ? `${dayLabel(e.date)}${e.time ? ", " + timeRange(e) : ""}` : timeRange(e));
    row.append(cb, title, when, catImg(e) || el("span"), moreButton(e, row));
    return row;
  }

  function meetEl(e) {
    const m = el("div", "meet");
    const ico = el("div", "ico");
    ico.append(catImg(e, "") || icon("video"));
    const info = el("div");
    info.append(el("b", null, e.title), el("small", null, `${e.date ? dayLabel(e.date) : ""}, ${timeRange(e)}`));
    m.append(ico, info, moreButton(e, m));
    return m;
  }

  function emptyEl(text) { return el("p", "empty", text); }

  // ------------------------------------------------------------ чат
  const log = $("log"), input = $("input"), statusEl = $("status"), stopBtn = $("stop"), mic = $("mic");
  let speakOn = store.get("speak", "1") === "1";

  function addMsg(text, who, extra = "") {
    const m = el("div", `msg ${who} ${extra}`.trim(), text);
    log.appendChild(m);
    log.scrollTop = log.scrollHeight;
    return m;
  }

  async function send(text) {
    text = (text || "").trim();
    if (!text) return;
    stopSpeaking();
    if (location.hash && location.hash !== "#/") go("#/");
    addMsg(text, "user");
    input.value = "";
    statusEl.textContent = "Думаю…";
    try {
      const res = await api("/api/message", { method: "POST", body: JSON.stringify({ text }) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const m = addMsg(data.text, "bot", data.awaiting ? "ask" : "");
      m.appendChild(el("span", "tag", `${data.intent}${data.parser ? " · " + data.parser : ""}`));
      speak(data.speech);
      statusEl.textContent = data.awaiting ? "Жду уточнения — ответьте голосом или текстом" : "";
      await refresh();
      saveBackup();
    } catch {
      addMsg("Не удалось связаться с сервером. Если сайт долго не открывался, подождите минуту — бесплатный хостинг просыпается.", "bot");
      statusEl.textContent = "Ошибка соединения";
    }
  }
  function prefill(text) {
    input.value = text;
    input.focus();
    input.setSelectionRange(text.length, text.length);
  }

  $("form").onsubmit = (e) => { e.preventDefault(); send(input.value); };
  $("chips").onclick = (e) => { const b = e.target.closest("button"); if (b) send(b.textContent); };
  $("popular").onclick = (e) => { const b = e.target.closest("button"); if (b) send(b.lastChild.textContent); };
  document.querySelectorAll("[data-prefill]").forEach((b) => b.onclick = () => prefill(b.dataset.prefill));
  document.querySelectorAll("[data-go]").forEach((b) => b.onclick = () => go(b.dataset.go));
  document.querySelectorAll("[data-feature]").forEach((b) => b.onclick = () => {
    if (b.dataset.feature === "voice") mic.click();
    if (b.dataset.feature === "search") $("search").focus();
    if (b.dataset.feature === "calendar") go("#/calendar");
  });

  // ---------- синтез речи и «Стоп»
  let ruVoice = null;
  function pickVoice() {
    const voices = window.speechSynthesis?.getVoices() || [];
    ruVoice = voices.find((v) => v.lang?.startsWith("ru") && /google|natural|online/i.test(v.name)) || voices.find((v) => v.lang?.startsWith("ru")) || null;
  }
  if ("speechSynthesis" in window) { pickVoice(); speechSynthesis.onvoiceschanged = pickVoice; }
  function stopSpeaking() { if ("speechSynthesis" in window) speechSynthesis.cancel(); stopBtn.hidden = true; }
  function speak(text) {
    if (!speakOn || !("speechSynthesis" in window) || !text) return;
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

  // ---------- распознавание речи
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
      if (statusEl.textContent === "Слушаю…") statusEl.textContent = "";
    };
    rec.onerror = (e) => {
      statusEl.textContent = { "not-allowed": "Нет доступа к микрофону — разрешите его в настройках браузера",
        "no-speech": "Ничего не услышал, попробуйте ещё раз",
        "network": "Распознавание речи недоступно без интернета" }[e.error] || `Ошибка распознавания: ${e.error}`;
    };
    rec.onresult = (e) => {
      let interim = "", final = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        (r.isFinal ? (final += r[0].transcript) : (interim += r[0].transcript));
      }
      if (interim) { interimEl = interimEl || addMsg("", "user", "interim"); interimEl.textContent = interim; }
      if (final) { if (interimEl) { interimEl.remove(); interimEl = null; } send(final); }
    };
    mic.onclick = () => { if (location.hash && location.hash !== "#/") go("#/"); recording ? rec.stop() : rec.start(); };
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { stopSpeaking(); closeMenus(); }
    if (e.ctrlKey && e.code === "Space" && rec) { e.preventDefault(); mic.click(); }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("search").focus(); }
  });

  // ------------------------------------------------------------ главная
  let selectedDay = null;
  async function renderHome() {
    const events = await getJSON("/api/events");                    // сегодня + 6 дней + без срока
    const today = todayIso();
    $("todayLabel").textContent = `${TODAY.getDate()} ${MONTHS_GEN[TODAY.getMonth()]}, ${WD_LONG[TODAY.getDay()]}`;

    // полоса дней
    selectedDay = selectedDay || today;
    const days = $("days");
    days.replaceChildren();
    for (let i = 0; i < 7; i++) {
      const d = addDays(parse(today), i), s = iso(d);
      const b = el("button", "day");
      if ([0, 6].includes(d.getDay())) b.classList.add("weekend");
      if (events.some((e) => e.date === s)) b.classList.add("has");
      if (s === selectedDay) b.classList.add("active");
      b.append(el("small", null, WD_SHORT[d.getDay()]), el("b", null, String(d.getDate())));
      b.setAttribute("aria-label", dayLabel(s, true));
      b.onclick = () => { selectedDay = s; renderHome(); };
      days.appendChild(b);
    }
    const agenda = $("agenda");
    agenda.replaceChildren();
    const dayEvents = events.filter((e) => e.date === selectedDay);
    if (!dayEvents.length) agenda.append(emptyEl(selectedDay === today ? "На сегодня ничего не запланировано." : "На этот день ничего нет."));
    for (const e of dayEvents) {
      const row = el("div", `ag${e.done ? " done" : ""}`);
      const label = el("label");
      const cb = el("input", "check");
      cb.type = "checkbox";
      cb.checked = e.done;
      cb.onchange = () => setDone(e, cb.checked);
      label.append(cb, el("span", null, e.title));
      row.append(el("time", null, e.time || "Весь день"), el("span", `dot ${e.type}`), label);
      agenda.appendChild(row);
    }

    // «Сегодня»: записи на сегодня и задачи без срока
    const todayList = $("todayList");
    todayList.replaceChildren();
    const todays = events.filter((e) => e.date === today || e.date === null);
    if (!todays.length) todayList.append(emptyEl("На сегодня ничего нет. Скажите, например: «Напомни в 18:00 купить хлеб»."));
    todays.slice(0, 6).forEach((e) => todayList.appendChild(rowEl(e)));

    // ближайшие встречи
    const meets = await getJSON(`/api/items?types=meeting&status=active&undated=false&start=${today}&end=${iso(addDays(TODAY, 90))}`);
    const ml = $("meetList");
    ml.replaceChildren();
    if (!meets.length) ml.append(emptyEl("Встреч пока нет. Скажите: «Добавь встречу с командой завтра в 15:00»."));
    meets.slice(0, 4).forEach((e) => ml.appendChild(meetEl(e)));
  }

  // ------------------------------------------------------------ списки с фильтрами
  const LISTS = {
    tasks: { title: "Мои задачи", sub: "Все записи — выберите период или даты", types: "", prefill: "Поставь задачу ",
      presets: ["all", "today", "week", "month", "overdue", "undated", "done"] },
    meetings: { title: "Встречи", sub: "Предстоящие и прошедшие встречи", types: "meeting", prefill: "Добавь встречу ",
      presets: ["upcoming", "today", "week", "month", "past", "all"] },
    reminders: { title: "Напоминания", sub: "Всё, о чём ассистент должен напомнить", types: "reminder", prefill: "Напомни ",
      presets: ["upcoming", "today", "week", "overdue", "done", "all"] },
  };
  const PRESET_NAMES = { all: "Все", today: "Сегодня", week: "Неделя", month: "Месяц", overdue: "Просроченные",
    undated: "Без срока", done: "Выполненные", upcoming: "Предстоящие", past: "Прошедшие", custom: "Период" };
  let listKind = "tasks", listPreset = "all", queryTimer, listSeq = 0, calSeq = 0;

  function presetRange(p) {
    const t = parse(todayIso());
    switch (p) {
      case "today": return [t, t];
      case "week": return [t, addDays(t, 6)];
      case "month": return [t, addDays(t, 30)];
      case "upcoming": return [t, addDays(t, 365)];
      case "past": return [addDays(t, -3650), addDays(t, -1)];
      default: return [null, null];
    }
  }

  function applyPreset(p) {
    listPreset = p;
    const [a, b] = presetRange(p);
    $("from").value = a ? iso(a) : "";
    $("to").value = b ? iso(b) : "";
    $("statusFilter").value = { overdue: "overdue", done: "done", upcoming: "active" }[p] || "all";
    renderList();
  }

  function openList(kind, preset) {
    listKind = kind;
    const cfg = LISTS[kind];
    $("listTitle").textContent = cfg.title;
    $("listSub").textContent = cfg.sub;
    $("typeFilter").hidden = !!cfg.types;
    $("typeFilter").value = "";
    $("listQuery").value = "";
    $("listAdd").onclick = () => { go("#/"); prefill(cfg.prefill); };
    const seg = $("presets");
    seg.replaceChildren();
    for (const p of cfg.presets) {
      const b = el("button", null, PRESET_NAMES[p]);
      b.dataset.preset = p;
      b.onclick = () => applyPreset(p);
      seg.appendChild(b);
    }
    applyPreset(preset && cfg.presets.includes(preset) ? preset : cfg.presets[0]);
  }

  ["from", "to", "typeFilter", "statusFilter"].forEach((id) => $(id).addEventListener("change", () => {
    if (id === "from" || id === "to") listPreset = "custom";
    renderList();
  }));
  $("listQuery").addEventListener("input", () => { clearTimeout(queryTimer); queryTimer = setTimeout(renderList, 250); });

  async function renderList() {
    const seq = ++listSeq;                       // ответ на устаревший запрос отбрасываем
    document.querySelectorAll("#presets button").forEach((b) => b.classList.toggle("on", b.dataset.preset === listPreset));
    const cfg = LISTS[listKind];
    const params = new URLSearchParams();
    const from = $("from").value, to = $("to").value;
    if (from) params.set("start", from);
    if (to) params.set("end", to);
    const types = cfg.types || $("typeFilter").value;
    if (types) params.set("types", types);
    params.set("status", $("statusFilter").value);
    // «без срока» показываем, если период не задан или включает сегодня
    const undatedOnly = listPreset === "undated";
    const includeUndated = undatedOnly || (!from && !to) || (from <= todayIso() && (!to || to >= todayIso()) && listPreset !== "past");
    params.set("undated", includeUndated ? "true" : "false");
    const q = $("listQuery").value.trim();
    if (q) params.set("q", q);

    let events = await getJSON(`/api/items?${params}`);
    if (seq !== listSeq) return;
    if (undatedOnly) events = events.filter((e) => e.date === null);
    if (listPreset === "past") events.reverse();

    const body = $("listBody");
    body.replaceChildren();
    const shown = events.slice(0, 400);
    const period = from || to ? ` · ${from ? dayLabel(from) : "…"} — ${to ? dayLabel(to) : "…"}` : "";
    $("listSummary").textContent = `${events.length} ${plural(events.length, "запись", "записи", "записей")}${period}`
      + (events.length > shown.length ? ` · показаны первые ${shown.length}` : "");
    if (!events.length) {
      body.append(emptyEl(q ? "Ничего не нашлось. Попробуйте другое слово." : "За выбранный период записей нет."));
      return;
    }
    let group = undefined, rows = null;
    for (const e of shown) {
      if (e.date !== group) {
        group = e.date;
        body.append(el("div", "group-title", e.date ? dayLabel(e.date, true) : "Без срока"));
        rows = el("div", "rows");
        body.append(rows);
      }
      rows.appendChild(rowEl(e));
    }
  }

  function plural(n, one, few, many) {
    const a = n % 100, b = n % 10;
    if (a >= 11 && a <= 14) return many;
    if (b === 1) return one;
    if (b >= 2 && b <= 4) return few;
    return many;
  }

  // ------------------------------------------------------------ календарь
  let calMonth = null, calSel = null;
  async function renderCalendar() {
    calMonth = calMonth || new Date(TODAY.getFullYear(), TODAY.getMonth(), 1);
    calSel = calSel || todayIso();
    $("calTitle").textContent = `${MONTHS[calMonth.getMonth()]} ${calMonth.getFullYear()}`;
    const first = calMonth, startOffset = (first.getDay() + 6) % 7;
    const gridStart = addDays(first, -startOffset), gridEnd = addDays(gridStart, 41);
    const seq = ++calSeq;
    const events = await getJSON(`/api/items?start=${iso(gridStart)}&end=${iso(gridEnd)}&undated=false`);
    if (seq !== calSeq) return;
    const byDay = {};
    for (const e of events) (byDay[e.date] ||= []).push(e);

    const grid = $("calGrid");
    grid.replaceChildren();
    ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].forEach((d) => grid.appendChild(el("div", "dow", d)));
    for (let i = 0; i < 42; i++) {
      const d = addDays(gridStart, i), s = iso(d), list = byDay[s] || [];
      const cell = el("button", "cal-cell");
      if (d.getMonth() !== calMonth.getMonth()) cell.classList.add("out");
      if (s === todayIso()) cell.classList.add("today");
      if (s === calSel) cell.classList.add("sel");
      if (list.length) cell.classList.add("has");
      cell.append(el("span", "num", String(d.getDate())));
      list.slice(0, 3).forEach((e) => cell.append(el("span", `ev ${e.type}${e.done ? " done" : ""}`, `${e.time ? e.time + " " : ""}${e.title}`)));
      if (list.length > 3) cell.append(el("span", "more", `ещё ${list.length - 3}`));
      cell.setAttribute("aria-label", `${dayLabel(s, true)}: ${list.length} ${plural(list.length, "запись", "записи", "записей")}`);
      cell.onclick = () => { calSel = s; renderCalendar(); };
      grid.appendChild(cell);
    }
    $("calDayTitle").textContent = dayLabel(calSel, true);
    const dayList = $("calDayList");
    dayList.replaceChildren();
    const sel = byDay[calSel] || [];
    if (!sel.length) dayList.append(emptyEl("В этот день ничего нет."));
    sel.forEach((e) => dayList.appendChild(rowEl(e)));
  }
  $("calPrev").onclick = () => { calMonth = new Date(calMonth.getFullYear(), calMonth.getMonth() - 1, 1); renderCalendar(); };
  $("calNext").onclick = () => { calMonth = new Date(calMonth.getFullYear(), calMonth.getMonth() + 1, 1); renderCalendar(); };
  $("calToday").onclick = () => { calMonth = null; calSel = null; renderCalendar(); };

  // ------------------------------------------------------------ история
  async function renderHistory() {
    const items = await getJSON("/api/history?limit=500");
    const box = $("historyBody");
    box.replaceChildren();
    if (!items.length) { box.append(emptyEl("Разговор ещё не начат. Задайте вопрос на главной.")); return; }
    let day = null;
    for (const m of items) {
      const d = (m.created_at || "").slice(0, 10);
      if (d && d !== day) { day = d; box.append(el("time", null, dayLabel(d, true))); }
      box.append(el("div", `msg ${m.role === "user" ? "user" : "bot"}`, m.text));
    }
  }

  // ------------------------------------------------------------ поиск (Ctrl+K)
  let searchTimer;
  const searchBox = $("search"), results = $("searchResults");
  searchBox.addEventListener("input", () => {
    clearTimeout(searchTimer);
    const q = searchBox.value.trim();
    if (!q) { results.hidden = true; return; }
    searchTimer = setTimeout(async () => {
      const hits = await getJSON(`/api/search?q=${encodeURIComponent(q)}`);
      results.replaceChildren();
      if (!hits.length) results.append(el("div", "note", "Ничего не нашлось"));
      for (const e of hits) {
        const b = el("button", "r");
        const info = el("span");
        info.append(el("b", null, e.title), el("small", null, ` · ${TYPE_NAME[e.type]}`));
        b.append(catImg(e) || icon(e.type === "meeting" ? "users" : "tasks"), info, el("small", null, e.date ? dayLabel(e.date) : "без срока"));
        b.onclick = () => {
          results.hidden = true;
          searchBox.value = "";
          if (e.date) { calSel = e.date; const d = parse(e.date); calMonth = new Date(d.getFullYear(), d.getMonth(), 1); go("#/calendar"); }
          else go("#/tasks?preset=undated");
        };
        results.appendChild(b);
      }
      results.hidden = false;
      openMenu = results;
    }, 200);
  });
  searchBox.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); const q = searchBox.value.trim(); results.hidden = true; go(`#/tasks?q=${encodeURIComponent(q)}`); }
  });

  // ------------------------------------------------------------ колокольчик
  async function renderBell() {
    const due = await getJSON(`/api/items?types=reminder&status=active&undated=false&start=${iso(addDays(TODAY, -30))}&end=${todayIso()}`);
    $("bellDot").hidden = !due.length;
    $("remindBadge").hidden = !due.length;
    $("remindBadge").textContent = String(due.length);
    $("bell").dataset.menuTrigger = "1";
    $("bell").onclick = (ev) => {
      ev.stopPropagation();
      toggleMenu($("bellMenu"), (m) => {
        m.replaceChildren();
        m.append(el("div", "note", due.length ? "Напоминания на сегодня и просроченные" : "На сегодня напоминаний нет"));
        for (const e of due.slice(0, 8)) {
          const b = el("button");
          b.append(icon("bell", "icon sm"), `${e.time || ""} ${e.title}${e.date !== todayIso() ? " · " + dayLabel(e.date) : ""}`);
          b.onclick = () => { closeMenus(); go("#/reminders?preset=overdue"); };
          m.append(b);
        }
        const all = el("button");
        all.append(icon("arrow-right", "icon sm"), "Все напоминания");
        all.onclick = () => { closeMenus(); go("#/reminders"); };
        m.append(el("hr"), all);
      });
    };
  }

  // ------------------------------------------------------------ профиль
  function renderProfile() {
    const initials = me.authenticated ? me.initials : "Г";
    ["profileAvatar", "topAvatar"].forEach((id) => { $(id).textContent = initials; $(id).dataset.menuTrigger = "1"; });
    $("profileName").textContent = me.authenticated ? me.name : "Гость";
    $("profileRole").textContent = me.authenticated ? "Пользователь" : "Без регистрации";
    const open = (ev) => {
      ev.stopPropagation();
      toggleMenu($("profileMenu"), async (m) => {
        m.replaceChildren();
        const head = el("div", "note");
        head.append(el("b", null, me.authenticated ? me.name : "Гостевой режим"),
          me.authenticated ? me.email : "Записи хранятся только для этого браузера");
        m.append(head, el("hr"));
        const voice = el("button");
        voice.append(icon(speakOn ? "volume" : "x", "icon sm"), speakOn ? "Озвучивание ответов: вкл" : "Озвучивание ответов: выкл");
        voice.onclick = () => { speakOn = !speakOn; store.set("speak", speakOn ? "1" : "0"); if (!speakOn) stopSpeaking(); closeMenus(); toast(speakOn ? "Ответы будут озвучиваться" : "Озвучивание выключено"); };
        const demo = el("button");
        demo.append(icon("database", "icon sm"), "Загрузить тестовые данные (1000)");
        demo.onclick = () => { closeMenus(); loadDemo(); };
        const clear = el("button", "danger");
        clear.append(icon("trash", "icon sm"), "Очистить мои данные");
        clear.onclick = () => { closeMenus(); resetData(); };
        m.append(voice, demo, clear, el("hr"));
        if (me.authenticated) {
          const out = el("button");
          out.append(icon("logout", "icon sm"), "Выйти");
          out.onclick = logout;
          m.append(out);
        } else {
          const login = el("a");
          login.href = "/login";
          login.append(icon("login", "icon sm"), "Войти");
          const reg = el("a");
          reg.href = "/register";
          reg.append(icon("user", "icon sm"), "Зарегистрироваться — записи будут на всех устройствах");
          m.append(login, reg);
        }
      });
    };
    $("profileAvatar").onclick = open;
    $("profileGear").onclick = open;
    $("profileGear").dataset.menuTrigger = "1";
    $("topAvatar").onclick = open;
  }

  async function loadDemo() {
    const total = (await getJSON("/api/stats")).total;
    if (total > 0) {
      toast("Тестовые данные загружаются только в пустой список — сначала очистите данные");
    } else {
      const r = await api("/api/demo-data", { method: "POST" });
      const data = await r.json();
      toast(r.ok ? `Загрузил ${data.imported} записей из задания` : data.detail);
      await refresh();
      saveBackup();
    }
  }
  async function resetData() {
    if (!confirm("Удалить все ваши задачи, встречи, напоминания и историю разговора?")) return;
    await api("/api/reset", { method: "POST" });
    store.set(backupKey(), "");
    log.replaceChildren();
    greet();
    await refresh();
  }
  async function logout() {
    await api("/api/auth/logout", { method: "POST" });
    store.set("guestOk", "");
    location.replace("/login");
  }

  // ------------------------------------------------------------ резервная копия
  // Бесплатный хостинг теряет базу при перезапуске: браузер хранит копию и возвращает её.
  const backupKey = () => (me.authenticated ? `backup:acc:${me.email}` : `backup:${guestId}`);
  async function saveBackup() {
    try { const r = await api("/api/export"); if (r.ok) store.set(backupKey(), await r.text()); } catch {}
  }
  async function restoreIfNeeded() {
    const raw = store.get(backupKey(), "");
    if (!raw) return false;
    try {
      if ((await getJSON("/api/stats")).total !== 0) return false;
      return (await api("/api/import", { method: "POST", body: raw })).ok;
    } catch { return false; }
  }

  async function loadDialog() {
    try {
      const items = await getJSON("/api/history?limit=20");
      for (const m of items) addMsg(m.text, m.role === "user" ? "user" : "bot", "old");
      return items.length;
    } catch { return 0; }
  }
  function greet() {
    addMsg(me.authenticated ? `Привет, ${me.name.split(" ")[1] || me.name.split(" ")[0]}! Скажите или напишите, что нужно сделать.`
      : "Привет! Я веду задачи, напоминания и встречи. Скажите или напишите, что нужно сделать.", "bot");
  }

  // ------------------------------------------------------------ маршрутизация
  function go(hash) { if (location.hash !== hash) location.hash = hash; else route(); }
  async function route() {
    closeMenus();
    const [path, query] = (location.hash.replace(/^#/, "") || "/").split("?");
    const params = new URLSearchParams(query || "");
    const view = { "/": "home", "/tasks": "tasks", "/calendar": "calendar", "/meetings": "meetings", "/reminders": "reminders", "/history": "history" }[path] || "home";
    document.querySelectorAll(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
    $("view-home").hidden = view !== "home";
    $("view-list").hidden = !LISTS[view];
    $("view-calendar").hidden = view !== "calendar";
    $("view-history").hidden = view !== "history";
    window.scrollTo(0, 0);
    try {
      if (view === "home") await renderHome();
      else if (LISTS[view]) {
        openList(view, params.get("preset"));
        if (params.get("q")) { $("listQuery").value = params.get("q"); renderList(); }
      } else if (view === "calendar") await renderCalendar();
      else if (view === "history") await renderHistory();
    } catch { toast("Не удалось загрузить данные — проверьте соединение"); }
  }
  window.addEventListener("hashchange", route);

  // обновить то, что сейчас на экране, и счётчики
  async function refresh() {
    const [path] = (location.hash.replace(/^#/, "") || "/").split("?");
    try {
      if (path === "/" || !path) await renderHome();
      else if (["/tasks", "/meetings", "/reminders"].includes(path)) await renderList();
      else if (path === "/calendar") await renderCalendar();
      else if (path === "/history") await renderHistory();
      await renderBell();
    } catch {}
  }

  // ------------------------------------------------------------ старт
  async function start() {
    try { me = await (await fetch("/api/auth/me")).json(); } catch {}
    if (!me.authenticated && store.get("guestOk", "") !== "1") { location.replace("/login"); return; }
    try {
      const h = await (await fetch("/api/health")).json();
      const d = new Date(h.now);
      if (!isNaN(d)) TODAY = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    } catch {}
    renderProfile();
    const restored = await restoreIfNeeded();
    const count = await loadDialog();
    if (restored) addMsg("Восстановил ваши записи и наш разговор после перезапуска сервера.", "bot");
    else if (!count) greet();
    const welcome = store.get("welcome", "");
    if (welcome) { toast(welcome); store.set("welcome", ""); }
    await route();
    renderBell();
  }
  start();
})();
