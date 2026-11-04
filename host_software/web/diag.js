/* diag.js -- 多维判决浮层（独立于 app.js，出问题只影响本浮层）
 *
 * 数据来源：relay 在 telemetry 消息里附带的 "diag" 字段（来自 10 Hz 的 X 帧）。
 * 展示内容：判决级别、卡方分数、动态基线 mu±sigma、噪声底、峭度、命中的判据。
 */
(function () {
  "use strict";

  var LEVEL_TEXT = ["L0 正常", "L1 关注", "L2 告警", "L3 严重"];
  var LEVEL_COLOR = ["#3fbf6f", "#e0b400", "#e07a00", "#d93838"];

  var box = document.createElement("div");
  box.id = "diag-overlay";
  box.style.cssText = [
    "position:fixed", "right:14px", "bottom:14px", "z-index:60",
    "min-width:250px", "padding:10px 12px",
    "background:rgba(16,20,28,.86)", "color:#dfe6f0",
    "border:1px solid rgba(120,150,190,.35)", "border-radius:10px",
    "font:12px/1.55 Consolas,Monaco,monospace",
    "box-shadow:0 6px 24px rgba(0,0,0,.35)",
    "backdrop-filter:blur(6px)", "cursor:move", "user-select:none"
  ].join(";");
  box.innerHTML =
    '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">' +
    '<span style="font-weight:700;letter-spacing:.5px">多维判决</span>' +
    '<span id="dg-level" style="margin-left:auto;font-weight:700">--</span>' +
    "</div>" +
    '<div id="dg-rows"></div>';

  function row(label, value, color) {
    return '<div style="display:flex;gap:8px">' +
      '<span style="color:#8fa3bd">' + label + "</span>" +
      '<span style="margin-left:auto;color:' + (color || "#dfe6f0") + '">' + value + "</span>" +
      "</div>";
  }

  var rowsEl, levelEl;

  function mount() {
    if (!document.body) return;
    document.body.appendChild(box);
    rowsEl = box.querySelector("#dg-rows");
    levelEl = box.querySelector("#dg-level");
    makeDraggable();
  }

  function makeDraggable() {
    var sx = 0, sy = 0, ox = 0, oy = 0, dragging = false;
    box.addEventListener("mousedown", function (e) {
      dragging = true;
      sx = e.clientX;
      sy = e.clientY;
      var r = box.getBoundingClientRect();
      ox = r.left;
      oy = r.top;
      box.style.right = "auto";
      box.style.bottom = "auto";
      box.style.left = ox + "px";
      box.style.top = oy + "px";
      e.preventDefault();
    });
    window.addEventListener("mousemove", function (e) {
      if (!dragging) return;
      box.style.left = (ox + e.clientX - sx) + "px";
      box.style.top = (oy + e.clientY - sy) + "px";
    });
    window.addEventListener("mouseup", function () { dragging = false; });
  }

  function render(d) {
    if (!rowsEl) return;
    var lv = Math.max(0, Math.min(3, d.level | 0));
    levelEl.textContent = LEVEL_TEXT[lv];
    levelEl.style.color = LEVEL_COLOR[lv];

    var score = d.score | 0;
    var thr = 1381;                       // chi2(2,0.001) 的显示单位
    var hit = score > thr;
    var bits = [];
    if (d.flags & 0x10) bits.push("能量突增");
    if (hit) bits.push("谱形偏离");
    if (d.flags & 0x01) bits.push("过热关联");
    if (d.flags & 0x08) bits.push("学习期");

    var html = "";
    html += row("判决分", score + (hit ? "  >" + thr : "  /" + thr),
      hit ? "#d93838" : "#dfe6f0");
    html += row("基线 BER",
      (d.mu | 0) / 1000 + " ± " + ((d.sd | 0) / 1000).toFixed(3));
    html += row("当前 BER", (d.ber | 0) / 1000,
      (d.ber | 0) > (d.mu | 0) ? "#e07a00" : "#dfe6f0");
    html += row("残差 BER", (d.ber_res | 0) / 1000);
    html += row("噪声底", (d.nf_low | 0) + " / " + (d.nf_high | 0));
    html += row("峭度档", d.kurt | 0);
    html += row("判据", bits.length ? bits.join(" + ") : "无",
      bits.length ? "#e0b400" : "#8fa3bd");
    rowsEl.innerHTML = html;
  }

  var ws = null;
  var proto = location.protocol === "https:" ? "wss" : "ws";
  var url = proto + "://" + location.host + "/ws";

  function connect() {
    try {
      ws = new WebSocket(url);
    } catch (e) {
      setTimeout(connect, 2000);
      return;
    }
    ws.onmessage = function (ev) {
      var msg;
      try {
        msg = JSON.parse(ev.data);
      } catch (e) {
        return;
      }
      if (msg && msg.type === "telemetry" && msg.diag) {
        render(msg.diag);
      }
    };
    ws.onclose = function () {
      setTimeout(connect, 2000);
    };
    ws.onerror = function () {
      try { ws.close(); } catch (e) {}
    };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      mount();
      connect();
    });
  } else {
    mount();
    connect();
  }
})();
