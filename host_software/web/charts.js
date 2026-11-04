(function () {
  "use strict";

  const MAX = 120;
  const charts = {};
  const buffers = { ber: [], temp: [], hum: [], bandLow: [], bandHigh: [] };
  let ready = false;

  function init() {
    if (ready) return true;
    if (!window.echarts) return false;

    const gaugeEl = document.getElementById("gauge");
    const berEl = document.getElementById("ber-spark");
    const tempEl = document.getElementById("temp-spark");
    const humEl = document.getElementById("hum-spark");
    const bandEl = document.getElementById("band-spark");
    if (!gaugeEl || !berEl || !tempEl || !humEl || !bandEl) return false;

    charts.gauge = echarts.init(gaugeEl, null, { renderer: "canvas" });
    charts.ber = echarts.init(berEl, null, { renderer: "canvas" });
    charts.temp = echarts.init(tempEl, null, { renderer: "canvas" });
    charts.hum = echarts.init(humEl, null, { renderer: "canvas" });
    charts.band = echarts.init(bandEl, null, { renderer: "canvas" });

    charts.gauge.setOption({
      animation: false,
      series: [{
        type: "gauge",
        min: 0,
        max: 1,
        startAngle: 210,
        endAngle: -30,
        center: ["50%", "60%"],
        radius: "88%",
        axisLine: {
          lineStyle: {
            width: 12,
            color: [[0.65, "#1e40af"], [0.78, "#f59e0b"], [1, "#ef4444"]]
          }
        },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: { show: false },
        pointer: {
          width: 4,
          length: "56%",
          itemStyle: { color: "#93c5fd" }
        },
        anchor: {
          show: true,
          size: 8,
          itemStyle: { color: "#3b82f6", borderWidth: 0 }
        },
        detail: {
          valueAnimation: false,
          offsetCenter: [0, "10%"],
          formatter: function (v) { return Number(v).toFixed(3); },
          fontSize: 19,
          fontWeight: 500,
          color: "#ffffff",
          fontFamily: "Barlow, Consolas, monospace"
        },
        title: { show: false },
        data: [{ value: 0 }]
      }]
    });

    const lineBase = {
      animation: false,
      grid: { left: 0, right: 0, top: 4, bottom: 0 },
      xAxis: { type: "category", show: false, boundaryGap: false, data: [] },
      yAxis: { type: "value", show: false },
      series: [{
        type: "line",
        showSymbol: false,
        smooth: true,
        silent: true,
        lineStyle: { width: 2.2 },
        areaStyle: { opacity: 1 },
        data: []
      }]
    };

    function sparkOption(color, min, max) {
      const opt = JSON.parse(JSON.stringify(lineBase));
      opt.yAxis.min = min;
      opt.yAxis.max = max;
      opt.series[0].lineStyle.color = color;
      opt.series[0].areaStyle.color = new echarts.graphic.LinearGradient(0, 0, 0, 1, [
        { offset: 0, color: hexA(color, 0.18) },
        { offset: 1, color: hexA(color, 0) }
      ]);
      return opt;
    }

    charts.ber.setOption(sparkOption("#3b82f6", 0, 1));
    charts.ber.setOption({
      series: [{
        markLine: {
          silent: true,
          symbol: "none",
          label: { show: false },
          data: [
            { yAxis: 0.65, lineStyle: { color: "rgba(245,158,11,.38)", type: "dashed", width: 1 } },
            { yAxis: 0.78, lineStyle: { color: "rgba(239,68,68,.42)", type: "dashed", width: 1 } }
          ]
        }
      }]
    }, false, false);
    charts.temp.setOption(sparkOption("#f59e0b", 15, 35));
    charts.hum.setOption(sparkOption("#67e8f9", 20, 80));
    charts.band.setOption({
      animation: false,
      grid: { left: 0, right: 0, top: 4, bottom: 0 },
      xAxis: { type: "category", show: false, boundaryGap: false, data: [] },
      yAxis: { type: "value", show: false, min: -90, max: -20 },
      series: [
        {
          type: "line", showSymbol: false, smooth: true, silent: true,
          lineStyle: { width: 1.8, color: "#3b82f6" },
          areaStyle: { opacity: .12, color: "rgba(30,64,175,.12)" },
          data: []
        },
        {
          type: "line", showSymbol: false, smooth: true, silent: true,
          lineStyle: { width: 1.8, color: "#67e8f9" },
          data: []
        }
      ]
    });

    ready = true;
    setTimeout(resize, 60);
    return true;
  }

  function hexA(hex, alpha) {
    const h = hex.replace("#", "");
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
  }

  function push(buf, chart, value) {
    if (!ready || !chart) return;
    buf.push(value);
    if (buf.length > MAX) buf.shift();
    const idx = buf.map(function (_, i) { return i; });
    chart.setOption({
      xAxis: { data: idx },
      series: [{ data: buf.slice() }]
    }, false, false);
  }

  function setGauge(value) {
    if (!ready || !charts.gauge) return;
    charts.gauge.setOption({
      series: [{ data: [{ value: Math.max(0, Math.min(1, value)) }] }]
    }, false, false);
  }

  function pushBer(value) {
    push(buffers.ber, charts.ber, value);
  }

  function pushEnv(temp, hum) {
    if (temp != null) push(buffers.temp, charts.temp, temp);
    if (hum != null) push(buffers.hum, charts.hum, hum);
  }

  function pushBand(low, high) {
    if (!ready || !charts.band) return;
    if (low != null) buffers.bandLow.push(low);
    if (high != null) buffers.bandHigh.push(high);
    if (buffers.bandLow.length > MAX) buffers.bandLow.shift();
    if (buffers.bandHigh.length > MAX) buffers.bandHigh.shift();

    const all = buffers.bandLow.concat(buffers.bandHigh);
    let yMin = -90;
    let yMax = -20;
    if (all.length) {
      let lo = Math.min.apply(null, all);
      let hi = Math.max.apply(null, all);
      if (hi - lo < 10) {
        const mid = (lo + hi) / 2;
        lo = mid - 5;
        hi = mid + 5;
      }
      yMin = Math.floor(lo - 2);
      yMax = Math.ceil(hi + 2);
    }

    const n = Math.max(buffers.bandLow.length, buffers.bandHigh.length);
    const idx = [];
    for (let i = 0; i < n; i++) idx.push(i);
    charts.band.setOption({
      yAxis: { min: yMin, max: yMax },
      xAxis: { data: idx },
      series: [
        { data: buffers.bandLow.slice() },
        { data: buffers.bandHigh.slice() }
      ]
    }, false, false);
  }

  function reset() {
    if (!ready) return;
    buffers.ber.length = 0;
    buffers.temp.length = 0;
    buffers.hum.length = 0;
    buffers.bandLow.length = 0;
    buffers.bandHigh.length = 0;
    [charts.ber, charts.temp, charts.hum].forEach(function (c) {
      c.setOption({ xAxis: { data: [] }, series: [{ data: [] }] }, false, false);
    });
    charts.band.setOption({
      yAxis: { min: -90, max: -20 },
      xAxis: { data: [] },
      series: [{ data: [] }, { data: [] }]
    }, false, false);
    setGauge(0);
  }

  function resize() {
    if (!ready) return;
    Object.keys(charts).forEach(function (k) {
      try { charts[k].resize(); } catch (e) { /* ignore */ }
    });
  }

  window.Charts = {
    init: init,
    setGauge: setGauge,
    pushBer: pushBer,
    pushEnv: pushEnv,
    pushBand: pushBand,
    reset: reset,
    resize: resize
  };
})();
