// Вход и регистрация. Режим определяется адресом: /login или /register.
(() => {
  const mode = location.pathname.startsWith("/register") ? "register" : "login";
  document.querySelectorAll("[data-mode]").forEach((el) => { el.hidden = el.dataset.mode !== mode; });
  document.title = (mode === "login" ? "Вход" : "Регистрация") + " — Ассистент задач и встреч";

  const store = {
    get(k, d) { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch {} },
  };

  // уже вошли — сразу в приложение
  fetch("/api/auth/me").then((r) => r.json()).then((me) => { if (me.authenticated) location.replace("/"); }).catch(() => {});

  // гостевой режим: без аккаунта, данные привязаны к этому браузеру
  document.querySelectorAll("[data-guest]").forEach((a) => a.addEventListener("click", () => store.set("guestOk", "1")));

  // показать/скрыть пароль
  document.querySelectorAll(".eye").forEach((btn) => btn.addEventListener("click", () => {
    const input = btn.parentElement.querySelector("input");
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    btn.querySelector("use").setAttribute("href", `/static/icons.svg#${show ? "eye" : "eye-off"}`);
    btn.setAttribute("aria-label", show ? "Скрыть пароль" : "Показать пароль");
  }));

  document.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => document.getElementById(b.dataset.open).showModal()));

  function showError(form, text) {
    const box = form.querySelector(".form-error");
    box.textContent = text || "";
    box.hidden = !text;
  }

  async function post(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-User-Id": store.get("userId", "") || "" },
      body: JSON.stringify(body),
    });
    let data = {};
    try { data = await res.json(); } catch {}
    return { ok: res.ok, status: res.status, data };
  }

  // ---------- вход ----------
  const loginForm = document.getElementById("loginForm");
  loginForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(loginForm);
    const email = (f.get("email") || "").trim(), password = f.get("password") || "";
    if (!email || !password) return showError(loginForm, "Введите почту и пароль");
    const btn = loginForm.querySelector(".submit");
    btn.disabled = true;
    const r = await post("/api/auth/login", { email, password, remember: !!f.get("remember") });
    btn.disabled = false;
    if (r.ok) { store.set("guestOk", ""); location.replace("/"); }
    else showError(loginForm, typeof r.data.detail === "string" ? r.data.detail : "Не удалось войти");
  });

  // ---------- регистрация ----------
  const regForm = document.getElementById("registerForm");
  const rules = {
    len: (p) => p.length >= 8,
    digit: (p) => /\d/.test(p),
    case: (p) => /[a-zа-яё]/.test(p) && /[A-ZА-ЯЁ]/.test(p),
    special: (p) => /[^\w\s]/.test(p),
  };
  const pw = regForm.querySelector('[name="password"]');
  pw.addEventListener("input", () => {
    for (const [name, test] of Object.entries(rules)) regForm.querySelector(`[data-rule="${name}"]`).classList.toggle("ok", test(pw.value));
  });

  regForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    regForm.querySelectorAll("[data-err]").forEach((el) => { el.textContent = ""; });
    showError(regForm, "");
    const f = new FormData(regForm);
    const body = {
      name: (f.get("name") || "").trim(), email: (f.get("email") || "").trim(),
      password: f.get("password") || "", password2: f.get("password2") || "", accept: !!f.get("accept"),
    };
    const btn = regForm.querySelector(".submit");
    btn.disabled = true;
    const r = await post("/api/auth/register", body);
    btn.disabled = false;
    if (r.ok) {
      store.set("guestOk", "");
      if (r.data.moved) store.set("welcome", `Перенёс в аккаунт ${r.data.moved} записей, созданных без входа.`);
      location.replace("/");
      return;
    }
    const errors = r.data?.detail?.errors;
    if (errors) for (const [field, text] of Object.entries(errors)) {
      const slot = regForm.querySelector(`[data-err="${field}"]`);
      if (slot) slot.textContent = text;
    }
    else showError(regForm, "Не удалось зарегистрироваться. Попробуйте ещё раз.");
  });
})();
