"use strict";

// The secret arrives in the URL fragment (never sent over the wire), is kept for
// this tab, and the fragment is wiped so it does not linger in history.
const SECRET = (() => {
  const m = location.hash.match(/s=([^&]+)/);
  if (m) {
    sessionStorage.setItem("mmm-secret", decodeURIComponent(m[1]));
    history.replaceState(null, "", location.pathname);
  }
  return sessionStorage.getItem("mmm-secret") || "";
})();

// ------------------------------------------------------------------ helpers

/** h("div", {class: "x", onclick: fn}, child, "text") — never innerHTML. */
function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

class ApiError extends Error {
  constructor(status, type, message) { super(message); this.status = status; this.type = type; }
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: { "X-MMM-Secret": SECRET, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data.type || "error", data.message || res.statusText);
  return data;
}

function toast(title, text, error) {
  const el = h("div", { class: "toast" + (error ? " error" : "") }, h("b", {}, title), text || "");
  document.getElementById("toasts").append(el);
  setTimeout(() => el.remove(), error ? 9000 : 5000);
}

/** Runs fn and shows any failure as a toast instead of a broken view. */
async function guard(fn) {
  try { return await fn(); }
  catch (e) {
    toast(e.type ? `${e.type}` : "Error", e.message, true);
    if (e.status === 403) banner("This page lost its secret. Run `mmm ui` again to reopen it.");
    return undefined;
  }
}

function banner(text) {
  const el = document.getElementById("banner");
  el.textContent = text || "";
  el.hidden = !text;
}

const fmtTime = (ms) => new Date(Number(ms)).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const digits = (s) => String(s || "").replace(/\D/g, "").slice(-10);
const card = (title, ...body) => h("section", { class: "card stack" }, title ? h("h2", {}, title) : null, ...body);
const kv = (pairs) => h("dl", { class: "kv" }, pairs.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v ?? "—")]));

function toggle(checked, onchange, disabled) {
  return h("label", { class: "switch" },
    h("input", { type: "checkbox", checked, disabled, onchange: (e) => onchange(e.target.checked, e.target) }),
    h("span"));
}

// ------------------------------------------------------------------- router

const main = document.getElementById("main");
const views = {};
let current = null;
let refreshTimer = null;

function show(name) {
  current = name;
  try { localStorage.setItem("mmm-view", name); } catch (_) { /* private mode */ }
  for (const b of document.querySelectorAll(".nav button")) {
    b.toggleAttribute("aria-current", b.dataset.view === name);
    if (b.dataset.view === name) b.setAttribute("aria-current", "page");
  }
  clearInterval(refreshTimer);
  main.replaceChildren(h("p", { class: "muted" }, "Loading…"));
  views[name]();
}

for (const b of document.querySelectorAll(".nav button")) b.addEventListener("click", () => show(b.dataset.view));

// --------------------------------------------------------------------- live

const link = document.getElementById("link");
function setLink(state) {
  link.classList.toggle("up", !!state.connected);
  link.classList.toggle("down", !state.connected);
  document.getElementById("link-text").textContent = state.connected
    ? `phone · ${state.latency_ms ?? "?"} ms`
    : (state.error ? "phone unreachable" : "connecting…");
}

function live() {
  const es = new EventSource(`/api/events?s=${encodeURIComponent(SECRET)}`);
  es.addEventListener("link", (e) => setLink(JSON.parse(e.data)));
  es.addEventListener("sms", (e) => {
    const m = JSON.parse(e.data);
    toast(`SMS from ${m.sender}`, m.text);
    if (current === "messages") setTimeout(() => views.messages(true), 1500);
  });
  es.addEventListener("run", (e) => {
    const r = JSON.parse(e.data);
    if (!r.test) toast(`Rule "${r.rule}" ${r.blocked ? "blocked" : "ran"}`, r.blocked ? `sender ${r.sender} not allowed` : `exit ${r.exit} in ${r.ms} ms`, r.blocked || r.exit !== 0);
    if (current === "rules") loadRuns();
  });
  es.onopen = () => banner("");
  es.onerror = () => { setLink({ connected: false }); banner("Lost the daemon. Retrying…"); };
}

