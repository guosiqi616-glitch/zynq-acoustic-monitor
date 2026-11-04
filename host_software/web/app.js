(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  const app = $("app");
  const modeEl = $("mode");
  const stateEl = $("state");
  const dotEl = $("dot");
  const fpsEl = $("fps");
  const latEl = $("lat");
  const berEl = $("ber");
  const berStateEl = $("berState");
  const thrEl = $("thr");
  const floorEl = $("floor");
  const sessionEl = $("session");
  const lockEl = $("lock");
  const detEl = $("det");
  const suppEl = $("supp");
  const splitLowEl = $("split-low");
  const splitHighEl = $("split-high");
  const splitLowFill = $("split-low-fill");
  const splitHighFill = $("split-high-fill");
  const lastLockEl = $("last-lock");
  const lastTimeEl = $("last-time");
  const lastBerEl = $("last-ber");
  const barLow = $("bar-low");
  const barHigh = $("bar-high");
  const capLow = $("cap-low");
  const capHigh = $("cap-high");
  const dbLowEl = $("db-low");
  const dbHighEl = $("db-high");
  const tempEl = $("temp");
  const humEl = $("hum");
  const rxEl = $("rx");
  const integrityEl = $("integrity");
  const healthStateEl = $("healthState");
  const eventsEl = $("events");
  const flashEl = $("flash");
  const snapshotEl = $("snapshot");
  const snapLockEl = $("snap-lock");
  const snapTimeEl = $("snap-time");
  const snapLowEl = $("snap-low");
  const snapHighEl = $("snap-high");
  const snapBerEl = $("snap-ber");
  const snapThrEl = $("snap-thr");
  const debugEl = $("debug");
  const toastEl = $("toast");
  const dashboard = $("dashboard");
  const resetBtn = $("reset-layout");
  const sideStatusEl = $("side-status");
  const sideDotEl = $("side-dot");
  const colLeft = document.querySelector(".col-left");
  const colRight = document.querySelector(".col-right");
  const gaugePanel = document.querySelector('.panel[data-panel="gauge"]');
  const envPanel = document.querySelector('.panel[data-panel="env"]');
  const eventsPanel = document.querySelector('.panel[data-panel="events"]');

  const S = {
    scale: { temp: 0.01, hum: 0.01, db: 0.1, ber: 0.001 },
    latest: null,
    status: null,
    hello: false,
    wsOpen: false,
    ws: null,
    retry: 1000,
    closedAt: performance.now(),
    lastT: 0,
    lastFrame: performance.now(),
    dirty: false,
    frozen: false,
    snapTimer: 0,
    peak: { low: null, high: null },
    mode: "SIM",
    session: 1,
    thr: 0.813,
    state: "CONNECTING",
    protoWarned: false,
    toastTimer: 0,
  };

  // ------------------------------------------------------------------
  // 联动分割布局：拖动分隔条，相邻模块一起让位
  // ------------------------------------------------------------------
  const SPLIT_DEFAULT = { left: 20, right: 20, gauge: 30, env: 32, events: 24 };
  const SPLIT_KEY = "dashboard-split-v2";
  let splitSizes = Object.assign({}, SPLIT_DEFAULT);
  let splitDrag = null;

  function applySplit() {
    colLeft.style.flexBasis = splitSizes.left + "%";
    colRight.style.flexBasis = splitSizes.right + "%";
    gaugePanel.style.flexBasis = splitSizes.gauge + "%";
    envPanel.style.flexBasis = splitSizes.env + "%";
    eventsPanel.style.flexBasis = splitSizes.events + "%";
  }

  function loadSplit() {
    try {
      const saved = JSON.parse(localStorage.getItem(SPLIT_KEY) || "null");
      if (saved && typeof saved === "object") {
        Object.keys(SPLIT_DEFAULT).forEach(function (k) {
          if (typeof saved[k] === "number" && isFinite(saved[k])) {
            splitSizes[k] = saved[k];
          }
        });
      }
    } catch (e) { /* ignore */ }
  }

  function saveSplit() {
    try {
      localStorage.setItem(SPLIT_KEY, JSON.stringify(splitSizes));
    } catch (e) { /* ignore */ }
  }

  function resetSplit() {
    splitSizes = Object.assign({}, SPLIT_DEFAULT);
    applySplit();
    saveSplit();
    if (window.Charts) window.Charts.resize();
    toast("已恢复默认布局");
  }

  function onSplitDown(ev, splitter) {
    if (ev.button !== 0 || window.innerWidth <= 1100) return;
    splitDrag = {
      kind: splitter.dataset.split,
      startX: ev.clientX,
      startY: ev.clientY,
      start: Object.assign({}, splitSizes),
      width: dashboard.clientWidth,
      height: dashboard.clientHeight,
      rightHeight: colRight.getBoundingClientRect().height,
      raf: 0,
    };
    splitter.classList.add("active");
    try { splitter.setPointerCapture(ev.pointerId); } catch (e) { /* ignore */ }
    ev.preventDefault();
  }

  function onSplitMove(ev) {
    if (!splitDrag) return;
    const d = splitDrag;
    const dx = ((ev.clientX - d.startX) / d.width) * 100;
    const dy = ((ev.clientY - d.startY) / d.height) * 100;
    const s = d.start;

    if (d.kind === "left") {
      splitSizes.left = clamp(s.left + dx, 12, 100 - s.right - 30);
    } else if (d.kind === "right") {
      splitSizes.right = clamp(s.right - dx, 12, 100 - s.left - 30);
    } else if (d.kind === "gauge-env") {
      const dyr = ((ev.clientY - d.startY) / d.rightHeight) * 100;
      splitSizes.gauge = clamp(s.gauge + dyr, 14, 100 - s.env - 18);
    } else if (d.kind === "env-health") {
      const dyr = ((ev.clientY - d.startY) / d.rightHeight) * 100;
      splitSizes.env = clamp(s.env + dyr, 14, 100 - s.gauge - 18);
    } else if (d.kind === "bottom") {
      splitSizes.events = clamp(s.events - dy, 14, 46);
    }

    applySplit();
    if (window.Charts && !d.raf) {
      d.raf = requestAnimationFrame(function () {
        if (splitDrag) splitDrag.raf = 0;
        if (window.Charts) window.Charts.resize();
      });
    }
    ev.preventDefault();
  }

  function onSplitUp() {
    if (!splitDrag) return;
    document.querySelectorAll(".splitter.active").forEach(function (el) {
      el.classList.remove("active");
    });
    splitDrag = null;
    saveSplit();
    if (window.Charts) window.Charts.resize();
  }

  function initSplitters() {
    loadSplit();
    applySplit();
    document.querySelectorAll(".splitter").forEach(function (el) {
      el.addEventListener("pointerdown", function (ev) {
        onSplitDown(ev, el);
      });
    });
    if (resetBtn) resetBtn.addEventListener("click", resetSplit);
    window.addEventListener("pointermove", onSplitMove, { passive: false });
    window.addEventListener("pointerup", onSplitUp);
    window.addEventListener("pointercancel", onSplitUp);
  }

  // ------------------------------------------------------------------
  // 基础工具
  // ------------------------------------------------------------------
  function setText(el, value) {
    if (el && el.textContent !== value) el.textContent = value;
  }

  function setState(st) {
    if (S.state === st) return;
    S.state = st;
    app.dataset.state = st;
    const labels = {
      LIVE: "系统在线",
      STALE: "数据延迟",
      LOST: "连接丢失",
      CONNECTING: "连接中",
    };
    setText(stateEl, labels[st] || st);
    stateEl.className = "pill state " + st.toLowerCase();
    dotEl.className = "live-dot " + st.toLowerCase();

    if (sideStatusEl) sideStatusEl.textContent = labels[st] || st;
    if (sideDotEl) sideDotEl.className = "side-dot " + st.toLowerCase();
  }

  function inferMode(source) {
    const s = String(source || "");
    if (s.indexOf("serial") >= 0) return "BOARD";
    if (s.indexOf("replay") >= 0 || s.indexOf("file") >= 0) return "REPLAY";
    return "SIM";
  }

  function setMode(mode) {
    const m = String(mode || "SIM").toUpperCase();
    if (S.mode === m) return;
    S.mode = m;
    setText(modeEl, { BOARD: "板卡", SIM: "模拟", REPLAY: "回放" }[m] || m);
    modeEl.className = "pill mode " + m.toLowerCase();
  }

  function toast(text) {
    setText(toastEl, text);
    toastEl.classList.add("on");
    clearTimeout(S.toastTimer);
    S.toastTimer = setTimeout(function () {
      toastEl.classList.remove("on");
    }, 2200);
  }

  function fmtTime(us) {
    const s = Math.max(0, Number(us || 0) / 1e6);
    const m = Math.floor(s / 60);
    const rest = (s - m * 60).toFixed(3).padStart(6, "0");
    return String(m).padStart(2, "0") + ":" + rest;
  }

  function fmtBer(v) {
    return v == null ? "\u2014" : (Number(v) * S.scale.ber).toFixed(3);
  }

  function fmtDb(v) {
    return v == null ? "\u2014" : (Number(v) * S.scale.db).toFixed(1);
  }

  function fmtTemp(v) {
    return v == null ? "\u2014" : (Number(v) * S.scale.temp).toFixed(1);
  }

  function fmtHum(v) {
    return v == null ? "\u2014" : (Number(v) * S.scale.hum).toFixed(1);
  }

  function pct(db) {
    return clamp((db + 100) / 60, 0, 1);
  }

  // ------------------------------------------------------------------
  // 消息
  // ------------------------------------------------------------------
  function applyIntegrity(s) {
    const items = [
      ["丢帧", s.lost, (s.lost || 0) === 0],
      ["事件缺失", s.e_missing, (s.e_missing || 0) === 0],
      ["不变量", s.invariant_ok ? "OK" : "FAIL", !!s.invariant_ok],
      ["FIFO 溢出", s.ovf, (s.ovf || 0) === 0],
      ["解析错误", s.errors, (s.errors || 0) === 0],
      ["设备错误", s.dev_err, (s.dev_err || 0) === 0],
    ];

    integrityEl.textContent = "";
    let allOk = true;
    items.forEach(function (it) {
      const ok = !!it[2];
      if (!ok) allOk = false;
      const row = document.createElement("div");
      row.className = "h-item";
      const k = document.createElement("span");
      k.textContent = it[0];
      const v = document.createElement("b");
      v.textContent = String(it[1] == null ? 0 : it[1]);
      v.className = ok ? "ok" : "bad";
      row.append(k, v);
      integrityEl.append(row);
    });

    setText(healthStateEl, allOk ? "全部正常" : "需检查");
    healthStateEl.className = "hint " + (allOk ? "ok" : "bad");
    setText(rxEl, s.rx == null ? "--" : String(s.rx));
  }

  function applyStatus(s) {
    if (!s) return;
    S.status = s;
    setMode(s.mode || inferMode(s.source));
    setText(fpsEl, (Number(s.fps) || 0).toFixed(1));
    applyIntegrity(s);

    if (s.protocol_ok === false && !S.protoWarned) {
      S.protoWarned = true;
      toast("协议不兼容：" + (s.protocol_error || "检查固件版本"));
    }
  }

  // ------------------------------------------------------------------
  // 校准用：60 秒窗口内的本底 / 峰值（dB，内部按 0.1 dB 存）
  //   调麦克风模块电位器时看这两个数：安静时本底别高于 −55，
  //   放音时「峰 − 本底」要能做到 10 dB 以上，才够判据用。
  // ------------------------------------------------------------------
  function trackLevel(t) {
    const vals = [t.db_low, t.db_high].filter(function (v) { return v != null; });
    if (!vals.length) return;
    const hi = Math.max.apply(null, vals);
    const now = performance.now();
    if (!S.level) S.level = { t0: now, min: hi, max: hi };
    if (hi < S.level.min) S.level.min = hi;
    if (hi > S.level.max) S.level.max = hi;
    if (now - S.level.t0 > 60000) {
      S.level.t0 = now;
      S.level.min = hi;
      S.level.max = hi;
    }
    setText(floorEl, "本底 " + (S.level.min / 10).toFixed(1) +
      " · 峰 " + (S.level.max / 10).toFixed(1) + " dB");
    floorEl.className = (S.level.max - S.level.min) >= 100 ? "ok" : "";
  }

  function applyTelemetry(t) {
    if (!t) return;
    S.latest = t;
    S.dirty = true;
    S.lastT = performance.now();
    S.hello = true;
    if (t.session != null) S.session = t.session;
    trackLevel(t);
    if (S.state !== "LIVE") setState("LIVE");
  }

  function handleHello(msg) {
    if (msg.scale) S.scale = msg.scale;
    S.hello = true;
    S.lastT = performance.now();
    applyStatus(msg.status);
    if (msg.state) {
      S.latest = msg.state;
      S.dirty = true;
      if (msg.state.session != null) S.session = msg.state.session;
    }
    const list = Array.isArray(msg.events) ? msg.events : [];
    eventsEl.textContent = "";
    if (list.length) {
      list.forEach(function (e) { addEvent(e, true); });
      setLastLock(list[list.length - 1]);
      // 快照里的历史事件是静默渲染（silent=true），不会更新“阈值”文字，
      // 这里用最后一条事件的 thr 补一次，避免页面长期停在默认值上。
      var lastThr = list[list.length - 1].thr;
      if (lastThr != null) {
        S.thr = Number(lastThr) * S.scale.ber;
        setText(thrEl, "阈值 " + fmtBer(lastThr));
      }
    } else {
      S.thr = 0.813;
      setText(thrEl, "阈值 0.813");
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "等待锁存事件…";
      eventsEl.append(li);
    }
    setState("LIVE");
  }

  function handleEvent(e) {
    S.lastT = performance.now();
    if (e.thr != null) S.thr = Number(e.thr) * S.scale.ber;
    addEvent(e, false);
    playLock(e);
  }

  function handleSession(msg) {
    S.session = msg.id || S.session + 1;
    S.peak.low = null;
    S.peak.high = null;
    S.thr = 0.813;
    S.frozen = false;
    snapshotEl.classList.remove("on");
    clearTimeout(S.snapTimer);
    setText(lockEl, "0");
    setText(detEl, "0");
    setText(suppEl, "0");
    setText(thrEl, "阈值 0.813");
    setText(sessionEl, "会话 " + S.session);
    setLastLock(null);
    setText(splitLowEl, "--");
    setText(splitHighEl, "--");
    splitLowFill.style.width = "0%";
    splitHighFill.style.width = "0%";
    eventsEl.textContent = "";
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "新会话，等待事件…";
    eventsEl.append(li);
    if (window.Charts) window.Charts.reset();
    toast("设备重启 #" + S.session);
  }

  function dispatch(msg) {
    switch (msg.type) {
      case "hello": handleHello(msg); break;
      case "telemetry": applyTelemetry(msg); break;
      case "event": handleEvent(msg); break;
      case "status": applyStatus(msg); break;
      case "session": handleSession(msg); break;
      case "pong": break;
      default: break;
    }
  }

  // ------------------------------------------------------------------
  // 事件时间线
  // ------------------------------------------------------------------
  function addEvent(e, silent) {
    const empty = eventsEl.querySelector(".empty");
    if (empty) empty.remove();

    eventsEl.querySelectorAll("li.fresh").forEach(function (li) {
      li.classList.remove("fresh");
    });

    const li = document.createElement("li");
    li.className = "fresh";

    const dot = document.createElement("span");
    dot.className = "ev-dot";

    const lock = document.createElement("span");
    lock.className = "ev-lock";
    lock.textContent = "#" + (e.lock == null ? "?" : e.lock);

    const time = document.createElement("span");
    time.className = "ev-time";
    time.textContent = fmtTime(e.t_us);

    const gap = document.createElement("span");

    const ber = document.createElement("span");
    ber.className = "ev-ber";
    ber.textContent = "BER " + fmtBer(e.ber);

    const thr = document.createElement("span");
    thr.className = "ev-thr";
    thr.textContent = "thr " + fmtBer(e.thr);

    li.append(dot, lock, time, gap, ber, thr);
    eventsEl.prepend(li);

    while (eventsEl.children.length > 5) eventsEl.lastElementChild.remove();

    if (!silent) {
      setText(thrEl, "阈值 " + fmtBer(e.thr));
    }
  }

  // ------------------------------------------------------------------
  // 锁存演出
  // ------------------------------------------------------------------
  function setLastLock(e) {
    if (!e) {
      setText(lastLockEl, "--");
      setText(lastTimeEl, "--");
      setText(lastBerEl, "--");
      return;
    }
    setText(lastLockEl, "#" + (e.lock == null ? "?" : e.lock));
    setText(lastTimeEl, fmtTime(e.t_us));
    setText(lastBerEl, fmtBer(e.ber));
  }

  function playLock(e) {
    flashEl.classList.remove("on");
    void flashEl.offsetWidth;
    flashEl.classList.add("on");
    setTimeout(function () { flashEl.classList.remove("on"); }, 180);

    // 不再冻结画面：事件只弹提示，数据继续刷新

    setText(snapLockEl, "#" + (e.lock == null ? "?" : e.lock));
    setText(snapTimeEl, fmtTime(e.t_us));
    setText(snapLowEl, fmtDb(e.db_low) + " dB");
    setText(snapHighEl, fmtDb(e.db_high) + " dB");
    setText(snapBerEl, fmtBer(e.ber));
    setText(snapThrEl, fmtBer(e.thr));
    setLastLock(e);
    snapshotEl.classList.add("on");

    clearTimeout(S.snapTimer);
    S.snapTimer = setTimeout(function () {
      S.frozen = false;
      S.lastFrame = performance.now();
      snapshotEl.classList.remove("on");
    }, 4000);
  }

  // ------------------------------------------------------------------
  // 渲染
  // ------------------------------------------------------------------
  function render(now) {
    const d = S.latest;
    if (!d) return;

    const ber = d.ber == null ? null : Number(d.ber) * S.scale.ber;
    const st = d.state == null ? 0 : Number(d.state);

    setText(berEl, ber == null ? "\u2014" : ber.toFixed(3));
    berEl.className = "ber" + (st === 2 ? " alarm" : st === 1 ? " warn" : "");

    setText(berStateEl, st === 2 ? "报警 · 已锁存" : st === 1 ? "预警" : "正常");
    berStateEl.className = "state-chip " + (st === 2 ? "alarm" : st === 1 ? "warn" : "normal");

    if (ber == null) {
      setText(splitLowEl, "--");
      setText(splitHighEl, "--");
      splitLowFill.style.width = "0%";
      splitHighFill.style.width = "0%";
    } else {
      const low = clamp(1 - ber, 0, 1);
      setText(splitLowEl, Math.round(low * 100) + "%");
      setText(splitHighEl, Math.round(ber * 100) + "%");
      splitLowFill.style.width = (low * 100).toFixed(1) + "%";
      splitHighFill.style.width = (ber * 100).toFixed(1) + "%";
    }

    setText(sessionEl, "会话 " + (d.session == null ? S.session : d.session));
    setText(lockEl, String(d.lock == null ? 0 : d.lock));
    setText(detEl, String(d.det == null ? 0 : d.det));
    setText(suppEl, String(d.supp == null ? 0 : d.supp));

    const lat = d.latency_ms;
    setText(latEl, lat == null ? "--" : Number(lat).toFixed(1));

    const dbLow = d.db_low == null ? null : Number(d.db_low) * S.scale.db;
    const dbHigh = d.db_high == null ? null : Number(d.db_high) * S.scale.db;
    setText(dbLowEl, dbLow == null ? "\u2014" : dbLow.toFixed(1));
    setText(dbHighEl, dbHigh == null ? "\u2014" : dbHigh.toFixed(1));

    const hl = pct(dbLow == null ? -90 : dbLow);
    const hh = pct(dbHigh == null ? -90 : dbHigh);

    const dt = Math.min(0.25, Math.max(0.001, (now - S.lastFrame) / 1000));
    if (S.peak.low == null) S.peak.low = hl;
    if (S.peak.high == null) S.peak.high = hh;
    S.peak.low = Math.max(hl, S.peak.low - 0.08 * dt);
    S.peak.high = Math.max(hh, S.peak.high - 0.08 * dt);

    barLow.style.transform = "scaleY(" + hl.toFixed(4) + ")";
    barHigh.style.transform = "scaleY(" + hh.toFixed(4) + ")";
    capLow.style.bottom = (S.peak.low * 100).toFixed(2) + "%";
    capHigh.style.bottom = (S.peak.high * 100).toFixed(2) + "%";

    setText(tempEl, fmtTemp(d.temp));
    setText(humEl, fmtHum(d.hum));

    if (window.Charts && ber != null) {
      window.Charts.setGauge(ber);
    }
  }

  function frame(now) {
    if (S.frozen) {
      S.lastFrame = now;
    } else if (S.dirty && S.latest) {
      render(now);
      S.dirty = false;
    }
    S.lastFrame = now;
    requestAnimationFrame(frame);
  }

  function pushHistory() {
    if (!S.latest || S.frozen || !window.Charts) return;
    const d = S.latest;
    const ber = d.ber == null ? null : Number(d.ber) * S.scale.ber;
    if (ber != null) window.Charts.pushBer(ber);
    window.Charts.pushEnv(
      d.temp == null ? null : Number(d.temp) * S.scale.temp,
      d.hum == null ? null : Number(d.hum) * S.scale.hum
    );
    window.Charts.pushBand(
      d.db_low == null ? null : Number(d.db_low) * S.scale.db,
      d.db_high == null ? null : Number(d.db_high) * S.scale.db
    );
  }

  // ------------------------------------------------------------------
  // 连接与状态机
  // ------------------------------------------------------------------
  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(proto + "://" + location.host + "/ws");
    S.ws = ws;

    ws.onopen = function () {
      S.wsOpen = true;
      S.retry = 1000;
      setState("CONNECTING");
    };

    ws.onmessage = function (ev) {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch (e) {
        return;
      }
      dispatch(msg);
    };

    ws.onclose = function () {
      S.wsOpen = false;
      S.hello = false;
      S.closedAt = performance.now();
      setState("CONNECTING");
      setTimeout(connect, S.retry);
      S.retry = Math.min(S.retry * 1.6, 5000);
    };

    ws.onerror = function () {
      try { ws.close(); } catch (e) { /* ignore */ }
    };
  }

  function tickState() {
    const now = performance.now();
    if (!S.wsOpen) {
      setState(now - S.closedAt > 5000 ? "LOST" : "CONNECTING");
      return;
    }
    if (!S.hello) {
      setState("CONNECTING");
      return;
    }
    const age = now - S.lastT;
    if (age > 5000) setState("LOST");
    else if (age > 2000) setState("STALE");
    else setState("LIVE");
  }

  // ------------------------------------------------------------------
  // 演示控制
  // ------------------------------------------------------------------
  function toggleFullscreen() {
    if (!document.fullscreenElement) {
      if (document.documentElement.requestFullscreen) {
        document.documentElement.requestFullscreen().catch(function () {});
      }
    } else if (document.exitFullscreen) {
      document.exitFullscreen().catch(function () {});
    }
  }

  async function control(key) {
    if (S.mode !== "SIM") {
      toast("仅模拟模式可注入");
      return;
    }
    try {
      const res = await fetch("/api/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: key })
      });
      if (res.ok) {
        toast("已发送控制键：" + key.toUpperCase());
      } else {
        let msg = "控制端口未开启";
        try {
          const data = await res.json();
          if (data && data.error) msg = data.error;
        } catch (e) { /* ignore */ }
        toast(msg);
      }
    } catch (e) {
      toast("桥接未响应");
    }
  }

  async function replay() {
    try {
      const res = await fetch("/api/replay", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: "records/sim60.log" })
      });
      if (res.ok) {
        toast("回放：records/sim60.log");
      } else {
        toast("回放接口未启用");
      }
    } catch (e) {
      toast("桥接未响应");
    }
  }

  function toggleDebug() {
    debugEl.classList.toggle("on");
    updateDebug();
  }

  function updateDebug() {
    if (!debugEl.classList.contains("on")) return;
    const s = S.status || {};
    const d = S.latest || {};
    const age = S.lastT ? Math.round(performance.now() - S.lastT) : -1;
    const lines = [
      "WS       " + (S.wsOpen ? "OPEN" : "CLOSED"),
      "STATE    " + S.state,
      "MODE     " + S.mode,
      "SESSION  " + S.session,
      "RX       " + (s.rx == null ? "--" : s.rx),
      "FPS      " + (s.fps == null ? "--" : s.fps),
      "T_US     " + (d.t_us == null ? "--" : d.t_us),
      "AGE_MS   " + (age < 0 ? "--" : age),
      "LAT_MS   " + (d.latency_ms == null ? "--" : Number(d.latency_ms).toFixed(2)),
      "LOCK     " + (d.lock == null ? "--" : d.lock),
      "DET      " + (d.det == null ? "--" : d.det),
      "SUPP     " + (d.supp == null ? "--" : d.supp),
      "LOST     " + (s.lost == null ? "--" : s.lost),
      "E_MISS   " + (s.e_missing == null ? "--" : s.e_missing),
      "INVARIANT " + (s.invariant_ok == null ? "--" : s.invariant_ok)
    ];
    setText(debugEl, lines.join("\n"));
  }

  window.addEventListener("keydown", function (ev) {
    if (ev.repeat || ev.ctrlKey || ev.metaKey || ev.altKey) return;
    const k = String(ev.key || "").toLowerCase();
    if (k === "f") { ev.preventDefault(); toggleFullscreen(); }
    else if (k === "d") { ev.preventDefault(); toggleDebug(); }
    else if (k === "l") { ev.preventDefault(); control("l"); }
    else if (k === "p") { ev.preventDefault(); control("p"); }
    else if (k === "r") { ev.preventDefault(); replay(); }
    else if (k === "escape") { debugEl.classList.remove("on"); }
  });

  window.addEventListener("resize", function () {
    if (window.Charts) window.Charts.resize();
  });

  document.querySelectorAll(".nav-item").forEach(function (item) {
    item.addEventListener("click", function (ev) {
      const href = item.getAttribute("href");
      if (!href || href === "#") {
        ev.preventDefault();
        toast("该模块下一步接入");
        return;
      }
      document.querySelectorAll(".nav-item").forEach(function (n) {
        n.classList.remove("active");
      });
      item.classList.add("active");
    });
  });

  initSplitters();
  if (window.Charts) window.Charts.init();
  setMode("SIM");
  setState("CONNECTING");
  connect();
  requestAnimationFrame(frame);
  setInterval(tickState, 250);
  setInterval(updateDebug, 1000);
  setInterval(pushHistory, 1000);

})();
