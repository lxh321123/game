/* ==========================================================
   CellSpace-Bridge — 小胞（Cellbuddy）结果解读助手
   ----------------------------------------------------------
   面向初次使用者的规则驱动解读助手：
   · 页面在渲染完结果后调用 SFM.assistant.predict() / .scrna()
   · 助手按指标阈值与排名生成解读卡（不联网、不调用大模型，
     说什么完全可控，比赛现场零风险）
   · 内置常见问题知识库（指标含义 / 图怎么读 / 结果口径）
   · 样式全部取自 style.css 的主题变量，自动适配明暗主题
   ========================================================== */
(function (global) {
  'use strict';

  if (!global.document) return;

  // ── 吉祥物：卡通细胞「小胞」 ─────────────────────────────
  // 圆形细胞 + 细胞核当脸 + 几颗细胞器，全部内联 SVG，无外部图片依赖
  var MASCOT =
    '<svg viewBox="0 0 64 64" aria-hidden="true">' +
    '<defs><radialGradient id="cb-body" cx="38%" cy="32%" r="75%">' +
    '<stop offset="0%" stop-color="#7fd8ff"/><stop offset="55%" stop-color="#42b8f4"/>' +
    '<stop offset="100%" stop-color="#1f7fc4"/></radialGradient>' +
    '<radialGradient id="cb-nucleus" cx="42%" cy="36%" r="70%">' +
    '<stop offset="0%" stop-color="#fff7e8"/><stop offset="100%" stop-color="#ffd889"/></radialGradient></defs>' +
    // 细胞膜
    '<circle cx="32" cy="32" r="28" fill="url(#cb-body)"/>' +
    '<circle cx="32" cy="32" r="28" fill="none" stroke="rgba(255,255,255,0.55)" stroke-width="1.6"/>' +
    // 细胞器
    '<circle cx="17" cy="24" r="3.4" fill="rgba(255,255,255,0.35)"/>' +
    '<circle cx="47" cy="20" r="2.4" fill="rgba(255,255,255,0.3)"/>' +
    '<circle cx="45" cy="45" r="3.8" fill="rgba(92,224,191,0.5)"/>' +
    '<circle cx="15" cy="42" r="2.2" fill="rgba(255,255,255,0.28)"/>' +
    // 细胞核（脸）
    '<circle cx="32" cy="34" r="13.5" fill="url(#cb-nucleus)"/>' +
    // 眼睛 + 微笑
    '<circle cx="27" cy="31.5" r="1.9" fill="#20344a"/>' +
    '<circle cx="37" cy="31.5" r="1.9" fill="#20344a"/>' +
    '<path d="M27.5 38 Q32 41.5 36.5 38" fill="none" stroke="#20344a" stroke-width="1.7" stroke-linecap="round"/>' +
    // 高光
    '<ellipse cx="23" cy="15" rx="6" ry="3.4" fill="rgba(255,255,255,0.4)" transform="rotate(-24 23 15)"/>' +
    '</svg>';

  // ── 样式（注入一次） ─────────────────────────────────────
  var CSS =
    '.cb-launcher{position:fixed;right:22px;bottom:22px;z-index:1200;width:58px;height:58px;' +
    'border-radius:50%;border:1px solid var(--border);cursor:pointer;padding:0;background:var(--color-surface,var(--gray-900));' +
    'box-shadow:var(--shadow-deep);display:flex;align-items:center;justify-content:center;transition:box-shadow .2s;}' +
    '.cb-launcher:hover{box-shadow:var(--shadow-deep),0 0 24px rgba(66,184,244,0.35);}' +
    '.cb-launcher svg{width:46px;height:46px;animation:cb-float 3.4s ease-in-out infinite;}' +
    '@keyframes cb-float{0%,100%{transform:translateY(0)}50%{transform:translateY(-3px)}}' +
    '.cb-badge{position:absolute;top:-2px;right:-2px;width:14px;height:14px;border-radius:50%;' +
    'background:var(--error);border:2px solid var(--color-surface,var(--gray-900));display:none;}' +
    '.cb-badge.on{display:block;animation:cb-pulse 1.6s ease-in-out infinite;}' +
    '@keyframes cb-pulse{0%,100%{transform:scale(1)}50%{transform:scale(1.25)}}' +
    '.cb-hello{position:fixed;right:90px;bottom:34px;z-index:1200;max-width:230px;padding:10px 14px;' +
    'background:var(--color-surface,var(--gray-900));color:var(--gray-700);border:1px solid var(--border);' +
    'border-radius:12px 12px 3px 12px;font-size:13px;line-height:1.55;box-shadow:var(--shadow-card);display:none;}' +
    '.cb-hello.on{display:block;}' +
    '.cb-panel{position:fixed;right:22px;bottom:90px;z-index:1210;width:min(370px,calc(100vw - 32px));' +
    'max-height:min(560px,72vh);display:none;flex-direction:column;overflow:hidden;' +
    'background:var(--color-surface,var(--gray-900));border:1px solid var(--border);border-radius:16px;' +
    'box-shadow:var(--shadow-deep);}' +
    '.cb-panel.on{display:flex;}' +
    '.cb-head{display:flex;align-items:center;gap:10px;padding:12px 14px;border-bottom:1px solid var(--border);flex-shrink:0;}' +
    '.cb-head .cb-avatar{width:38px;height:38px;flex-shrink:0;}' +
    '.cb-head .cb-avatar svg{width:38px;height:38px;}' +
    '.cb-head b{display:block;font-size:14px;color:var(--gray-800);}' +
    '.cb-head small{display:block;font-size:11px;color:var(--gray-500);margin-top:1px;}' +
    '.cb-close{margin-left:auto;background:none;border:none;color:var(--gray-500);font-size:18px;cursor:pointer;' +
    'padding:4px 8px;border-radius:6px;line-height:1;}' +
    '.cb-close:hover{color:var(--gray-800);background:var(--primary-soft,rgba(66,184,244,0.12));}' +
    '.cb-body{overflow-y:auto;padding:12px 14px;display:flex;flex-direction:column;gap:10px;flex:1;}' +
    '.cb-say{font-size:13px;line-height:1.65;color:var(--gray-700);}' +
    '.cb-say b{color:var(--gray-800);}' +
    '.cb-card{border:1px solid var(--border);border-left:3px solid var(--primary-500);border-radius:10px;' +
    'padding:10px 12px;background:var(--primary-soft,rgba(66,184,244,0.07));}' +
    '.cb-card h5{margin:0 0 4px;font-size:13px;color:var(--gray-800);}' +
    '.cb-card p{margin:0;font-size:12px;line-height:1.65;color:var(--gray-600);}' +
    '.cb-card.tone-good{border-left-color:var(--success);}' +
    '.cb-card.tone-mid{border-left-color:var(--warning);}' +
    '.cb-card.tone-warn{border-left-color:var(--error);}' +
    '.cb-chips{display:flex;flex-wrap:wrap;gap:6px;padding:10px 14px;border-top:1px solid var(--border);flex-shrink:0;}' +
    '.cb-chip{padding:4px 10px;border-radius:999px;border:1px solid var(--border);background:transparent;' +
    'color:var(--gray-600);font-size:12px;cursor:pointer;font-family:inherit;transition:border-color .15s,color .15s;}' +
    '.cb-chip:hover{border-color:var(--primary-400);color:var(--primary-500);}' +
    '.cb-back{font-size:12px;color:var(--primary-500);background:none;border:none;cursor:pointer;padding:0 0 2px;font-family:inherit;}' +
    '@media (max-width:768px){.cb-launcher{right:14px;bottom:14px}.cb-panel{right:14px;bottom:78px}}';

  // ── 常见问题知识库 ───────────────────────────────────────
  var QA = [
    { q: 'ARI 是什么？', a: '<b>ARI（调整兰德指数）</b>衡量预测结果与真实标注的一致程度，取值范围 [-1, 1]，越大越好。它考虑了随机巧合的校正，是聚类评估的黄金标准：经验上 ARI ≥ 0.75 属于优秀，0.55 ~ 0.75 良好，0.35 ~ 0.55 中等。' },
    { q: 'NMI 是什么？', a: '<b>NMI（归一化互信息）</b>衡量预测结果与真实标注之间共享的信息量，取值 [0, 1]，越大越好。它对簇的编号顺序不敏感，常与 ARI 搭配报告——两者都高，说明结果稳健。' },
    { q: 'SC / DBI 是什么？', a: '<b>SC（轮廓系数）</b>衡量嵌入空间中簇的"紧致又分离"程度，越大越好；<b>DBI（Davies-Bouldin 指数）</b>衡量簇内相似度与簇间差异之比，<b>越小越好</b>。这两个指标不需要真实标注，用于无标注数据集的质量评估。' },
    { q: '为什么没有 ARI？', a: '因为该数据集<b>没有真实标注</b>（专家注释的空间域标签）。没有"标准答案"就算不出 ARI / NMI，所以系统改用 SC 和 DBI 这两个无监督指标来衡量聚类质量——这也是论文对无标注数据集的处理方式。' },
    { q: '空间域图怎么看？', a: '每个点是一个 <b>spot</b>（空间转录组的捕获点位），位置对应它在组织切片上的物理坐标，颜色代表它被分到哪个<b>空间域</b>。左图是模型预测，右图是真实解剖标注：颜色块越连贯、与右图越接近，说明识别越准。点图例可以隐藏某个域单独查看。' },
    { q: '嵌入图怎么看？', a: '模型为每个 spot 学到一个高维表示，嵌入图把它投影到二维（PCA）。<b>同一个空间域的点聚成一簇</b>，说明表示学习已把空间结构编码进嵌入；簇与簇分得越开，表示的区分度越好。PC1 / PC2 的解释方差告诉你这个二维视图保留了多少信息。' },
    { q: '结果是怎么算出来的？', a: '页面展示的是<b>预计算的完整推理结果</b>：冻结预训练基础模型，仅用 <b>5% 标注</b>训练轻量适配器（CE 损失）后推理得到，与论文主实验口径完全一致。因此打开即查，无需等待训练。' },
    { q: '怎么导出结果？', a: '在预测页结果底部点击<b>「导出结果 JSON」</b>，会下载包含数据集、切片、方法、各项指标的 JSON 文件，可直接用于报告或二次分析。' }
  ];

  // ── 状态与 DOM ───────────────────────────────────────────
  var opened = false;          // 面板是否展开
  var unread = false;          // 是否有未读解读
  var helloShown = false;      // 首次问候气泡
  var messages = [];           // 当前解读内容
  var mode = 'say';            // 'say' 展示解读 | 'qa' 展示某条问答
  var qaActive = -1;

  var launcher, badge, hello, panel, body, chips;

  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html !== undefined) e.innerHTML = html;
    return e;
  }

  function toneOf(t) { return ' tone-' + (t || 'info'); }

  function renderBody() {
    if (!body) return;
    body.innerHTML = '';
    if (mode === 'qa' && qaActive >= 0) {
      var back = el('button', 'cb-back', '← 返回解读');
      back.onclick = function () { mode = 'say'; qaActive = -1; renderBody(); };
      body.appendChild(back);
      body.appendChild(el('div', 'cb-say', QA[qaActive].a));
      return;
    }
    messages.forEach(function (m) {
      if (m.say) body.appendChild(el('div', 'cb-say', m.say));
      else body.appendChild(el('div', 'cb-card' + toneOf(m.tone),
        '<h5>' + m.title + '</h5><p>' + m.text + '</p>'));
    });
    body.scrollTop = 0;
  }

  function markUnread(on) {
    unread = !!on;
    if (badge) badge.className = 'cb-badge' + (on && !opened ? ' on' : '');
  }

  function openPanel() {
    opened = true;
    mode = 'say'; qaActive = -1;
    renderBody();
    panel.classList.add('on');
    markUnread(false);
    if (hello) hello.classList.remove('on');
  }

  function closePanel() {
    opened = false;
    panel.classList.remove('on');
  }

  /** 页面报告新的解读内容；messages 为 [{say}| {title,text,tone}] 数组 */
  function report(msgs) {
    messages = msgs || [];
    mode = 'say'; qaActive = -1;
    if (opened) renderBody();
    else markUnread(true);
  }

  // ── 指标解读规则 ─────────────────────────────────────────
  function ariCard(v) {
    if (v == null) return null;
    var t = parseFloat(v), title, text, tone;
    if (t >= 0.75) { tone = 'good'; title = 'ARI ' + t.toFixed(3) + ' —— 一致性很高'; text = '预测的空间域与真实解剖结构高度一致（≥0.75 属优秀档），主要组织结构已被清晰还原。'; }
    else if (t >= 0.55) { tone = 'good'; title = 'ARI ' + t.toFixed(3) + ' —— 一致性较高'; text = '预测结果与真实标注吻合较好（0.55~0.75 属良好档），主体结构正确，局部边界略有出入。'; }
    else if (t >= 0.35) { tone = 'mid'; title = 'ARI ' + t.toFixed(3) + ' —— 中等水平'; text = '模型捕捉到了主要结构，但部分相邻区域被混淆。可以结合右侧真实标注图对比差异。'; }
    else { tone = 'warn'; title = 'ARI ' + t.toFixed(3) + ' —— 偏低'; text = '预测与真实标注的一致性有限，建议切换其他切片或方法对比观察。'; }
    return { title: title, text: text, tone: tone };
  }

  function rankCard(rank, total, gap, metric) {
    if (rank == null || total == null || total < 2) return null;
    if (rank === 1) {
      return { title: '在 ' + total + ' 个方法中排名第 1', tone: 'good',
        text: '该切片上' + (gap != null && gap > 0 ? '领先第 2 名 ' + gap.toFixed(3) + '。' : '为最优结果。') +
          '可切换上方方法卡片查看其他方法在同一份数据上的表现。' };
    }
    return { title: '在 ' + total + ' 个方法中排名第 ' + rank, tone: 'info',
      text: '当前所选方法在此切片上的 ' + metric + ' 对比排名。切换到 SFM-MA 卡片可查看本框架的实测结果。' };
  }

  function unlabCard(m) {
    if (m.ari != null) return null;
    var t = '无标注数据集，看 SC 和 DBI';
    var x = '该数据集没有真实标注，所以不计算 ARI / NMI；改用 <b>SC</b>（轮廓系数，越大越好）' +
      (m.sc != null ? '，当前为 <b>' + Number(m.sc).toFixed(3) + '</b>' : '') +
      ' 和 <b>DBI</b>（越小越好' + (m.db != null ? '，当前为 <b>' + Number(m.db).toFixed(3) + '</b>' : '') +
      '）来衡量聚类质量。上方的「方法对比」条形图用的就是 SC。';
    return { title: t, text: x, tone: 'info' };
  }

  // ── 对外接口 ─────────────────────────────────────────────
  /** 预测页：渲染完结果后调用 */
  function predict(p) {
    var msgs = [];
    var name = p.method + ' 在 ' + p.dataset + (p.section ? ' / ' + p.section : '');
    msgs.push({ say: '这份 <b>' + name + '</b> 的结果，我来帮你解读 👇' });
    var c;
    if ((c = unlabCard(p.metrics))) msgs.push(c);
    if ((c = ariCard(p.metrics && p.metrics.ari))) msgs.push(c);
    if ((c = rankCard(p.rank, p.total, p.gap, p.metric || 'ARI'))) msgs.push(c);
    if (p.nSpots && p.nDomains) {
      msgs.push({
        title: '空间域划分：' + p.nDomains + ' 个域' + (p.nTrue ? '（真实标注 ' + p.nTrue + ' 类）' : ''),
        tone: 'info',
        text: '共 ' + p.nSpots + ' 个 spot 被划入 ' + p.nDomains + ' 个空间域。' +
          (p.nTrue ? '对照右侧真实标注图，颜色块越连贯、边界越接近，说明识别越准。' : '') +
          '点击图例可隐藏某个域，单独查看感兴趣的区域。'
      });
    }
    if (p.embVar && (p.embVar[0] || p.embVar[1])) {
      msgs.push({
        title: '嵌入图：同域聚簇 = 结构已编码',
        tone: 'info',
        text: '同一空间域的 spot 在嵌入空间聚成一簇，说明表示学习有效。PC1 + PC2 共解释了 ' +
          ((p.embVar[0] + p.embVar[1]) * 100).toFixed(1) + '% 的方差。'
      });
    }
    report(msgs);
  }

  /** 单细胞页：渲染完结果后调用 */
  function scrna(d) {
    var msgs = [];
    msgs.push({ say: '这是 <b>' + (d.label || d.name) + '</b> 的 SaDGAE 聚类结果，我来帮你解读 👇' });
    var c;
    if ((c = ariCard(d.ari))) msgs.push(c);
    if (d.ari != null) {
      msgs.push({
        title: d.nCells + ' 个细胞聚成 ' + d.nClusters + ' 簇',
        tone: 'info',
        text: '每个点是一个细胞，颜色是预测的细胞类型。同一类型的细胞聚成一簇、不同簇分得开，' +
          '说明自适应细胞图把转录组相似的细胞拉到了一起。'
      });
    }
    if (!d.modelReady) {
      msgs.push({ title: '该数据集的训练权重未随系统部署', tone: 'mid',
        text: '指标仍为正式训练结果，可正常解读；仅「模型」列显示为部分就绪。' });
    }
    report(msgs);
  }

  // ── 初始化 ───────────────────────────────────────────────
  function init() {
    var style = document.createElement('style');
    style.id = 'cb-style';
    style.textContent = CSS;
    document.head.appendChild(style);

    launcher = el('button', 'cb-launcher');
    launcher.setAttribute('aria-label', '打开结果解读助手');
    launcher.innerHTML = MASCOT + '<span class="cb-badge"></span>';
    launcher.onclick = function () { opened ? closePanel() : openPanel(); };

    hello = el('div', 'cb-hello', '你好，我是小胞 🧫<br>分析出结果后，我来帮你解读。');
    hello.onclick = openPanel;

    panel = el('div', 'cb-panel');
    var head = el('div', 'cb-head');
    head.innerHTML = '<div class="cb-avatar">' + MASCOT + '</div>' +
      '<div><b>小胞</b><small>结果解读助手 · 规则生成，不会胡说</small></div>';
    var close = el('button', 'cb-close', '×');
    close.setAttribute('aria-label', '关闭');
    close.onclick = closePanel;
    head.appendChild(close);
    panel.appendChild(head);

    body = el('div', 'cb-body');
    panel.appendChild(body);

    chips = el('div', 'cb-chips');
    QA.forEach(function (item, i) {
      var chip = el('button', 'cb-chip', item.q);
      chip.onclick = function () { mode = 'qa'; qaActive = i; renderBody(); };
      chips.appendChild(chip);
    });
    panel.appendChild(chips);

    document.body.appendChild(launcher);
    document.body.appendChild(hello);
    document.body.appendChild(panel);
    badge = launcher.querySelector('.cb-badge');

    // 首次到访：3 秒问候气泡（不自动展开，不打扰）
    var seen = false;
    try { seen = global.localStorage.getItem('cb_seen') === '1'; } catch (e) { /* 隐私模式 */ }
    if (!seen && !helloShown) {
      helloShown = true;
      setTimeout(function () { hello.classList.add('on'); }, 1200);
      setTimeout(function () { hello.classList.remove('on'); }, 9000);
      try { global.localStorage.setItem('cb_seen', '1'); } catch (e) { /* 忽略 */ }
      hello.onclick = openPanel;
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  global.SFM = global.SFM || {};
  global.SFM.assistant = { report: report, predict: predict, scrna: scrna };
})(window);
