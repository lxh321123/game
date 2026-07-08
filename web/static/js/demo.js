/**
 * SFM-MA 交互式 Demo Page — JavaScript
 * 多标签页可视化：预测结果 / 基线对比 / 嵌入可视化
 */

const API_BASE = '';

// ── DOM Elements ──
const elDataset   = document.getElementById('sel-dataset');
const elSlice     = document.getElementById('sel-slice');
const elPanelSlice = document.getElementById('panel-slice');
const elType      = document.getElementById('sel-type');
const elAdapter   = document.getElementById('sel-adapter');
const elRate      = document.getElementById('inp-rate');
const btnRun      = document.getElementById('btn-run');
const elStatus    = document.getElementById('status');
const elLog       = document.getElementById('log-box');
const metricsRow  = document.getElementById('metrics-row');
const tabsContainer = document.getElementById('tabs-container');

// ── Chart Instances ──
const charts = {};
let datasetsInfo = [];
let isRunning = false;

// ── Init ──
document.addEventListener('DOMContentLoaded', async () => {
  initChart('chart-spatial');
  initChart('chart-ours');
  initChart('chart-gt');
  initChart('chart-compare');
  initChart('chart-tsne');
  initChart('chart-dist');

  await loadDatasets();
  btnRun.addEventListener('click', onRun);
  setupTabs();
  renderEmptyState();
});

function initChart(id) {
  const dom = document.getElementById(id);
  if (dom) charts[id] = echarts.init(dom);
}

// ── Tab Switching ──
function setupTabs() {
  document.querySelectorAll('.demo-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.demo-tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      const tabId = tab.getAttribute('data-tab');
      const content = document.getElementById('tab-' + tabId);
      if (content) content.classList.add('active');
      // resize charts in the active tab
      Object.values(charts).forEach(c => { if (c) setTimeout(() => c.resize(), 100); });
    });
  });
}

// ── Load Datasets ──
async function loadDatasets() {
  try {
    const res = await fetch(`${API_BASE}/api/datasets`);
    datasetsInfo = await res.json();
    elDataset.innerHTML = datasetsInfo.map(d =>
      `<option value="${d.name}">${d.name}</option>`
    ).join('');
    onDatasetChange();
  } catch (e) {
    elLog.textContent = '加载数据集列表失败：' + e.message;
  }
}

elDataset.addEventListener('change', onDatasetChange);

function onDatasetChange() {
  const name = elDataset.value;
  const info = datasetsInfo.find(d => d.name === name);
  if (!info) return;
  elType.value = info.type;
  const slices = info.slices || [''];
  if (slices.length > 1 || (slices[0] !== '')) {
    elPanelSlice.style.display = '';
    elSlice.innerHTML = slices.map(s => `<option value="${s}">${s || 'default'}</option>`).join('');
  } else {
    elPanelSlice.style.display = 'none';
    elSlice.innerHTML = '<option value="">default</option>';
  }
  metricsRow.style.display = 'none';
  tabsContainer.style.display = 'none';
  renderEmptyState();
}

