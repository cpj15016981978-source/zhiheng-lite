/* 时中 M1 聊天壳：零依赖，所有内容用 textContent。
 * 会话列表 + 证据折叠。本地会话存 localStorage；服务端只负责历史/推理。
 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const STORE = "chuixue.sessions.v1";
  const MAX_SESSIONS = 20;
  const state = { sessions: [], activeId: null, sending: false, health: null, historyFilter: "all" };

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  function uid() {
    return "s" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  }

  function now() { return new Date().toISOString(); }

  function current() {
    return state.sessions.find((s) => s.id === state.activeId) || null;
  }

  function ensureSession() {
    let s = current();
    if (!s) {
      s = { id: uid(), title: "新会话", createdAt: now(), updatedAt: now(), messages: [] };
      state.sessions.unshift(s);
      state.activeId = s.id;
    }
    return s;
  }

  function save() {
    try {
      const data = state.sessions.slice(0, MAX_SESSIONS).map((s) => ({
        id: s.id, title: s.title, createdAt: s.createdAt, updatedAt: s.updatedAt,
        messages: s.messages.map((m) => ({ role: m.role, text: m.text, trace: m.trace || null })),
      }));
      localStorage.setItem(STORE, JSON.stringify({ v: 1, activeId: state.activeId, sessions: data }));
    } catch (_) { /* 隐私模式/配额满：不阻断聊天 */ }
  }

  function load() {
    try {
      const raw = localStorage.getItem(STORE);
      if (!raw) return false;
      const data = JSON.parse(raw);
      if (!data || !Array.isArray(data.sessions)) return false;
      state.sessions = data.sessions.filter((s) => s && s.id && Array.isArray(s.messages));
      state.sessions = state.sessions.slice(0, MAX_SESSIONS);
      state.activeId = data.activeId && state.sessions.some((s) => s.id === data.activeId)
        ? data.activeId : (state.sessions[0] ? state.sessions[0].id : null);
      return state.sessions.length > 0;
    } catch (_) { return false; }
  }

  function titleOf(text) {
    const t = String(text || "").replace(/\s+/g, " ").trim();
    return t.length > 18 ? t.slice(0, 18) + "…" : (t || "新会话");
  }

  async function api(path, body) {
    const opt = body === undefined ? {} : {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    };
    const r = await fetch(path, opt);
    let data = null;
    try { data = await r.json(); } catch (_) { data = { error: "响应不是 JSON" }; }
    if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
    return data;
  }

  function setStatus(text, cls) {
    const s = $("status");
    s.textContent = text;
    s.className = "status" + (cls ? " " + cls : "");
  }

  function fmtTime(iso) {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }

  function renderSessions() {
    const box = $("sessions");
    if (!box) return;
    box.textContent = "";
    if (!state.sessions.length) {
      box.appendChild(el("div", "muted small", "还没有会话"));
      return;
    }
    state.sessions.forEach((s) => {
      const row = el("div", "session-row" + (s.id === state.activeId ? " active" : ""));
      const b = el("button", "session-main");
      b.type = "button";
      b.appendChild(el("span", "session-title", s.title || "新会话"));
      b.appendChild(el("span", "session-meta", fmtTime(s.updatedAt) + " · " + s.messages.length + " 条"));
      b.addEventListener("click", () => {
        if (state.sending) return;
        state.activeId = s.id;
        save();
        renderSessions();
        render();
      });
      const acts = el("div", "session-actions");
      const ren = el("button", "session-act", "改名");
      ren.type = "button";
      ren.title = "重命名";
      ren.addEventListener("click", () => {
        if (state.sending) return;
        const name = window.prompt("会话名称", s.title || "新会话");
        if (name === null) return;
        const t = String(name).trim();
        if (!t) return;
        s.title = t.slice(0, 30);
        s.updatedAt = now();
        save();
        render();
      });
      const del = el("button", "session-act danger", "删除");
      del.type = "button";
      del.title = "删除会话";
      del.addEventListener("click", () => {
        if (state.sending) return;
        if (!window.confirm("删除「" + (s.title || "新会话") + "」？只删本机记录。")) return;
        state.sessions = state.sessions.filter((x) => x.id !== s.id);
        if (!state.sessions.length) {
          state.sessions.push({ id: uid(), title: "新会话", createdAt: now(), updatedAt: now(), messages: [] });
        }
        if (state.activeId === s.id) state.activeId = state.sessions[0].id;
        save();
        render();
      });
      acts.appendChild(ren);
      acts.appendChild(del);
      row.appendChild(b);
      row.appendChild(acts);
      box.appendChild(row);
    });
  }

  function evidence(m) {
    const t = m.trace || {};
    const recalled = Array.isArray(t.recalled) ? t.recalled : [];
    const world = Array.isArray(t.world) ? t.world
      : (Array.isArray(t.world_model) ? t.world_model
        : (Array.isArray(t.world_facts) ? t.world_facts
          : (Array.isArray(t.culture_refs) ? t.culture_refs : [])));
    const principles = Array.isArray(t.principles_used) ? t.principles_used : [];
    const models = Array.isArray(t.models) ? t.models : [];
    const uncertainty = Array.isArray(t.uncertainty) ? t.uncertainty
      : (t.uncertain ? [String(t.uncertain)] : []);
    const d = el("details", "evidence");
    const summary = el("summary", "");
    summary.appendChild(el("span", "note-kicker", "边注"));
    summary.appendChild(el("span", "note-count",
      "查 " + recalled.length + " · 推 " + (t.reason ? 1 : 0)
      + " · 疑 " + uncertainty.length));
    d.appendChild(summary);

    const body = el("div", "evidence-body");
    if (t.blocked || t.blocked_secret) {
      const b = el("div", "note-badges");
      if (t.blocked) b.appendChild(el("span", "badge bad", "已拦截"));
      if (t.blocked_secret) b.appendChild(el("span", "badge bad", "密钥拦截"));
      body.appendChild(b);
    }
    const sec = (name, cls, text) => {
      if (text === undefined || text === null || text === "") return;
      const s = el("div", "ev-sec " + cls);
      s.appendChild(el("div", "ev-name", name));
      s.appendChild(el("div", "ev-text", text));
      body.appendChild(s);
    };
    const listSec = (name, cls, arr, fmt) => {
      if (!arr.length) return;
      const s = el("div", "ev-sec " + cls);
      s.appendChild(el("div", "ev-name", name));
      arr.slice(0, 8).forEach((x) => {
        s.appendChild(el("div", "ev-text", "· " + fmt(x)));
      });
      body.appendChild(s);
    };
    const short = (x) => String(x === undefined || x === null ? "" : x).slice(0, 240);

    listSec("记忆 · 查到的", "e-mem-bg", recalled, (r) => {
      const item = (r && r.item) || {};
      return short(item.content || item.text || item.id || JSON.stringify(r));
    });
    listSec("世界 / 文化参考", "e-world-bg", world, (w) => {
      if (w && typeof w === "object") {
        return short(w.title || w.name || w.ref || w.text || w.content || JSON.stringify(w));
      }
      return short(w);
    });
    listSec("原则", "e-principle-bg", principles, short);

    if (t.reason) sec("推断 / 理由", "e-infer-bg", String(t.reason));
    if (t.r0_intent) sec("意图", "e-infer-bg", t.r0_intent + " / " + (t.r0_mode || ""));
    if (uncertainty.length) sec("不确定", "e-unknown-bg", uncertainty.join("；"));
    if (t.cost !== undefined || t.tokens !== undefined) {
      sec("花费", "e-infer-bg", "cost=" + (t.cost || 0) + " · tokens=" + (t.tokens || 0));
    }
    d.appendChild(body);

    const raw = el("details", "trace-raw");
    raw.appendChild(el("summary", "", "原始 trace"));
    raw.appendChild(el("pre", "", JSON.stringify(t, null, 2)));
    d.appendChild(raw);
    return d;
  }

  function render() {
    renderSessions();
    const box = $("messages");
    box.textContent = "";
    const s = current();
    const list = s ? s.messages : [];
    if (!list.length) {
      const e = el("div", "empty");
      e.appendChild(el("b", "", "还没有对话。"));
      e.appendChild(document.createTextNode("试试："));
      ["你还记得我上次说的吗", "帮我做个计划", "把文件保存到桌面"].forEach((t) => {
        e.appendChild(el("div", "", "· " + t));
      });
      box.appendChild(e);
      return;
    }
    list.forEach((m) => {
      const wrap = el("div", "msg " + m.role);
      wrap.appendChild(el("div", "who", m.role === "user" ? "你" : "时中"));
      const row = el("div", "msg-row");
      row.appendChild(el("div", "body", m.text));
      if (m.trace) {
        row.classList.add("has-note");
        row.appendChild(evidence(m));
      }
      wrap.appendChild(row);
      box.appendChild(wrap);
    });
    box.scrollTop = box.scrollHeight;
  }

  function add(role, text, trace) {
    const s = ensureSession();
    s.messages.push({ role, text, trace: trace || null });
    s.updatedAt = now();
    if (role === "user" && s.title === "新会话") s.title = titleOf(text);
    save();
    render();
  }

  async function send() {
    if (state.sending) return;
    const input = $("input");
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    state.sending = true;
    $("send").disabled = true;
    setStatus("streaming", "busy");
    add("user", text);

    const s = ensureSession();
    const assistant = { role: "assistant", text: "" };
    s.messages.push(assistant);
    render();
    let finished = false;

    function finish(err) {
      if (finished) return;
      finished = true;
      state.sending = false;
      $("send").disabled = false;
      if (err) {
        assistant.text = "出错了：" + err;
        setStatus("error", "bad");
      } else {
        setStatus("idle");
      }
      s.updatedAt = now();
      save();
      render();
      input.focus();
    }

    try {
      if (!window.EventSource) throw new Error("浏览器不支持 SSE");
      const es = new EventSource("/api/events?text=" + encodeURIComponent(text));
      es.addEventListener("start", () => { setStatus("thinking", "busy"); });
      es.addEventListener("delta", (ev) => {
        try {
          const d = JSON.parse(ev.data);
          if (d.text) assistant.text += d.text;
        } catch (_) { /* 忽略坏事件 */ }
        render();
      });
      es.addEventListener("done", (ev) => {
        try {
          const d = JSON.parse(ev.data);
          if (d.trace) assistant.trace = d.trace;
        } catch (_) { /* 忽略坏事件 */ }
        es.close();
        finish(null);
      });
      es.addEventListener("error", () => {
        es.close();
        finish("SSE 连接失败");
      });
    } catch (e) {
      finish(e.message);
    }
  }

  async function approvals() {
    const stateEl = $("appr-state");
    const list = $("appr-list");
    if (!stateEl || !list) return;
    list.textContent = "";
    try {
      const a = await api("/api/approvals");
      if (!a.available) {
        stateEl.textContent = a.reason || "审批服务不可用";
        return;
      }
      if (!a.items || !a.items.length) {
        stateEl.textContent = "没有待批";
        return;
      }
      stateEl.textContent = a.items.length + " 条待批";
      a.items.forEach((it) => list.appendChild(approvalRow(it)));
    } catch (e) {
      stateEl.textContent = "加载失败：" + e.message;
    }
  }

  async function approvalHistory() {
    const box = $("appr-history");
    if (!box) return;
    box.textContent = "加载中…";
    try {
      const h = await api("/api/approvals/history");
      if (!h.available) {
        box.textContent = h.reason || "历史不可用";
        return;
      }
      state.history = h.items || [];
      renderHistory();
    } catch (e) {
      box.textContent = "加载失败：" + e.message;
    }
  }

  function renderHistory() {
    const box = $("appr-history");
    if (!box) return;
    box.textContent = "";
    const f = state.historyFilter || "all";
    const items = (state.history || []).filter((x) => f === "all" || x.history_kind === f);
    if (!items.length) {
      box.appendChild(el("div", "muted small", "没有" + (f === "all" ? "历史" : "记录")));
      return;
    }
    items.forEach((it) => {
      const row = el("div", "appr-item");
      const kind = it.history_kind || "—";
      row.appendChild(el("div", "goal", it.goal || "未命名任务"));
      row.appendChild(el("div", "meta",
        (it.id || "") + " · " + ((it.payload && it.payload.tool) || "tool")
        + " · " + (it.status || "") + " · " + kind));
      row.appendChild(el("div", "meta",
        "created " + (it.created || "—")
        + (it.done_at ? " · done " + it.done_at : "")));
      box.appendChild(row);
    });
  }

  function approvalRow(it) {
    const row = el("div", "appr-item");
    row.appendChild(el("div", "goal", it.goal || "未命名任务"));
    row.appendChild(el("div", "meta",
      (it.id || "") + " · " + ((it.payload && it.payload.tool) || "tool") + " · " + (it.status || "")));
    const preview = el("div", "appr-preview");
    const prev = el("button", "preview", "预览 dry-run");
    prev.addEventListener("click", async () => {
      preview.textContent = "预览中…";
      try {
        const o = await api("/api/simulate", { id: it.id });
        const sim = o.simulation || o;
        preview.textContent = "";
        if (o.error) {
          preview.appendChild(el("div", "bad", "预览失败：" + o.error));
          return;
        }
        const lines = [
          ["decision", sim.decision || "—"],
          ["target", sim.target || "—"],
          ["before", sim.before_sha12 || sim.bytes_before || "—"],
          ["after", sim.after_sha12 || sim.bytes_after || "—"],
          ["verified", sim.verified ? "是" : (sim.cached ? "缓存" : "否")],
          ["note", sim.reason || sim.invalidated_reason || ""],
        ];
        lines.forEach(([k, v]) => {
          if (v === "" || v === null || v === undefined) return;
          const d = el("div", "prev-row");
          d.appendChild(el("span", "", k));
          d.appendChild(el("b", "", v));
          preview.appendChild(d);
        });
      } catch (e) {
        preview.textContent = "预览失败：" + e.message;
      }
    });
    const yes = el("button", "", "批准一次");
    yes.addEventListener("click", async () => {
      try { await api("/api/approve", { id: it.id }); await approvals(); }
      catch (e) { alert("批准失败：" + e.message); }
    });
    const no = el("button", "no", "拒绝");
    no.addEventListener("click", async () => {
      const reason = window.prompt("拒绝理由", "前端拒绝") || "前端拒绝";
      try { await api("/api/reject", { id: it.id, reason: reason }); await approvals(); }
      catch (e) { alert("拒绝失败：" + e.message); }
    });
    row.appendChild(prev);
    row.appendChild(yes);
    row.appendChild(no);
    row.appendChild(preview);
    return row;
  }

  async function health() {
    try {
      const h = await api("/api/health");
      state.health = h;
      $("h-model").textContent = h.model || "—";
      $("h-db").textContent = h.db_exists ? "可用" : "缺失";
      $("h-db").title = h.db || "";
      $("model").textContent = "模型：" + (h.model || "—");
      $("dot").className = "dot on";
      $("conn").textContent = "本机";
      try {
        const a = await api("/api/approvals");
        $("h-appr").textContent = !a.available ? "未配置"
          : (a.items && a.items.length ? a.items.length + " 待批" : "无待批");
      } catch (_) { $("h-appr").textContent = "不可用"; }
    } catch (e) {
      $("dot").className = "dot";
      $("conn").textContent = "离线";
      setStatus("offline", "bad");
    }
  }

  async function history() {
    const s = current();
    if (!s || s.messages.length) return;
    try {
      const h = await api("/api/history");
      const eps = (h.episodes || []).slice(-40);
      s.messages = eps.map((e) => ({
        role: e.role === "assistant" ? "assistant" : "user",
        text: e.content || "",
      }));
      if (eps.length && s.title === "新会话") {
        const firstUser = eps.find((e) => e.role !== "assistant");
        if (firstUser) s.title = titleOf(firstUser.content || "");
      }
      s.updatedAt = now();
      save();
      render();
    } catch (_) { /* 空状态 */ }
  }

  function newSession() {
    if (state.sending) return;
    const s = { id: uid(), title: "新会话", createdAt: now(), updatedAt: now(), messages: [] };
    state.sessions.unshift(s);
    state.sessions = state.sessions.slice(0, MAX_SESSIONS);
    state.activeId = s.id;
    save();
    render();
    $("input").focus();
  }

  function switchTab(name) {
    document.querySelectorAll("#tabs .tab").forEach((b) => {
      b.classList.toggle("active", b.dataset.tab === name);
    });
    document.querySelectorAll(".tabpane").forEach((p) => {
      p.classList.toggle("active", p.id === "tab-" + name);
    });
    if (name === "memory") loadMemory();
    if (name === "world") loadWorld();
    if (name === "approvals") { approvals(); approvalHistory(); }
    if (name === "self") loadSelf();
  }

  async function loadSelf() {
    const box = $("self-caps");
    const note = $("self-note");
    const calib = $("self-calib");
    const time = $("self-time");
    const cmet = $("self-cmetrics");
    if (!box) return;
    box.textContent = "加载中…";
    try {
      const s = await api("/api/self-report");
      box.textContent = "";
      if (note) note.textContent = s.note || "功能自检，不是体验声明。";
      if (time) time.textContent = "最近自检：" + (s.checked_at || "—");
      if (cmet) {
        const m = s.c_metrics || {};
        cmet.textContent = "C0 " + (m.c0_passed === undefined ? "—" : m.c0_passed)
          + "/" + (m.c0_total === undefined ? "—" : m.c0_total)
          + " · 身份 v" + (m.identity_version === undefined ? "—" : m.identity_version)
          + " · 原则 " + (m.principles_active === undefined ? "—" : m.principles_active)
          + " · 改口 " + (m.revisions === undefined ? "—" : m.revisions)
          + " · " + (m.healthy ? "一致" : "有告警");
      }
      const scoreBox = $("self-score");
      if (scoreBox) {
        scoreBox.textContent = "";
        const cs = s.c_score || {};
        if (cs.label) scoreBox.appendChild(el("div", "muted small", cs.label));
        (cs.items || []).forEach((it) => {
          const row = el("div", "cap-row");
          row.appendChild(el("div", "cap-name", it.id + " · " + (it.score === 2 ? "2 分" : it.score + " 分")));
          row.appendChild(el("div", "cap-ev", it.evidence || ""));
          scoreBox.appendChild(row);
        });
      }
      (s.capabilities || []).forEach((c) => {
        const st = c.status || "—";
        const cls = st === "有" ? "ok" : (st === "部分" ? "part" : "no");
        const badge = el("span", "cap-badge " + cls, st);
        const ev = el("div", "cap-ev", c.evidence || "");
        if (st === "有") {
          const row = el("div", "cap-row");
          row.appendChild(el("div", "cap-name", c.name || ""));
          row.appendChild(badge);
          row.appendChild(ev);
          box.appendChild(row);
        } else {
          const d = el("details", "cap-details");
          const sm = el("summary", "cap-summary");
          sm.appendChild(el("span", "cap-name", c.name || ""));
          sm.appendChild(badge);
          sm.appendChild(el("span", "cap-open", "证据"));
          d.appendChild(sm);
          d.appendChild(ev);
          box.appendChild(d);
        }
      });
      if (!(s.capabilities || []).length) box.appendChild(el("div", "muted small", "没有自检项"));
      if (calib) {
        const c = s.calibration || {};
        calib.textContent = c.measured ? "已校准" : ("未校准：" + (c.reason || "—"));
      }
    } catch (e) {
      box.textContent = "加载失败：" + e.message;
    }
  }

  function factLine(f, query) {
    const row = el("div", "mem-row");
    const main = el("div", "mem-main");
    const text = (f.subject || "") + " · " + (f.predicate || "") + " · " + (f.object || "");
    if (query && text.toLowerCase().indexOf(query.toLowerCase()) < 0) return null;
    main.appendChild(el("div", "mem-text", text));
    main.appendChild(el("div", "mem-meta",
      "置信 " + (f.confidence === undefined ? "—" : f.confidence)
      + " · 来源 " + (f.source_episode_id === undefined ? "—" : f.source_episode_id)
      + " · #" + (f.id === undefined ? "—" : f.id)));
    row.appendChild(main);
    if (f.id !== undefined && f.id !== null) {
      const del = el("button", "session-act danger", "忘掉");
      del.type = "button";
      del.title = "删除这条事实";
      del.addEventListener("click", async () => {
        const reason = window.prompt("删除理由（可选；会留审计，不写回事实）", "");
        if (reason === null) return;
        if (!window.confirm("删除这条记忆？不可撤销。\n" + text)) return;
        try {
          await api("/api/forget", { id: f.id, reason: reason.trim() });
          await loadMemory();
        } catch (e) { alert("删除失败：" + e.message); }
      });
      row.appendChild(del);
    }
    return row;
  }

  async function loadCards() {
    const box = $("mem-cards");
    if (!box) return;
    box.textContent = "加载中…";
    try {
      const r = await api("/api/memory/cards");
      box.textContent = "";
      if (!r.items || !r.items.length) {
        box.appendChild(el("div", "muted small", "还没有记忆卡"));
        return;
      }
      r.items.forEach((c) => {
        const row = el("div", "mem-row");
        row.appendChild(el("div", "mem-text",
          "[" + (c.kind === "promise" ? "承诺" : c.kind === "story" ? "故事" : "事") + "] "
          + (c.at || "") + " · " + (c.summary || "").split("\n")[0]));
        if (c.summary && c.summary.includes("\n")) {
          row.appendChild(el("div", "mem-meta", c.summary.split("\n").slice(1).join(" · ")));
        }
        box.appendChild(row);
      });
    } catch (e) {
      box.textContent = "记忆卡加载失败：" + e.message;
    }
  }

  async function saveMemoryCard() {
    const msg = $("mem-card-msg");
    const what = ($("mem-what").value || "").trim();
    if (!what) {
      if (msg) msg.textContent = "先写发生了什么";
      return;
    }
    if (msg) msg.textContent = "写入中…";
    try {
      await api("/api/memory/card", {
        kind: $("mem-kind").value,
        person: $("mem-person").value || "",
        what: what,
        why: $("mem-why").value || "",
        next_step: $("mem-next").value || "",
      });
      $("mem-what").value = "";
      $("mem-why").value = "";
      $("mem-next").value = "";
      if (msg) msg.textContent = "已记下";
      await loadCards();
    } catch (e) {
      if (msg) msg.textContent = "没记下：" + e.message;
    }
  }

  async function loadMemory() {
    const factsBox = $("mem-facts");
    const prBox = $("mem-principles");
    if (!factsBox || !prBox) return;
    factsBox.textContent = "加载中…";
    prBox.textContent = "";
    try {
      const s = await api("/api/state");
      state.memory = s;
      factsBox.textContent = "";
      const query = ($("mem-search").value || "").trim();
      const facts = Array.isArray(s.facts) ? s.facts : [];
      let shown = 0;
      facts.forEach((f) => {
        const row = factLine(f, query);
        if (row) { factsBox.appendChild(row); shown++; }
      });
      if (!shown) factsBox.appendChild(el("div", "muted small", "没有匹配的事实"));
      prBox.textContent = "";
      const ps = Array.isArray(s.principles) ? s.principles : [];
      if (!ps.length) prBox.appendChild(el("div", "muted small", "还没有原则"));
      ps.forEach((p) => {
        const row = el("div", "mem-row");
        row.appendChild(el("div", "mem-text", p.text || p.content || JSON.stringify(p)));
        row.appendChild(el("div", "mem-meta", "active=" + (p.active === undefined ? "—" : p.active)));
        prBox.appendChild(row);
      });
    } catch (e) {
      factsBox.textContent = "加载失败：" + e.message;
    }
  }

  function worldSection(box, title, items, fmt) {
    if (!box) return;
    if (!items.length) {
      box.appendChild(el("div", "muted small", "没有" + title));
      return;
    }
    items.forEach((x) => {
      const row = el("div", "mem-row");
      row.appendChild(el("div", "mem-text", fmt(x)));
      box.appendChild(row);
    });
  }

  async function addWorldEntity() {
    const msg = $("world-add-msg");
    const name = ($("world-add-name").value || "").trim();
    if (!name) {
      if (msg) msg.textContent = "先填名称";
      return;
    }
    if (msg) msg.textContent = "写入中…";
    try {
      await api("/api/world/add", {
        kind: "entity",
        type: $("world-add-type").value,
        name: name,
        aliases: $("world-add-aliases").value || "",
      });
      $("world-add-name").value = "";
      $("world-add-aliases").value = "";
      if (msg) msg.textContent = "已添加（临时库）";
      await loadWorld();
    } catch (e) {
      if (msg) msg.textContent = "添加失败：" + e.message;
    }
  }

  async function prepareWorld(cid) {
    const box = $("world-gate");
    if (box) box.textContent = "准备中…";
    try {
      await api("/api/world/approval/prepare", { candidate_id: cid });
      await loadWorldGate();
    } catch (e) {
      if (box) box.textContent = "准备失败：" + e.message;
    }
  }

  async function loadWorldGate() {
    const box = $("world-gate");
    if (!box) return;
    box.textContent = "加载中…";
    try {
      const g = await api("/api/world/approvals");
      box.textContent = "";
      if (!g.items || !g.items.length) {
        box.appendChild(el("div", "muted small", "没有写入请求"));
        return;
      }
      g.items.slice().reverse().forEach((it) => {
        const row = el("div", "mem-row");
        row.appendChild(el("div", "mem-text",
          "[" + it.kind + "] " + (it.name || it.candidate_id)
          + " · " + (it.used ? "已执行" : (it.approved ? "已批准" : "待批"))));
        row.appendChild(el("div", "mem-meta", it.request + " · " + (it.db || "")));
        if (!it.approved) {
          const b = el("button", "session-act", "批准");
          b.type = "button";
          b.addEventListener("click", async () => {
            try {
              const token = ($("world-gate-token") || {}).value || "";
              await api("/api/world/approval/approve",
                { request: it.request, by: "Boss", token: token });
              await loadWorldGate();
            } catch (e) { alert("批准失败：" + e.message); }
          });
          row.appendChild(b);
        } else if (!it.used && it.approval) {
          const b = el("button", "session-act", "执行");
          b.type = "button";
          b.addEventListener("click", async () => {
            try {
              await api("/api/world/approval/execute",
                { request: it.request, approval: it.approval });
              await loadWorld();
              await loadWorldGate();
            } catch (e) { alert("执行失败：" + e.message); }
          });
          row.appendChild(b);
        }
        box.appendChild(row);
      });
    } catch (e) {
      box.textContent = "审批加载失败：" + e.message;
    }
  }

  async function loadWorld() {
    const eBox = $("world-entities");
    const rBox = $("world-relations");
    const vBox = $("world-events");
    const cBox = $("world-candidates");
    if (!eBox || !rBox || !vBox) return;
    loadWorldGate();
    eBox.textContent = "加载中…";
    rBox.textContent = "";
    vBox.textContent = "";
    if (cBox) {
      cBox.textContent = "";
      try {
        const cs = await api("/api/world/candidates");
        if (!cs.items || !cs.items.length) {
          cBox.appendChild(el("div", "muted small", "没有影子候选"));
        } else {
          cs.items.forEach((c) => {
            const row = el("div", "mem-row");
            row.appendChild(el("div", "mem-text",
              "[" + c.kind + "] " + c.text + "  · " + c.status));
            if (c.evidence) row.appendChild(el("div", "mem-meta", "证据：" + c.evidence));
            if (c.status === "待审") {
              const b = el("button", "session-act", "送审");
              b.type = "button";
              b.addEventListener("click", () => prepareWorld(c.id));
              row.appendChild(b);
            }
            cBox.appendChild(row);
          });
        }
      } catch (e) {
        cBox.textContent = "候选加载失败：" + e.message;
      }
    }
    try {
      const w = await api("/api/world");
      state.world = w;
      eBox.textContent = "";
      rBox.textContent = "";
      vBox.textContent = "";
      const total = (w.entities || []).length + (w.relations || []).length + (w.events || []).length;
      const empty = $("world-empty");
      if (empty) empty.style.display = total ? "none" : "block";
      worldSection(eBox, "实体", w.entities || [], (x) =>
        (x.name || x.id) + " · " + (x.type || "") + " · " + (x.id || ""));
      worldSection(rBox, "关系", w.relations || [], (x) =>
        (x.subject_name || x.subject_id) + " —" + (x.predicate || "") + "→ "
        + (x.object_name || x.object_value || x.object_id || ""));
      worldSection(vBox, "事件", w.events || [], (x) =>
        (x.at || "") + " · " + (x.kind || "") + " · " + (x.summary || ""));
    } catch (e) {
      const empty = $("world-empty");
      if (empty) {
        empty.style.display = "block";
        empty.textContent = "世界面板加载失败：" + e.message;
      }
      eBox.textContent = "加载失败：" + e.message;
    }
  }

  function init() {
    load();
    if (!state.sessions.length) {
      state.sessions.push({ id: uid(), title: "新会话", createdAt: now(), updatedAt: now(), messages: [] });
      state.activeId = state.sessions[0].id;
      save();
    }
    $("send").addEventListener("click", send);
    $("composer").addEventListener("submit", (e) => { e.preventDefault(); send(); });
    $("input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        send();
      }
    });
    $("new").addEventListener("click", newSession);
    $("appr-refresh").addEventListener("click", approvals);
    $("world-add-btn").addEventListener("click", addWorldEntity);
    const pt = $("panel-toggle");
    if (pt) {
      const hidden = localStorage.getItem("chuixue.panelHidden") === "1";
      document.getElementById("app").classList.toggle("panel-hidden", hidden);
      pt.textContent = hidden ? "⟨" : "⟩";
      pt.addEventListener("click", () => {
        const h = !document.getElementById("app").classList.contains("panel-hidden");
        document.getElementById("app").classList.toggle("panel-hidden", h);
        localStorage.setItem("chuixue.panelHidden", h ? "1" : "0");
        pt.textContent = h ? "⟨" : "⟩";
      });
    }
    document.querySelectorAll(".htab").forEach((b) => {
      b.addEventListener("click", () => {
        state.historyFilter = b.dataset.hist || "all";
        document.querySelectorAll(".htab").forEach((x) => x.classList.toggle("active", x === b));
        renderHistory();
      });
    });
    document.querySelectorAll("#tabs .tab").forEach((b) => {
      b.addEventListener("click", () => switchTab(b.dataset.tab));
    });
    $("mem-search").addEventListener("input", loadMemory);
    $("mem-save").addEventListener("click", saveMemoryCard);
    loadCards();
    health();
    approvals();
    approvalHistory();
    history();
    render();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