// ----------------------------------------------------------------- messages

let openThread = null;

views.messages = async (keep) => {
  const rows = await guard(() => api("GET", "/api/sms?limit=400"));
  if (!rows || current !== "messages") return;
  const threads = new Map();
  for (const m of rows) {
    const key = digits(m.address) || m.address || "?";
    if (!threads.has(key)) threads.set(key, { address: m.address, items: [] });
    threads.get(key).items.push(m);
  }
  if (!keep || !threads.has(openThread)) openThread = threads.keys().next().value || null;

  const convo = h("div", { class: "card convo" });
  const list = h("div", { class: "card threads" },
    h("div", { class: "row spread" }, h("h2", {}, "Threads"),
      h("button", { class: "btn", onclick: () => { openThread = "new"; renderConvo(); } }, "New")),
    [...threads.entries()].map(([key, t]) =>
      h("button", { class: "thread", "aria-current": String(key === openThread), onclick: () => { openThread = key; views.messages(true); } },
        h("div", { class: "who" }, t.address),
        h("div", { class: "last" }, t.items[0].body))));

  function renderConvo() {
    const t = threads.get(openThread);
    const to = h("input", { type: "text", placeholder: "Phone number", value: t ? t.address : "" });
    const text = h("textarea", { placeholder: "Text message", rows: 1 });
    const send = async () => {
      if (!text.value.trim() || !to.value.trim()) return;
      const ok = await guard(() => api("POST", "/api/sms", { to: to.value.trim(), body: text.value }));
      if (ok) { toast("Sent", text.value); text.value = ""; openThread = digits(to.value); setTimeout(() => views.messages(true), 1200); }
    };
    text.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
    const bubbles = h("div", { class: "bubbles" },
      t ? [...t.items].reverse().map((m) =>
        h("div", { class: "bubble" + (Number(m.type) === 2 ? " mine" : "") }, m.body, h("time", {}, fmtTime(m.date)))) : null);
    convo.replaceChildren(
      h("div", { class: "row" }, h("h2", {}, t ? t.address : "New message")),
      bubbles,
      h("div", { class: "compose" }, t ? null : to, text, h("button", { class: "btn primary", onclick: send }, "Send")));
    if (t) to.hidden = true;
    // After the view is in the DOM, or there is no height to scroll yet.
    requestAnimationFrame(() => { bubbles.scrollTop = bubbles.scrollHeight; });
  }
  renderConvo();
  main.replaceChildren(h("h1", {}, "Messages"), h("div", { class: "messages" }, list, convo));
};

// -------------------------------------------------------------------- rules

let rulesState = [];

