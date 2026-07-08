# SFM-MA Web 前端 — 交接文档

## 一、项目概述

SFM-MA 是一个面向空间转录组学（ST）的多模态预训练与轻量化适配框架。
Web Demo 用于比赛展示，包含两个页面：

| 页面 | 路由 | 功能 |
|------|------|------|
| 首页（介绍页） | `/` | 展示模型背景、架构图、核心创新、应用领域 |
| Demo 页（交互页） | `/demo` | 选择数据集 → 运行推理 → 多角度可视化结果展示 |

---

## 二、接口文档

> 基础 URL：`http://localhost:5000`

### 2.1 GET /api/datasets
获取支持的数据集列表。

**响应示例：**
```json
[
  {
    "name": "DLPFC",
    "type": "labeled",
    "slices": ["151507","151508","151509","151510","151669",
               "151670","151671","151672","151673","151674","151675","151676"]
  },
  {
    "name": "Mouse_anterior_brain",
    "type": "labeled",
    "slices": [""]
  },
  {
    "name": "Breast_Cancer",
    "type": "labeled",
    "slices": [""]
  },
  {
    "name": "STARmap",
    "type": "labeled",
    "slices": [""]
  },
  {
    "name": "human_lung_cancer",
    "type": "unlabeled",
    "slices": [""]
  },
  {
    "name": "human_ovarian_cancer",
    "type": "unlabeled",
    "slices": [""]
  },
  {
    "name": "MERFISH",
    "type": "only_gene",
    "slices": [""]
  },
  {
    "name": "MERFISH_frontal_cortex",
    "type": "only_gene",
    "slices": ["4_0","4_1","4_2","6_0","6_1","6_2","8_0","8_1","8_2"]
  },
  {
    "name": "osmFISH",
    "type": "only_gene",
    "slices": [""]
  }
]
```

**字段说明：**
| 字段 | 类型 | 说明 |
|------|------|------|
| name | string | 数据集名称 |
| type | string | 数据类型：labeled / unlabeled / only_gene |
| slices | string[] | 切片列表（单一切片用 `[""]` 占位） |

---

### 2.2 POST /api/infer
触发推理任务。

**请求体：**
```json
{
  "data_name": "DLPFC",
  "data_type": "labeled",
  "adapter": "ce",
  "label_rate": 0.05,
  "section": "151671",
  "idx": ""
}
```

**参数说明：**
| 参数 | 必填 | 类型 | 默认值 | 说明 |
|------|------|------|--------|------|
| data_name | ✅ | string | — | 数据集名称 |
| data_type | ✅ | string | labeled | labeled / unlabeled / only_gene |
| adapter | ✅ | string | ce | ce / kl |
| label_rate | ✅ | number | 0.05 | 0.01 ~ 1.0 |
| section | ❌ | string | "" | DLPFC 时必填切片ID |
| idx | ❌ | string | "" | MERFISH_frontal_cortex 时必填索引 |

**返回示例（立即返回，推理异步执行）：**
```json
{
  "status": "started"
}
```

**当服务器繁忙时返回 503：**
```json
{
  "status": "busy",
  "msg": "Another inference is running."
}
```

---

### 2.3 GET /api/progress
SSE（Server-Sent Events）实时进度推送。

**响应格式（SSE stream）：**
```
data: {"dataset":"DLPFC","epoch":12,"loss":1.2345,"done":false,"log":"..."}

data: {"dataset":"DLPFC","epoch":50,"loss":0.8567,"done":false,"log":"..."}

data: {"dataset":"DLPFC","epoch":0,"loss":0,"done":true,"log":"..."}
```

**字段说明：**
| 字段 | 类型 | 说明 |
|------|------|------|
| dataset | string | 当前推理的数据集 |
| epoch | number | 当前 epoch 数（0 表示初始化/完成） |
| loss | number | 当前 loss 值 |
| done | boolean | 是否完成 |
| log | string | 最近的日志文本 |

**前端使用示例（JS）：**
```javascript
const evtSource = new EventSource('/api/progress');
evtSource.onmessage = (e) => {
  const state = JSON.parse(e.data);
  if (state.done) { evtSource.close(); }
};
```

---

### 2.4 GET /api/metrics
获取所有已完成的推理结果指标。

**返回示例：**
```json
[
  {
    "dataset": "DLPFC",
    "slice": "151671",
    "ari": 0.844,
    "nmi": 0.783,
    "sc": 0,
    "db": 0
  },
  {
    "dataset": "Breast_Cancer",
    "slice": "",
    "ari": 0.523,
    "nmi": 0.542,
    "sc": 0,
    "db": 0
  }
]
```

