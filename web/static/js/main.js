/**
 * SFM-MA Web Demo Frontend
 * Dependencies: ECharts 5.x (loaded via CDN in index.html)
 */
const API_BASE = '';

// ── DOM Elements ──
const elDataset  = document.getElementById('sel-dataset');
const elSlice    = document.getElementById('sel-slice');
const elPanelSlice = document.getElementById('panel-slice');
const elType     = document.getElementById('sel-type');
const elAdapter  = document.getElementById('sel-adapter');
const elRate     = document.getElementById('inp-rate');
const btnRun     = document.getElementById('btn-run');
const elStatus   = document.getElementById('status');
const elLog      = document.getElementById('log-box');

// ── Chart Instances ──
let chartSpatial = null;

// ── State ──
let datasetsInfo = [];
let isRunning = false;

// ── Init ──
document.addEventListener('DOMContentLoaded', async () => {
    initCharts();
    await loadDatasets();
    btnRun.addEventListener('click', onRun);
    renderComparePlaceholder();
});

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
    renderComparePlaceholder();
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
            // 只更新了末尾，保持滚动条
            elLog.textContent = state.log;
            elLog.scrollTop = elLog.scrollHeight;
            lastLen = state.log.length;
        }

        if (state.epoch > 0) {
            elStatus.textContent = `Epoch ${state.epoch} | Loss ${state.loss}`;
        } else {
            elStatus.textContent = '正在初始化...';
        }

        if (state.done) {
            evtSource.close();
            elStatus.textContent = '推理完成';
            elStatus.className = 'status done';
            setTimeout(() => {
                refreshMetrics();
                refreshVisualize();
            }, 500);
            resetUI();
        }
    };
    evtSource.onerror = (e) => {
        console.warn('SSE error/close', e);
    };
}

function resetUI() {
    isRunning = false;
    btnRun.disabled = false;
    btnRun.textContent = '运行推理';
    if (!elStatus.classList.contains('done')) {
        elStatus.className = 'status';
    }
}

// ── Refresh Metrics & Visualization ──
async function refreshMetrics() {
    const res = await fetch(`${API_BASE}/api/metrics`);
    const rows = await res.json();
    renderMetrics(rows);
}

function renderMetrics(rows) {
    const name = elDataset.value;
    const sec  = elSlice.value || '';
    const row = rows.find(r => r.dataset === name && r.slice === sec);
    if (!row) return;

    document.getElementById('val-ari').textContent = row.ari != null ? row.ari.toFixed(3) : '-';
    document.getElementById('val-nmi').textContent = row.nmi != null ? row.nmi.toFixed(3) : '-';
    document.getElementById('val-sc').textContent  = row.sc  != null ? row.sc.toFixed(3)  : '-';
    document.getElementById('val-db').textContent  = row.db  != null ? row.db.toFixed(3)  : '-';
}

async function refreshVisualize() {
    const name = elDataset.value;
    const sec  = elSlice.value || '';
    const idx  = (name === 'MERFISH_frontal_cortex') ? sec : '';
    let url = `${API_BASE}/api/visualize/${name}`;
    if (sec) url += `?section=${encodeURIComponent(sec)}`;
    if (idx) url += `?idx=${encodeURIComponent(idx)}`;

    try {
        const res = await fetch(url);
        if (!res.ok) throw new Error('可视化数据未就绪');
        const data = await res.json();
        renderSpatial(data);
        renderCompareBar();
    } catch (e) {
        console.warn(e.message);
    }
}

// ── ECharts Renderers ──
function initCharts() {
    if (!chartSpatial) {
        chartSpatial = echarts.init(document.getElementById('chart-spatial'));
    }
    window.addEventListener('resize', () => {
        if (chartSpatial) chartSpatial.resize();
    });
}