views.rules = async () => {
  const data = await guard(() => api("GET", "/api/rules"));
  if (!data || current !== "rules") return;
  rulesState = data.rules.map((r) => ({ ...r, senders: r.senders.join(", ") }));
  const list = h("div", { class: "stack" });
  const runs = h("div", { id: "runs" });

  function ruleCard(r, i) {
    const out = h("div", { class: "out mono", hidden: true });
    const sample = h("input", { type: "text", placeholder: `${r.trigger === "*" ? "" : r.trigger + " "}sample text` });
    const bind = (key, el, prop = "value") => { el.addEventListener("input", () => { r[key] = prop === "checked" ? el.checked : el.value; }); return el; };
    return card(null,
      h("div", { class: "rule" },
        h("label", {}, "Trigger"), bind("trigger", h("input", { type: "text", value: r.trigger, placeholder: "word, or * for anything else" })),
        h("label", {}, "Command"), bind("command_line", h("input", { type: "text", class: "mono", value: r.command_line, placeholder: "/path/to/script --flag" })),
        h("label", {}, "Senders"), bind("senders", h("input", { type: "text", value: r.senders, placeholder: "empty = anyone" })),
        h("label", {}, "Timeout (s)"), bind("timeout", h("input", { type: "number", min: 1, max: 3600, value: r.timeout })),
        h("label", {}, "Reply"), h("div", { class: "row" }, toggle(r.reply, (v) => { r.reply = v; }), h("span", { class: "muted" }, "text the command's output back (one SMS)")),
        h("label", {}, "Enabled"), toggle(r.enabled, (v) => { r.enabled = v; })),
      h("div", { class: "row" }, sample,
        h("button", { class: "btn", onclick: async () => {
          const res = await guard(() => api("POST", "/api/rules/test", { trigger: r.trigger, text: sample.value || r.trigger }));
          if (res) { out.hidden = false; out.textContent = `exit ${res.exit}${res.timed_out ? " (timed out)" : ""} · ${res.ms} ms\n${res.stdout}${res.stderr ? "\n--- stderr ---\n" + res.stderr : ""}`; }
        } }, "Test (no reply)"),
        h("button", { class: "btn danger", onclick: () => { rulesState.splice(i, 1); draw(); } }, "Delete")),
      out);
  }
  function draw() { list.replaceChildren(...rulesState.map(ruleCard)); }
  draw();

  const save = async () => {
    const body = { rules: rulesState.map((r) => ({
      trigger: r.trigger, command: r.command_line, reply: r.reply, enabled: r.enabled,
      timeout: Number(r.timeout) || 60,
      senders: String(r.senders || "").split(",").map((s) => s.trim()).filter(Boolean),
    })) };
    const res = await guard(() => api("PUT", "/api/rules", body));
    if (res) { toast("Rules saved", `${res.rules.length} rule(s) live now`); views.rules(); }
  };

  main.replaceChildren(
    h("div", { class: "row spread" }, h("h1", {}, "Rules"),
      h("div", { class: "row" },
        h("button", { class: "btn", onclick: () => { rulesState.push({ trigger: "", command_line: "", senders: "", reply: false, timeout: 60, enabled: true }); draw(); } }, "Add rule"),
        h("button", { class: "btn primary", onclick: save }, "Save"))),
    h("p", { class: "muted" }, "When a text's first word matches a trigger, its command runs on this computer. The message reaches the command on stdin and in MMM_TEXT, MMM_ARGS, MMM_SENDER and MMM_TRIGGER, never as arguments or through a shell, so $VARS in a command stay literal. To use one, let sh read it from the environment: ", h("span", { class: "mono" }, `sh -c 'say "$MMM_ARGS"'`), ". Saved to ", h("span", { class: "mono" }, data.path)),
    list,
    h("h2", {}, "Recent runs"), runs);
  loadRuns();
};

async function loadRuns() {
  const box = document.getElementById("runs");
  if (!box) return;
  const runs = await guard(() => api("GET", "/api/runs?limit=50"));
  if (!runs) return;
  box.replaceChildren(runs.length ? h("table", {},
    h("tr", {}, ["When", "From", "Rule", "Result", "Output"].map((t) => h("th", {}, t))),
    runs.map((r) => h("tr", {},
      h("td", {}, fmtTime(r.ts)), h("td", {}, r.test ? "test" : r.sender), h("td", { class: "mono" }, r.rule),
      h("td", { class: r.blocked ? "warn" : (r.exit === 0 ? "ok" : "bad") },
        r.blocked ? "blocked" : `exit ${r.exit ?? "—"}${r.timed_out ? " (timeout)" : ""} · ${r.ms} ms${r.replied ? " · replied" : ""}`),
      h("td", { class: "mono value" }, (r.stdout || r.stderr || "").slice(0, 200)))))
    : h("p", { class: "muted" }, "Nothing has run yet."));
}

// -------------------------------------------------------------------- phone