**字段说明：**
| 字段 | 类型 | 说明 |
|------|------|------|
| dataset | string | 数据集名称 |
| slice | string | 切片ID（单一切片为空字符串） |
| ari | float | Adjusted Rand Index |
| nmi | float | Normalized Mutual Information |
| sc | float | Silhouette Coefficient（unlabeled 数据集有值） |
| db | float | Davies-Bouldin Index（unlabeled 数据集有值） |

---

### 2.5 GET /api/visualize/:dataset

获取可视化用的空间坐标与标签数据。

**查询参数：**
| 参数 | 必填 | 类型 | 说明 |
|------|------|------|------|
| section | ❌ | string | DLPFC 时必填切片ID |
| idx | ❌ | string | MERFISH_frontal_cortex 时必填索引 |

**返回示例：**
```json
{
  "x": [4176.0, 4259.0, 4342.0, ...],
  "y": [4046.0, 4046.0, 4046.0, ...],
  "domain": ["0", "1", "2", "0", "1", ...],
  "true_label": ["0", "1", "2", ...]
}
```

**字段说明：**
| 字段 | 类型 | 说明 |
|------|------|------|
| x | float[] | 所有 spot 的 x 坐标 |
| y | float[] | 所有 spot 的 y 坐标 |
| domain | string[] | 预测的空间域标签 |
| true_label | string[] | 真实的标注标签（无标注时为空数组） |

**注意：** 数据量可能较大（单个切片 3000-5000 个点），建议使用 large 模式渲染。

---

## 三、启动方式

### 3.1 后端（Flask）

```bash
cd ~/sfm-ma/web
pip install flask flask-cors -q
python app.py
```

启动后访问：
- http://localhost:5000 — 首页
- http://localhost:5000/demo — Demo 页

### 3.2 前端开发模式

可以用 Vite / live-server 等工具进行独立开发，通过配置 proxy 或直接请求后端地址。

---

## 四、设计参考

### 4.1 配色方案

```css
--primary: #006D77;        /* 主色：深青 */
--primary-light: #83C5BE;  /* 浅青 */
--accent: #FFB703;         /* 强调色：金黄色 */
--bg: #f0f4f8;            /* 背景 */
--bg-card: #ffffff;        /* 卡片背景 */
--text: #1a1a2e;           /* 正文颜色 */
--text-secondary: #5a6a7a; /* 次要文字 */
```

### 4.2 页面设计参考

参照 `static/index.html` 和 `static/demo.html` 的布局结构。两份 HTML 文件包含完整的设计示例，你可以直接用也可以用来理解设计意图。

### 4.3 图标资源

页面中使用的图标目前使用 Unicode emoji（🧬 🔬 🧠 等），建议替换为专业图标库：
- [Lucide Icons](https://lucide.dev/)（开源，适合 Vue）
- [Font Awesome](https://fontawesome.com/)（免费版够用）

---

## 五、交付物清单

| 交付物 | 文件/位置 | 说明 |
|--------|----------|------|
| 首页 HTML | `static/index.html` | 页面结构和内容示例 |
| Demo 页 HTML | `static/demo.html` | 交互页面结构 |
| CSS 设计系统 | `static/css/style.css` | 完整的样式系统 |
| Demo JS 逻辑 | `static/js/demo.js` | API 调用与 ECharts 渲染 |
| 架构图 | `static/figs/SFM-MA-workflow.png` | 需要从论文中导出 |

---

## 六、数据流图

```
用户点击"运行推理"
    │
    ▼
POST /api/infer ──→ 后端触发子进程 → 返回 `{"status":"started"}`
    │
    ▼
建立 EventSource → GET /api/progress  (SSE 实时接收 epoch/loss)
    │
    ▼
推理完成 → done=true → evtSource.close()
    │
    ▼
GET /api/metrics  ─── 获取 ARI/NMI 等指标
GET /api/visualize ── 获取空间坐标 + 预测标签
    │
    ▼
渲染 ECharts 图表：散点图 / 柱状图 / 嵌入图
```

---

## 七、技术选型建议

| 技术 | 建议 | 说明 |
|------|------|------|
| 框架 | Vue 3 + Vite | 推荐，本仓库兼容 |
| 图表 | ECharts 5 | 已接入，大散点图性能好 |
| HTTP 请求 | Fetch API / Axios | 自由选择 |
| SSE | EventSource / fetch stream | 已原生支持 |
| CSS | UnoCSS / Tailwind / 原生 | 自由选择 |