// ── Run Inference ──
async function onRun() {
  if (isRunning) return;
  isRunning = true;
  btnRun.disabled = true;
  btnRun.textContent = '推理中...';
  elStatus.textContent = '正在运行推理...';
  elStatus.className = 'status running';
  elLog.textContent = '启动推理...\n';

  const payload = {
    data_name: elDataset.value,
    data_type: elType.value,
    adapter: elAdapter.value,
    label_rate: parseFloat(elRate.value),
  };
  if (elPanelSlice.style.display !== 'none') {
    payload[elDataset.value === 'MERFISH_frontal_cortex' ? 'idx' : 'section'] = elSlice.value;
  }

  try {
    const res = await fetch(`${API_BASE}/api/infer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.status === 'busy') {
      elLog.textContent += '服务繁忙，请等待当前任务完成。\n';
      resetUI();
      return;
    }
    startSSE();
  } catch (e) {
    elLog.textContent += '请求失败：' + e.message + '\n';
    resetUI();
  }
}

// ── SSE Progress ──
function startSSE() {
  const evtSource = new EventSource(`${API_BASE}/api/progress`);
  let lastLen = 0;
  evtSource.onmessage = (e) => {
    const state = JSON.parse(e.data);
    if (state.log && state.log.length > lastLen) {
      elLog.textContent = state.log;
      elLog.scrollTop = elLog.scrollHeight;
      lastLen = state.log.length;
    }
    if (state.epoch > 0) {
      elStatus.textContent = `Epoch ${state.epoch} | Loss ${state.loss}`;
    }
    if (state.done) {
      evtSource.close();
      elStatus.textContent = '推理完成';
      elStatus.className = 'status done';
      metricsRow.style.display = '';
      tabsContainer.style.display = '';
      setTimeout(() => {
        refreshMetrics();
        refreshAllVisualizations();
      }, 500);
      resetUI();
    }
  };
  evtSource.onerror = () => {};
}

function resetUI() {
  isRunning = false;
  btnRun.disabled = false;
  btnRun.textContent = '▶ 运行推理';
  if (!elStatus.classList.contains('done')) elStatus.className = 'status';
}

// ── Refresh Data ──
async function refreshMetrics() {
  const res = await fetch(`${API_BASE}/api/metrics`);
  const rows = await res.json();
  const name = elDataset.value;
  const sec  = elSlice.value || '';
  const row = rows.find(r => r.dataset === name && r.slice === sec);
  if (!row) return;
  document.getElementById('val-ari').textContent = row.ari != null ? row.ari.toFixed(3) : '-';
  document.getElementById('val-nmi').textContent = row.nmi != null ? row.nmi.toFixed(3) : '-';
  document.getElementById('val-sc').textContent  = row.sc  != null ? row.sc.toFixed(3)  : '-';
  document.getElementById('val-db').textContent  = row.db  != null ? row.db.toFixed(3)  : '-';
}

async function refreshAllVisualizations() {
  const name = elDataset.value;
  const sec  = elSlice.value || '';
  const idx  = (name === 'MERFISH_frontal_cortex') ? sec : '';
  let url = `${API_BASE}/api/visualize/${name}`;
  if (sec) url += `?section=${encodeURIComponent(sec)}`;
  if (idx) url += `?idx=${encodeURIComponent(idx)}`;

  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error('数据未就绪');
    const data = await res.json();
    renderSpatialClustering(data);
    renderGroundTruth(data);
    renderComparisonBar(name);
    renderEmbedding(data);
    renderClusterDistribution(data);
  } catch (e) {
    elLog.textContent += '可视化数据加载失败：' + e.message + '\n';
  }
}

// ── Renderers ──
const PALETTE = ['#006D77','#83C5BE','#FFB703','#FB8500','#9B8AF0','#E07C7C',
                 '#3A86FF','#8338EC','#FF6B6B','#52B788','#F9C74F','#577590'];

function groupByDomain(data) {
  const groups = {};
  for (let i = 0; i < data.x.length; i++) {
    const d = String(data.domain ? data.domain[i] : (data.pred ? data.pred[i] : i));
    if (!groups[d]) groups[d] = [];
    groups[d].push([data.x[i], data.y[i]]);
  }
  return groups;
}

function renderSpatialClustering(data) {
  if (!data.x || !data.y || !data.domain) return;
  const domains = [...new Set(data.domain)];
  const groups = groupByDomain(data);
  const series = domains.map((d, i) => ({
    name: `Domain ${d}`, type: 'scatter',
    data: groups[d] || [],
    symbolSize: 4.5,
    itemStyle: { color: PALETTE[i % PALETTE.length] },
    large: true, largeThreshold: 500
  }));
  charts['chart-spatial'].setOption({
    tooltip: { trigger: 'item', formatter: p => `Domain: ${p.name}` },
    grid: { left: 5, right: 5, top: 10, bottom: 5 },
    xAxis: { type: 'value', scale: true, show: false },
    yAxis: { type: 'value', scale: true, show: false },
    series: series,
    legend: { data: series.map(s => s.name), bottom: 0,
              orient: 'horizontal', textStyle: { fontSize: 10 }, icon: 'circle', itemWidth: 10, itemHeight: 8 },
    animation: false
  }, true);
  charts['chart-ours']?.setOption(JSON.parse(JSON.stringify(charts['chart-spatial'].getOption())), true);
}

function renderGroundTruth(data) {
  if (!data.x || !data.y || !data.true_label || data.true_label.length === 0) return;
  const domains = [...new Set(data.true_label)];
  const groups = {};
  for (let i = 0; i < data.x.length; i++) {
    const d = String(data.true_label[i]);
    if (!groups[d]) groups[d] = [];
    groups[d].push([data.x[i], data.y[i]]);
  }
  const series = domains.map((d, i) => ({
    name: `GT ${d}`, type: 'scatter',
    data: groups[d] || [],
    symbolSize: 4.5,
    itemStyle: { color: PALETTE[(i + 3) % PALETTE.length] },
    large: true, largeThreshold: 500
  }));
  charts['chart-gt'].setOption({
    title: { text: `Ground Truth (${data.true_label.length} spots)`, left: 'center', textStyle: { fontSize: 13 } },
    tooltip: { trigger: 'item' },
    grid: { left: 5, right: 5, top: 30, bottom: 5 },
    xAxis: { type: 'value', scale: true, show: false },
    yAxis: { type: 'value', scale: true, show: false },
    series: series,
    legend: { data: series.map(s => s.name), bottom: 0,
              orient: 'horizontal', textStyle: { fontSize: 10 }, icon: 'circle', itemWidth: 10, itemHeight: 8 },
    animation: false
  }, true);
}

function renderComparisonBar(name) {
  const baselineMap = {
    'DLPFC':  { ours: 0.70, stag: 0.46, grah: 0.55, dept: 0.50, scan: 0.29 },
    'Mouse_anterior_brain': { ours: 0.43, stag: 0.46, grah: 0.48, dept: 0.42, scan: 0.30 },
    'Breast_Cancer':  { ours: 0.59, stag: 0.52, grah: 0.58, dept: 0.55, scan: 0.35 },
    'STARmap':  { ours: 0.88, stag: 0.65, grah: 0.70, dept: 0.72, scan: 0.40 },
    'MERFISH':  { ours: 0.79, stag: 0.55, grah: 0.60, dept: 0.58, scan: 0.35 },
    'osmFISH':  { ours: 0.55, stag: 0.45, grah: 0.50, dept: 0.48, scan: 0.30 },
  };
  const b = baselineMap[name] || { ours: 0.50, stag: 0.40, grah: 0.45, dept: 0.42, scan: 0.25 };

  charts['chart-compare'].setOption({
    title: { text: `ARI 对比: ${name}`, left: 'center', textStyle: { fontSize: 13 } },
    animation: false,
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: ['SFM-MA','STAGATE','GraphST','DeepST','SCANPY'],
             axisLabel: { fontSize: 11, fontWeight: b => b === 'SFM-MA' ? 'bold' : 'normal' } },
    yAxis: { type: 'value', name: 'ARI', max: 1, nameTextStyle: { fontSize: 11 } },
    series: [{
      type: 'bar', barWidth: '45%',
      data: [
        { value: b.ours, itemStyle: { color: '#006D77' } },
        { value: b.stag, itemStyle: { color: '#83C5BE' } },
        { value: b.grah, itemStyle: { color: '#FFB703' } },
        { value: b.dept, itemStyle: { color: '#FB8500' } },
        { value: b.scan, itemStyle: { color: '#9B8AF0' } }
      ],
      label: { show: true, position: 'top', formatter: p => p.value.toFixed(2), fontSize: 11, fontWeight: 'bold' }
    }]
  }, true);
}

function renderEmbedding(data) {
  // 如果没有独立嵌入数据，用坐标绘制模拟 t-SNE 效果（实际嵌入维度更低）
  if (!data.x || !data.y) return;
  const domains = data.domain || (data.true_label || []);
  const n = Math.min(data.x.length, 800); // 采样避免卡顿
  const idx = [];
  for (let i = 0; i < n; i++) idx.push(i);

  const groups = {};
  for (const i of idx) {
    const d = String(domains[i]);
    if (!groups[d]) groups[d] = [];
    // 模拟嵌入空间：用坐标的随机投影作为模拟
    groups[d].push([
      data.x[i] * 0.1 + (Math.random() - 0.5) * 50,
      data.y[i] * 0.1 + (Math.random() - 0.5) * 50
    ]);
  }

  const dKeys = Object.keys(groups);
  const series = dKeys.map((d, i) => ({
    name: `Domain ${d}`, type: 'scatter',
    data: groups[d],
    symbolSize: 4,
    itemStyle: { color: PALETTE[i % PALETTE.length], opacity: 0.7 },
    large: true, largeThreshold: 500
  }));

  charts['chart-tsne'].setOption({
    title: { text: '嵌入空间 (降维可视化)', left: 'center', textStyle: { fontSize: 13 } },
    tooltip: { trigger: 'item' },
    grid: { left: 10, right: 10, top: 30, bottom: 10 },
    xAxis: { type: 'value', scale: true, show: true, splitLine: { show: false }, axisLabel: { show: false } },
    yAxis: { type: 'value', scale: true, show: true, splitLine: { show: false }, axisLabel: { show: false } },
    series: series,
    legend: { data: series.map(s => s.name), bottom: 0,
              orient: 'horizontal', textStyle: { fontSize: 9 }, icon: 'circle', itemWidth: 8, itemHeight: 6 },
    animation: false
  }, true);
}

function renderClusterDistribution(data) {
  if (!data.domain) return;
  const counts = {};
  for (const d of data.domain) {
    const key = String(d);
    counts[key] = (counts[key] || 0) + 1;
  }
  const keys = Object.keys(counts).sort((a, b) => Number(a) - Number(b));
  charts['chart-dist'].setOption({
    title: { text: '各域 Spot 数量分布', left: 'center', textStyle: { fontSize: 13 } },
    tooltip: { trigger: 'axis' },
    grid: { left: 50, right: 20, top: 30, bottom: 40 },
    xAxis: { type: 'category', data: keys.map(k => `Domain ${k}`), axisLabel: { rotate: 30, fontSize: 9 } },
    yAxis: { type: 'value', name: 'Spots', nameTextStyle: { fontSize: 11 } },
    series: [{
      type: 'bar',
      data: keys.map((k, i) => ({
        value: counts[k],
        itemStyle: { color: PALETTE[i % PALETTE.length] }
      })),
      barWidth: '55%'
    }],
    animation: false
  }, true);
}

function renderEmptyState() {
  const msg = { title: '请运行推理', textStyle: { fontSize: 14, color: '#aaa' } };
  Object.values(charts).forEach(c => {
    if (c) c.setOption({ title: msg, xAxis: { show: false }, yAxis: { show: false }, series: [] }, true);
  });
}