views.phone = async () => {
  const data = await guard(() => api("GET", "/api/phone"));
  if (!data || current !== "phone") return;
  const i = data.info, s = data.status;
  const bars = s.signalLevel == null ? "—" : "▮".repeat(s.signalLevel + 1) + "▯".repeat(4 - s.signalLevel);
  main.replaceChildren(
    h("div", { class: "row spread" }, h("h1", {}, `${s.manufacturer || ""} ${s.model || i.model}`),
      h("button", { class: "btn", onclick: () => show("phone") }, "Refresh")),
    h("div", { class: "grid" },
      card("Battery", h("div", { class: "big" }, s.batteryPercent == null ? "—" : `${s.batteryPercent}%`), h("div", { class: "muted" }, s.charging ? `charging${s.plugged ? ` · ${s.plugged.toUpperCase()}` : ""}` : (s.plugged ? "plugged in, not charging" : "on battery"))),
      card("Signal", h("div", { class: "big mono" }, bars), h("div", { class: "muted" }, s.networkOperator || "no operator")),
      card("Network", kv([["Mobile data", s.dataEnabled == null ? "—" : (s.dataEnabled ? "on" : "off")], ["SIM state", s.simState], ["Phone permission", s.phoneStatePermission ? "granted" : "missing"]])),
      card("System", kv([["Android", `${s.androidRelease} (API ${s.sdkInt})`], ["Root", i.root ? "yes" : "no"], ["Priv-app", i.priv_app ? "yes" : "no"]])),
      card("MMM", kv([["Version", i.version], ["Port", i.port], ["Tailnet", i.tailnet || "off"], ["Clients", i.clients]]))));
};

// -------------------------------------------------------------- mmm settings

const SETTING_NOTES = {
  port: "Restarts the listener; this page reconnects.",
  tailnet: "Also listen on the phone's Tailscale address. Restarts the listener.",
  startOnBoot: "Start the service when the phone boots.",
  backlogEvents: "Events kept per durable subscription while nobody is connected.",
  backlogHours: "How long those events are kept.",
  proxyReplyTimeoutMs: "How long the phone waits for a proxy reply.",
  handleCap: "Most handles one session may hold.",
  theme: "The app's own theme on the phone.",
};

views.mmm = async () => {
  const s = await guard(() => api("GET", "/api/mmm-settings"));
  if (!s || current !== "mmm") return;
  const set = async (key, value) => {
    const res = await guard(() => api("POST", "/api/mmm-settings", { key, value }));
    if (res) toast("Saved", `${key} = ${res[key]}`);
  };
  const rows = Object.entries(s).map(([key, value]) => {
    let input;
    if (typeof value === "boolean") input = toggle(value, (v) => set(key, v));
    else if (key === "theme") input = h("select", { onchange: (e) => set(key, e.target.value) }, ["SYSTEM", "LIGHT", "DARK"].map((t) => h("option", { value: t, selected: t === value }, t)));
    else {
      input = h("input", { type: "number", value });
      input.addEventListener("change", () => set(key, Number(input.value)));
    }
    return h("tr", {}, h("td", {}, h("div", { class: "mono" }, key), h("div", { class: "muted" }, SETTING_NOTES[key] || "")), h("td", {}, input));
  });
  main.replaceChildren(h("h1", {}, "MMM settings"), card(null, h("table", {}, rows)));
};

// ---------------------------------------------------------- android settings

let androidNs = "system";