function renderSpatial(data) {
    if (!data.x || !data.y || !data.domain) return;

    const domains = [...new Set(data.domain)];
    const palette = ['#5470c6','#91cc75','#fac858','#ee6666','#73c0de',
                     '#3ba272','#fc8452','#9a60b4','#ea7ccc','#37A2DA'];

    // 按 domain 分组数据，减少 series 数量
    const seriesData = {};
    for (let i = 0; i < data.x.length; i++) {
        const d = String(data.domain[i]);
        if (!seriesData[d]) seriesData[d] = [];
        seriesData[d].push([data.x[i], data.y[i]]);
    }

    const series = domains.map((d, i) => ({
        name: `Domain ${d}`,
        type: 'scatter',
        data: seriesData[d],
        symbolSize: 5,
        itemStyle: { color: palette[i % palette.length] },
        large: true,           // ECharts 大数据优化
        largeThreshold: 500
    }));

    chartSpatial.setOption({
        title: { text: 'Spatial Domain Clustering', left: 'center', textStyle: { fontSize: 14 } },
        tooltip: { trigger: 'item' },
        grid: { left: 10, right: 10, top: 40, bottom: 10 },
        xAxis: { type: 'value', scale: true, show: false },
        yAxis: { type: 'value', scale: true, show: false },
        series: series,
        legend: {
            data: series.map(s => s.name),
            top: 20, orient: 'horizontal',
            textStyle: { fontSize: 10 }, itemWidth: 12, itemHeight: 8
        },
        animation: false
    }, true);
}

function renderCompareBar() {
    // 显示对比图容器
    const sec = document.getElementById('section-extra');
    if (sec) sec.style.display = '';

    const name = elDataset.value;
    const baselineMap = {
        'DLPFC':  { ours: 0.70, stag: 0.46, grah: 0.55, dept: 0.50, scan: 0.29 },
        'Mouse_anterior_brain': { ours: 0.50, stag: 0.46, grah: 0.48, dept: 0.42, scan: 0.30 },
        'Breast_Cancer':        { ours: 0.71, stag: 0.52, grah: 0.58, dept: 0.55, scan: 0.35 },
        'STARmap':              { ours: 0.87, stag: 0.65, grah: 0.70, dept: 0.72, scan: 0.40 },
        'MERFISH':              { ours: 0.72, stag: 0.55, grah: 0.60, dept: 0.58, scan: 0.35 },
        'osmFISH':              { ours: 0.61, stag: 0.45, grah: 0.50, dept: 0.48, scan: 0.30 },
    };
    const b = baselineMap[name] || { ours: 0.50, stag: 0.40, grah: 0.45, dept: 0.42, scan: 0.25 };

    let chartCompare = echarts.getInstanceByDom(document.getElementById('chart-compare'));
    if (!chartCompare) {
        chartCompare = echarts.init(document.getElementById('chart-compare'));
    }

    chartCompare.setOption({
        title: { text: 'ARI Comparison: ' + name, left: 'center', textStyle: { fontSize: 13 } },
        animation: false,
        tooltip: { trigger: 'axis' },
        grid: { left: 50, right: 20, top: 40, bottom: 30 },
        xAxis: { type: 'category', data: ['SFM-MA','STAGATE','GraphST','DeepST','SCANPY'], axisLabel: { fontSize: 10 } },
        yAxis: { type: 'value', name: 'ARI', max: 1 },
        series: [{
            type: 'bar',
            data: [
                { value: b.ours, itemStyle: { color: '#1a73e8' } },
                { value: b.stag, itemStyle: { color: '#91cc75' } },
                { value: b.grah, itemStyle: { color: '#fac858' } },
                { value: b.dept, itemStyle: { color: '#ee6666' } },
                { value: b.scan, itemStyle: { color: '#73c0de' } }
            ],
            barWidth: '50%'
        }]
    }, true);
}

function renderComparePlaceholder() {
    const sec = document.getElementById('section-extra');
    if (sec) sec.style.display = 'none';
    const dom = document.getElementById('chart-compare');
    if (!dom) return;
    let chart = echarts.getInstanceByDom(dom);
    if (chart) chart.dispose();
}
