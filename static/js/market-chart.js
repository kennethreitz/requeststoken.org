(function () {
  const root = document.getElementById("market-chart");
  if (!root) return;

  const canvas = document.getElementById("market-chart-canvas");
  const message = document.getElementById("market-chart-message");
  const legend = document.getElementById("market-chart-legend");
  const note = document.getElementById("market-chart-note");
  const kicker = document.getElementById("market-chart-kicker");
  const link = document.getElementById("market-chart-link");
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const LABELS = { "1h": "1H candles", "4h": "4H candles", "1d": "1D candles" };
  const REFRESH_MS = 120000;

  let chart = null;
  let candleSeries = null;
  let volumeByTime = new Map();
  let latest = null;
  let timeframe = "1h";
  let drawn = false;
  let seq = 0;

  function cssVar(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  }

  function withAlpha(hex, alpha) {
    const value = String(hex || "").replace("#", "");
    if (!/^[0-9a-fA-F]{6}$/.test(value)) return hex;
    const red = parseInt(value.slice(0, 2), 16);
    const green = parseInt(value.slice(2, 4), 16);
    const blue = parseInt(value.slice(4, 6), 16);
    return "rgba(" + red + ", " + green + ", " + blue + ", " + alpha + ")";
  }

  function usd(value) {
    if (typeof value !== "number" || !isFinite(value)) return "—";
    const abs = Math.abs(value);
    let digits = 2;
    if (abs > 0 && abs < 1) digits = Math.min(10, Math.ceil(-Math.log10(abs)) + 3);
    let body = abs.toFixed(digits);
    if (body.indexOf(".") !== -1) body = body.replace(/0+$/, "").replace(/\.$/, "");
    return (value < 0 ? "−" : "") + "$" + body;
  }

  function usdVolume(value) {
    if (typeof value !== "number" || !isFinite(value)) return "—";
    const abs = Math.abs(value);
    const sign = value < 0 ? "−" : "";
    if (abs >= 1e9) return sign + "$" + (abs / 1e9).toFixed(2) + "B";
    if (abs >= 1e6) return sign + "$" + (abs / 1e6).toFixed(2) + "M";
    if (abs >= 1e3) return sign + "$" + (abs / 1e3).toFixed(1) + "k";
    if (abs >= 1) return sign + "$" + Math.round(abs);
    return sign + "$" + abs.toFixed(2);
  }

  function candleTime(unix, size) {
    if (size !== "1d") return unix;
    const date = new Date(unix * 1000);
    const month = String(date.getUTCMonth() + 1).padStart(2, "0");
    const day = String(date.getUTCDate()).padStart(2, "0");
    return date.getUTCFullYear() + "-" + month + "-" + day;
  }

  function formatWhen(time, size) {
    if (typeof time === "string") {
      const parts = time.split("-");
      if (parts.length === 3) {
        return Number(parts[2]) + " " + (MONTHS[Number(parts[1]) - 1] || parts[1]) + " " + parts[0];
      }
    }
    if (time && typeof time === "object" && time.year) {
      return time.day + " " + (MONTHS[time.month - 1] || "") + " " + time.year;
    }
    const date = new Date(Number(time) * 1000);
    if (isNaN(date.getTime())) return "";
    const day = date.getUTCDate() + " " + MONTHS[date.getUTCMonth()];
    if (size === "1d") return day + " " + date.getUTCFullYear();
    const hour = String(date.getUTCHours()).padStart(2, "0");
    const minute = String(date.getUTCMinutes()).padStart(2, "0");
    return day + " " + hour + ":" + minute + " UTC";
  }

  function legendText(bar, size) {
    if (!bar) return "";
    const volume = volumeByTime.get(String(bar.time));
    return formatWhen(bar.time, size)
      + "  O " + usd(bar.open)
      + "  H " + usd(bar.high)
      + "  L " + usd(bar.low)
      + "  C " + usd(bar.close)
      + (volume == null ? "" : "  Vol " + usdVolume(volume));
  }

  function showLegend(bar, size) {
    if (!legend) return;
    const text = legendText(bar, size);
    legend.textContent = text;
    legend.hidden = !text;
  }

  function setPressed(size) {
    root.querySelectorAll("[data-candle]").forEach(function (button) {
      const on = button.getAttribute("data-candle") === size;
      button.classList.toggle("is-on", on);
      button.setAttribute("aria-pressed", on ? "true" : "false");
    });
    if (kicker) kicker.textContent = LABELS[size] || "Price candles";
  }

  function finish(id) {
    if (id === seq) root.removeAttribute("aria-busy");
  }

  function destroyChart() {
    if (chart) chart.remove();
    chart = null;
    candleSeries = null;
  }

  function fail(detail, keep) {
    if (keep && drawn) return;
    drawn = false;
    latest = null;
    destroyChart();
    if (canvas) canvas.hidden = true;
    if (legend) legend.hidden = true;
    if (note) {
      note.hidden = true;
      note.textContent = "";
    }
    if (message) {
      message.hidden = false;
      message.classList.add("unavailable");
      message.textContent = detail || "Price history is temporarily unavailable.";
    }
  }

  function prepare(rows, size) {
    const sorted = rows.slice().sort(function (a, b) { return a.time - b.time; });
    const bars = [];
    let last = -Infinity;
    sorted.forEach(function (row) {
      const open = Number(row.open);
      const high = Number(row.high);
      const low = Number(row.low);
      const close = Number(row.close);
      const volume = Number(row.volume);
      if (![open, high, low, close, volume].every(isFinite)) return;
      if (!(open > 0 && high > 0 && low > 0 && close > 0) || volume < 0) return;
      const time = candleTime(Number(row.time), size);
      const key = typeof time === "number" ? time : Date.parse(time + "T00:00:00Z") / 1000;
      if (!(key > last)) return;
      last = key;
      bars.push({
        time: time,
        open: open,
        high: Math.max(high, open, close),
        low: Math.min(low, open, close),
        close: close,
        volume: volume,
      });
    });
    return bars;
  }

  function chartOptions(size) {
    const ink = cssVar("--muted", "#a9a290");
    const line = cssVar("--line", "rgba(241, 234, 216, 0.13)");
    const border = cssVar("--line-2", "rgba(241, 234, 216, 0.24)");
    return {
      autoSize: true,
      layout: {
        background: { type: "solid", color: cssVar("--raise", "#1b231e") },
        textColor: ink,
        fontFamily: cssVar("--mono", "IBM Plex Mono, ui-monospace, monospace"),
        fontSize: 11,
        attributionLogo: false,
        panes: {
          enableResize: false,
          separatorColor: line,
          separatorHoverColor: line,
        },
      },
      grid: {
        vertLines: { color: line },
        horzLines: { color: line },
      },
      rightPriceScale: { borderColor: border },
      timeScale: {
        borderColor: border,
        timeVisible: size !== "1d",
        secondsVisible: false,
        rightOffset: 2,
      },
      crosshair: { mode: window.LightweightCharts.CrosshairMode.Magnet },
      localization: { priceFormatter: usd },
      handleScroll: {
        mouseWheel: false,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: false,
      },
      handleScale: {
        mouseWheel: false,
        pinch: true,
        axisPressedMouseMove: true,
      },
    };
  }

  function paint(data) {
    if (!window.LightweightCharts || !canvas) {
      fail("Price history is temporarily unavailable.", false);
      return;
    }
    const bars = prepare(data.candles, data.timeframe);
    if (!bars.length) {
      fail(data.detail, false);
      return;
    }
    const Charts = window.LightweightCharts;
    const sage = cssVar("--sage", "#9cba96");
    const clay = cssVar("--clay", "#d89482");
    const gold = cssVar("--gold", "#e4c36a");
    const last = bars[bars.length - 1];
    const abs = Math.abs(last.close) || 1e-8;
    const precision = Math.min(12, Math.max(2, Math.ceil(-Math.log10(abs)) + 3));
    canvas.hidden = false;
    if (message) message.hidden = true;
    destroyChart();
    chart = Charts.createChart(canvas, chartOptions(data.timeframe));
    candleSeries = chart.addSeries(Charts.CandlestickSeries, {
      upColor: sage,
      downColor: clay,
      borderVisible: false,
      wickUpColor: sage,
      wickDownColor: clay,
      priceLineColor: gold,
      priceFormat: { type: "price", precision: precision, minMove: Math.pow(10, -precision) },
      autoscaleInfoProvider: function (original) {
        const info = original();
        if (!info || !info.priceRange || !(info.priceRange.minValue > 0)) return info;
        const min = info.priceRange.minValue;
        const max = info.priceRange.maxValue;
        info.priceRange.minValue = min * 0.92;
        info.priceRange.maxValue = max * 1.06;
        return info;
      },
    });
    const volumeSeries = chart.addSeries(Charts.HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "left",
      priceLineVisible: false,
      lastValueVisible: false,
    }, 1);
    candleSeries.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.02 } });
    const panes = chart.panes();
    if (panes.length > 1) {
      panes[0].setStretchFactor(3.2);
      panes[1].setStretchFactor(1);
      chart.priceScale("left", 1).applyOptions({
        visible: false,
        scaleMargins: { top: 0.16, bottom: 0 },
      });
    }
    volumeByTime = new Map();
    candleSeries.setData(bars.map(function (bar) {
      return { time: bar.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close };
    }));
    volumeSeries.setData(bars.map(function (bar) {
      volumeByTime.set(String(bar.time), bar.volume);
      return {
        time: bar.time,
        value: bar.volume,
        color: withAlpha(bar.close >= bar.open ? sage : clay, 0.45),
      };
    }));
    const visible = data.timeframe === "1h" ? 48 : data.timeframe === "4h" ? 60 : bars.length;
    if (bars.length > visible) {
      chart.timeScale().setVisibleLogicalRange({ from: bars.length - visible, to: bars.length + 1 });
    } else {
      chart.timeScale().fitContent();
    }
    chart.subscribeCrosshairMove(function (param) {
      const bar = param && param.seriesData && candleSeries ? param.seriesData.get(candleSeries) : null;
      showLegend(bar || latest, data.timeframe);
    });
    latest = bars[bars.length - 1];
    drawn = true;
    showLegend(latest, data.timeframe);
    if (link && typeof data.chart_url === "string" && data.chart_url.indexOf("https://www.geckoterminal.com/") === 0) {
      link.href = data.chart_url;
    }
    if (note) {
      if (data.stale && data.detail) {
        note.hidden = false;
        note.textContent = data.detail;
      } else {
        note.hidden = true;
        note.textContent = "";
      }
    }
  }

  function load(size, quiet) {
    const id = ++seq;
    timeframe = size;
    setPressed(size);
    root.setAttribute("aria-busy", "true");
    if (!quiet) {
      drawn = false;
      destroyChart();
      if (canvas) canvas.hidden = true;
      if (legend) legend.hidden = true;
      if (note) {
        note.hidden = true;
        note.textContent = "";
      }
      if (message) {
        message.hidden = false;
        message.classList.remove("unavailable");
        message.textContent = "Loading price history…";
      }
    }
    fetch("/api/candles?timeframe=" + encodeURIComponent(size), {
      headers: { Accept: "application/json" },
      cache: "no-store",
    }).then(function (response) {
      if (!response.ok) throw new Error(String(response.status));
      return response.json();
    }).then(function (data) {
      if (id !== seq) return;
      if (!data || !data.available || !data.candles || !data.candles.length) {
        fail(data && data.detail, quiet);
        return;
      }
      try {
        paint(data);
      } catch (error) {
        fail("Price history is temporarily unavailable.", false);
      }
    }).catch(function () {
      if (id !== seq) return;
      fail("Price history is temporarily unavailable.", quiet);
    }).then(function () {
      finish(id);
    });
  }

  root.querySelectorAll("[data-candle]").forEach(function (button) {
    button.addEventListener("click", function () {
      const next = button.getAttribute("data-candle");
      if (next) load(next, false);
    });
  });

  if (!window.LightweightCharts) {
    fail("Price history is temporarily unavailable.", false);
    return;
  }

  load("1h", false);

  window.setInterval(function () {
    if (document.visibilityState === "visible") load(timeframe, true);
  }, REFRESH_MS);

  const theme = window.matchMedia("(prefers-color-scheme: light)");
  if (theme.addEventListener) {
    theme.addEventListener("change", function () {
      if (drawn && latest) load(timeframe, true);
    });
  }
})();