views.android = async () => {
  const data = await guard(() => api("GET", `/api/settings/${androidNs}`));
  if (!data || current !== "android") return;
  const search = h("input", { type: "search", placeholder: `Search ${Object.keys(data).length} settings` });
  const body = h("tbody");
  const draw = () => {
    const q = search.value.toLowerCase();
    body.replaceChildren(...Object.entries(data)
      .filter(([k, v]) => !q || k.toLowerCase().includes(q) || String(v).toLowerCase().includes(q))
      .map(([name, value]) => {
        const input = h("input", { type: "text", value, class: "mono" });
        const save = h("button", { class: "btn", onclick: async () => {
          const res = await guard(() => api("PUT", `/api/settings/${androidNs}`, { name, value: input.value }));
          if (res) { data[name] = input.value; toast("Saved", `${androidNs}/${name} = ${input.value}`); }
        } }, "Save");
        return h("tr", {}, h("td", { class: "mono" }, name), h("td", { class: "value" }, h("div", { class: "row" }, input, save)));
      }));
  };
  search.addEventListener("input", draw);
  draw();
  main.replaceChildren(
    h("h1", {}, "Android settings"),
    h("div", { class: "row spread" },
      h("div", { class: "tabs", role: "tablist" }, ["system", "secure", "global"].map((ns) =>
        h("button", { role: "tab", "aria-selected": String(ns === androidNs), onclick: () => { androidNs = ns; show("android"); } }, ns))),
      search),
    h("p", { class: "muted" }, "Writing system settings needs the WRITE_SETTINGS appop; secure and global need WRITE_SECURE_SETTINGS (granted to the priv-app)."),
    card(null, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, "Name"), h("th", {}, "Value"))), body)));
};

// ------------------------------------------------------------------ console

const consoleState = { op: "call", target: "android.os.Build", member: "", args: "[]", history: [] };

function tree(value) {
  if (value && typeof value === "object" && "$h" in value) {
    return h("button", { class: "chip", title: "Use as target", onclick: () => { consoleState.target = value; show("console"); } }, `${value.cls} #${value.$h}`);
  }
  if (Array.isArray(value)) return h("span", {}, "[", value.flatMap((v, i) => [i ? ", " : "", tree(v)]), "]");
  if (value && typeof value === "object") {
    return h("div", {}, Object.entries(value).map(([k, v]) => h("div", {}, h("span", { class: "k" }, `${k}: `), tree(v))));
  }
  return JSON.stringify(value);
}

views.console = () => {
  const s = consoleState;
  const targetIsHandle = typeof s.target === "object";
  const op = h("select", {}, ["call", "get", "set", "expand", "cls"].map((o) => h("option", { value: o, selected: o === s.op }, o)));
  const target = h("input", { type: "text", class: "mono", value: targetIsHandle ? "" : s.target, placeholder: "class name, e.g. android.os.Build", hidden: targetIsHandle });
  const member = h("input", { type: "text", class: "mono", value: s.member, placeholder: "method or field" });
  const args = h("textarea", { class: "mono", rows: 3, value: s.args, placeholder: '[1, "two", {"$ctx": true}]' });
  const history = h("div", { class: "stack" }, s.history.map((e) => card(null,
    h("div", { class: "mono muted" }, e.label), h("div", { class: "tree" }, e.error ? h("span", { class: "bad" }, e.error) : tree(e.value)))));

  const run = async () => {
    Object.assign(s, { op: op.value, member: member.value, args: args.value });
    if (!targetIsHandle) s.target = target.value;
    let parsed = [];
    try { parsed = JSON.parse(args.value || "[]"); } catch (e) { toast("Arguments are not JSON", e.message, true); return; }
    const body = { op: s.op, target: s.target, member: s.member };
    if (s.op === "set") body.value = parsed; else body.args = Array.isArray(parsed) ? parsed : [parsed];
    const label = `${s.op} ${typeof s.target === "object" ? `#${s.target.$h}` : s.target}${s.member ? "." + s.member : ""}${s.op === "call" ? `(${args.value.replace(/^\[|\]$/g, "")})` : ""}`;
    try {
      const res = await api("POST", "/api/console", body);
      s.history.unshift({ label, value: res.value });
    } catch (e) {
      s.history.unshift({ label, error: `${e.type}: ${e.message}` });
    }
    s.history.length = Math.min(s.history.length, 30);
    show("console");
  };

  main.replaceChildren(
    h("h1", {}, "Console"),
    card(null,
      h("div", { class: "rule" },
        h("label", {}, "Op"), op,
        h("label", {}, "Target"), targetIsHandle
          ? h("div", { class: "row" }, tree(s.target), h("button", { class: "btn", onclick: () => { s.target = ""; show("console"); } }, "Clear"))
          : target,
        h("label", {}, "Member"), member,
        h("label", {}, "Arguments"), args),
      h("div", { class: "row" },
        h("button", { class: "btn primary", onclick: run }, "Run"),
        h("button", { class: "btn", onclick: () => { args.value = args.value.replace(/\]\s*$/, (args.value.trim() === "[]" ? "" : ", ") + '{"$ctx": true}]'); } }, "Insert context"),
        h("span", { class: "muted" }, "Handles stay on the daemon; click one to call it next."))),
    history);
};

