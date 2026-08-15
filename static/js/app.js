/* ==========================================================
   SFM-MA — 共享前端工具
   页面脚本统一走这里的 api / chart 助手，避免各页重复实现，
   也避免把实验数据内联硬编码进 HTML（唯一来源是 /api/*）。
   ========================================================== */
(function (global) {
  'use strict';

  // ── 设计令牌（与 style.css 的调色板保持一致） ──────────────
  var PALETTE = [
    '#7C3AED', '#F59E0B', '#10B981', '#EF4444', '#3B82F6',
    '#EC4899', '#14B8A6', '#F97316', '#8B5CF6', '#06B6D4',
    '#84CC16', '#A855F7', '#0EA5E9', '#F43F5E', '#22C55E',
    '#EAB308', '#6366F1', '#D946EF', '#059669', '#DC2626'
  ];
  var C = {
    primary: '#7C3AED', primaryLight: '#C4B5FD', primarySoft: '#EDE9FE',
    gray: '#9CA3AF', grayLine: '#E5E7EB', text: '#374151', textSoft: '#6B7280',
    pos: '#10B981', neg: '#EF4444', warn: '#F59E0B'
  };

  var FONT = "Inter, -apple-system, 'Microsoft YaHei', sans-serif";

  // ── 数据请求 ────────────────────────────────────────────────
  //
  // 同一套页面要在两种环境下跑：
  //   ① Flask 服务   —— 直接请求 /api/*
  //   ② 纯静态托管   —— 没有后端，导出脚本会注入 window.SFM_STATIC，
  //                     此时把 /api/* 映射到预生成的 JSON 文件，
  //                     任务记录退化为浏览器 localStorage。
  // 页面代码不需要区分，照常调 SFM.api() / SFM.post() 即可。
  var STATIC = global.SFM_STATIC || null;
  var _memo = {};

  function parseQuery(path) {
    var i = path.indexOf('?'), q = {};
    if (i < 0) return { base: path, q: q };
    path.slice(i + 1).split('&').forEach(function (kv) {
      if (!kv) return;
      var p = kv.split('=');
      q[decodeURIComponent(p[0])] = decodeURIComponent(p[1] || '');
    });
    return { base: path.slice(0, i), q: q };
  }

  var STATIC_MAP = {
    '/api/summary': 'data/summary.json',
    '/api/datasets': 'data/index.json',
    '/api/baselines': 'data/baselines.json',
    '/api/transfer': 'data/transfer.json',
    '/api/paradigm': 'data/paradigm.json',
    '/api/downloads': 'data/downloads.json',
    '/api/metrics': 'data/metrics.json'
  };

  /** 静态模式下把 /api/... 解析成一个 {url, filter} 描述 */
  function staticTarget(path) {
    var pq = parseQuery(path), base = pq.base, q = pq.q;

    if (base.indexOf('/api/visualize/') === 0) {
      var ds = base.slice('/api/visualize/'.length);
      var sl = q.section || q.idx || '_single';
      return { url: STATIC.base + 'viz_data/' + ds + '/' + sl + '/viz.json' };
    }
    if (base === '/api/metrics') {
      return {
        url: STATIC.base + STATIC_MAP[base],
        filter: function (rows) {
          return rows.filter(function (r) {
            if (q.dataset && q.dataset !== 'all' && r.dataset !== q.dataset) return false;
            if (q.section && r.slice !== q.section) return false;
            return true;
          });
        }
      };
    }
    if (STATIC_MAP[base]) return { url: STATIC.base + STATIC_MAP[base] };
    return null;
  }

  function api(path, opts) {
    var noCache = opts && opts.noCache;
    if (!noCache && _memo[path]) return _memo[path];

    var p;
    if (STATIC && path.indexOf('/api/tasks') === 0) {
      p = Promise.resolve(loadLocalTasks());
    } else {
      var t = STATIC ? staticTarget(path) : null;
      if (STATIC && !t) {
        p = Promise.reject(new Error('静态模式下不支持的接口: ' + path));
      } else {
        p = fetch(t ? t.url : path).then(function (r) {
          if (!r.ok) {
            return r.json().catch(function () { return {}; }).then(function (b) {
              throw new Error(b.error || ('HTTP ' + r.status));
            });
          }
          return r.json();
        }).then(function (body) {
          return (t && t.filter) ? t.filter(body) : body;
        });
      }
    }
    if (!noCache) _memo[path] = p;
    return p.catch(function (e) { delete _memo[path]; throw e; });
  }

  // 静态模式的任务记录：存在浏览器本地，刷新仍在，但不跨设备共享
  var TASK_KEY = 'sfm_ma_tasks';

  function loadLocalTasks() {
    try { return JSON.parse(global.localStorage.getItem(TASK_KEY) || '[]'); }
    catch (e) { return []; }
  }

  function saveLocalTask(entry) {
    var list = loadLocalTasks().filter(function (t) {
      return !(t.dataset === entry.dataset && t.slice === entry.slice && t.method === entry.method);
    });
    entry.time = entry.time || new Date().toISOString();
    entry.status = entry.status || 'completed';
    list.push(entry);
    list = list.slice(-100);
    try { global.localStorage.setItem(TASK_KEY, JSON.stringify(list)); } catch (e) { /* 隐私模式忽略 */ }
    delete _memo['/api/tasks'];
    return { status: 'ok', count: list.length };
  }

  function post(path, body) {
    if (STATIC) {
      return Promise.resolve(path.indexOf('/api/tasks') === 0
        ? saveLocalTask(body)
        : { status: 'skipped' });
    }
    return fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); });
  }

  // ── 格式化 ──────────────────────────────────────────────────
  function fmt(v, d) {
    if (v === null || v === undefined || v === '' || Number.isNaN(v)) return '—';
    return Number(v).toFixed(d === undefined ? 4 : d);
  }

  function fmtDelta(v, d) {
    if (v === null || v === undefined) return '—';
    var s = Number(v).toFixed(d === undefined ? 4 : d);
    return Number(v) > 0 ? '+' + s : s;
  }

  function fmtP(p) {
    if (p === null || p === undefined) return '—';
    return p < 1e-6 ? '<1e-6' : Number(p).toFixed(p < 0.001 ? 5 : 4);
  }

  function fmtInt(v) {
    if (v === null || v === undefined) return '—';
    return Number(v).toLocaleString('en-US');
  }

  /** 迁移分类 → 语义色，PT 绿 / ET 橙 / UI 红 */
  function catClass(cat) {
    if (!cat) return 'cat-na';
    if (cat.indexOf('PT') === 0) return 'cat-pt';
    if (cat.indexOf('ET') === 0) return 'cat-et';
    if (cat.indexOf('UI') === 0) return 'cat-ui';
    return 'cat-na';
  }

  function deltaClass(v) {
    if (v === null || v === undefined) return '';
    if (v > 0.0388) return 'delta-pos';
    if (v < -0.0388) return 'delta-neg';
    return 'delta-eq';
  }

  // ── ECharts 助手 ────────────────────────────────────────────
  var _charts = {};
  var _observed = {};

  /**
   * 按容器 id 初始化并登记图表，重复调用会先释放旧实例。
   *
   * ECharts 在 init 时锁定容器尺寸。图表往往在 fetch 回调里创建，此时栅格布局
   * 可能尚未定型（实测拿到过 28px 宽的画布），因此挂一个 ResizeObserver：
   * 容器尺寸一变就 resize，既修正首次的错误宽度，也覆盖窗口缩放与响应式断点。
   */
  function chart(id, option) {
    var el = document.getElementById(id);
    if (!el || !global.echarts) return null;
    if (_charts[id]) _charts[id].dispose();
    var c = global.echarts.init(el);
    c.setOption(option);
    _charts[id] = c;

    if (!_observed[id] && global.ResizeObserver) {
      var ro = new ResizeObserver(function () {
        if (_charts[id]) _charts[id].resize();
      });
      ro.observe(el);
      _observed[id] = ro;
    }

    // 兜底：ResizeObserver 的回调挂在渲染管线上，容器在隐藏区域（未展开的
    // 标签页、折叠面板、后台标签）里创建时不会触发。这里再补几次延时 resize，
    // 把首帧量到的错误宽度纠正回来。
    [0, 150, 500].forEach(function (ms) {
      setTimeout(function () {
        var cur = _charts[id];
        if (cur && el.clientWidth && cur.getWidth() !== el.clientWidth) cur.resize();
      }, ms);
    });
    return c;
  }

  global.addEventListener('resize', function () {
    Object.keys(_charts).forEach(function (k) {
      if (_charts[k]) _charts[k].resize();
    });
  });

  var baseGrid = { left: 52, right: 20, top: 28, bottom: 34, containLabel: true };

  function axisStyle(name) {
    return {
      name: name, nameTextStyle: { color: C.textSoft, fontSize: 11 },
      axisLine: { lineStyle: { color: C.grayLine } },
      axisTick: { show: false },
      axisLabel: { color: C.textSoft, fontSize: 11 },
      splitLine: { lineStyle: { color: C.grayLine, type: 'dashed' } }
    };
  }

  function tooltipStyle(extra) {
    return Object.assign({
      backgroundColor: 'rgba(255,255,255,0.97)',
      borderColor: C.grayLine, borderWidth: 1,
      padding: [8, 12],
      textStyle: { color: C.text, fontSize: 12, fontFamily: FONT },
      extraCssText: 'box-shadow:0 4px 16px rgba(0,0,0,.10);border-radius:8px;'
    }, extra || {});
  }

  /**
   * 分类散点图（空间图 / 嵌入图共用）。
   * cats 为每个点的类别字符串，颜色按类别在 legend 顺序取自 PALETTE。
   */
  function scatterByCategory(id, title, xs, ys, cats, opts) {
    opts = opts || {};
    var uniq = [];
    for (var i = 0; i < cats.length; i++) {
      if (uniq.indexOf(cats[i]) === -1) uniq.push(cats[i]);
    }
    uniq.sort(function (a, b) {
      var na = parseFloat(a), nb = parseFloat(b);
      if (!isNaN(na) && !isNaN(nb)) return na - nb;
      return String(a).localeCompare(String(b));
    });

    var series = uniq.map(function (u, k) {
      var pts = [];
      for (var i = 0; i < cats.length; i++) {
        if (cats[i] === u) pts.push([xs[i], ys[i]]);
      }
      return {
        name: String(u), type: 'scatter', data: pts,
        symbolSize: opts.symbolSize || 4,
        large: pts.length > 800, largeThreshold: 800,
        itemStyle: { color: PALETTE[k % PALETTE.length], opacity: 0.85 }
      };
    });

    return chart(id, {
      title: title ? {
        text: title, left: 'center', top: 2,
        textStyle: { fontSize: 13, fontWeight: 600, color: C.text, fontFamily: FONT }
      } : undefined,
      tooltip: tooltipStyle({
        formatter: function (p) {
          return '域 <b>' + p.seriesName + '</b><br/>(' +
            Number(p.value[0]).toFixed(1) + ', ' + Number(p.value[1]).toFixed(1) + ')';
        }
      }),
      legend: {
        type: 'scroll', bottom: 0, itemWidth: 9, itemHeight: 9,
        textStyle: { fontSize: 10, color: C.textSoft }, data: uniq.map(String)
      },
      grid: { left: 8, right: 8, top: title ? 28 : 8, bottom: 30 },
      xAxis: { show: false, scale: true },
      yAxis: { show: false, scale: true, inverse: !!opts.invertY },
      series: series,
      animation: false
    });
  }

  /** 横向条形图，突出首项（通常是 SFM-MA） */
  function barCompare(id, names, values, opts) {
    opts = opts || {};
    var hi = opts.highlight || names[0];
    return chart(id, {
      tooltip: tooltipStyle({ trigger: 'axis', axisPointer: { type: 'shadow' } }),
      grid: Object.assign({}, baseGrid, opts.grid || {}),
      xAxis: Object.assign({ type: 'value', max: opts.max }, axisStyle(opts.xName)),
      yAxis: Object.assign({ type: 'category', data: names, inverse: true },
        axisStyle(), { splitLine: { show: false } }),
      series: [{
        type: 'bar', data: values.map(function (v, i) {
          return {
            value: v,
            itemStyle: {
              color: names[i] === hi ? C.primary : C.primaryLight,
              borderRadius: [0, 4, 4, 0]
            }
          };
        }),
        barWidth: opts.barWidth || '58%',
        label: {
          show: true, position: 'right', fontSize: 11, color: C.textSoft,
          formatter: function (p) { return fmt(p.value, 3); }
        }
      }]
    });
  }

  return (global.SFM = {
    PALETTE: PALETTE, C: C, FONT: FONT,
    isStatic: !!STATIC,
    api: api, post: post,
    fmt: fmt, fmtDelta: fmtDelta, fmtP: fmtP, fmtInt: fmtInt,
    catClass: catClass, deltaClass: deltaClass,
    chart: chart, charts: _charts,
    axisStyle: axisStyle, tooltipStyle: tooltipStyle, baseGrid: baseGrid,
    scatterByCategory: scatterByCategory, barCompare: barCompare
  });
})(window);