// ---------------------------------------------------------------------- mac

views.mac = async () => {
  const draw = async () => {
    const m = await guard(() => api("GET", "/api/mac"));
    if (!m || current !== "mac") return;
    const up = (s) => `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
    const login = toggle(m.login.enabled, async (on, el) => {
      el.disabled = true;
      const res = await guard(() => api("POST", "/api/mac/login", { on }));
      if (res) toast(on ? "Starts at login" : "No longer starts at login", res.handover ? "Handing over to the system service; this page reconnects in a few seconds." : "");
      setTimeout(draw, 4000);
    }, !m.login.supported);
    main.replaceChildren(
      h("h1", {}, `Mac · ${m.host}`),
      h("div", { class: "grid" },
        card("Daemon", kv([["Status", h("span", { class: "ok" }, "running")], ["PID", m.pid], ["Up", up(m.uptime_s)], ["UI", m.ui], ["Run by", m.managed ? "the system (login item)" : "hand (mmm ui / serve)"]])),
        card("Phone link", kv([
          ["State", h("span", { class: m.link.connected ? "ok" : "bad" }, m.link.connected ? "connected" : "down")],
          ["Profile", m.profile], ["Transport", m.transport],
          ["Latency", m.link.latency_ms == null ? "—" : `${m.link.latency_ms} ms`],
          ["Last error", m.link.error]])),
        card("Start at login",
          h("div", { class: "row spread" }, h("span", {}, m.login.supported ? (m.login.enabled ? "On" : "Off") : "Not supported here"), login),
          h("p", { class: "muted" }, "Keeps the daemon (and your rules) running in the background, restarted if it dies.")),
        card("Rules", kv([["Rules", m.rules], ["Runs today", m.runs_today],
          ["Last run", m.last_run ? `${m.last_run.rule} · ${fmtTime(m.last_run.ts)} · exit ${m.last_run.exit ?? "—"}` : null]]))),
      h("h2", {}, "Log"),
      h("div", { class: "log" }, m.log.join("\n") || "(empty)"));
  };
  await draw();
  refreshTimer = setInterval(draw, 5000);
};

// -------------------------------------------------------------------- theme

const themePicker = document.getElementById("theme");
function applyTheme(value) {
  if (value) document.documentElement.dataset.theme = value;
  else delete document.documentElement.dataset.theme;
}
try { themePicker.value = localStorage.getItem("mmm-theme") || ""; } catch (_) { /* private mode */ }
applyTheme(themePicker.value);
themePicker.addEventListener("change", () => {
  applyTheme(themePicker.value);
  try { localStorage.setItem("mmm-theme", themePicker.value); } catch (_) { /* private mode */ }
});

// -------------------------------------------------------------------- start

if (!SECRET) banner("Open this page with `mmm ui`; it carries the secret the daemon wants.");
live();
let first = "messages";
try { first = localStorage.getItem("mmm-view") || first; } catch (_) { /* private mode */ }
show(views[first] ? first : "messages");
