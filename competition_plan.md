# 全球校园人工智能算法精英大赛 — 算法模型创新赛

## 参赛项目规划报告（含 Web Service 展示方案）

**项目名称**：SFM-MA：面向空间转录组学的多模态基础模型与轻量化任务适配框架  
**团队规模**：3 人  
**核心创新**：多数据集预训练 + 非对称交叉注意力融合 + 冻结骨干轻量化适配（Few-Shot）  
**展示形式**：算法模型 + Web Demo 平台

---

## 一、项目总体架构

### 1.1 技术定位

本项目针对 **空间转录组学（Spatial Transcriptomics, ST）** 领域的核心难题——**空间域识别（Spatial Domain Identification）**，提出了一套完整的 "预训练-适配" 算法框架：

| 模块 | 技术内容 | 创新点 |
|------|---------|--------|
| **S1 预训练** | 跨 9 个公共数据集构建通用语料，训练多模态基础模型 | 首次在 ST 领域引入多数据集联合预训练范式 |
| **非对称融合** | 基因表达作为 Query，图像特征作为 Key/Value 的交叉注意力 | 生物学先验驱动的非对称模态融合机制 |
| **S2 适配器** | 冻结预训练骨干，仅训练轻量 MLP/KL 分类器 | 1% 标注即可达到接近全监督性能 |

### 1.2 整体流程图

```
┌──────────────────────────────────────────────────────────────┐
│  九大数据集 (DLPFC / MERFISH / STARmap / 癌变组织 等)          │
│  ↓ PCA降维(100d) + 半径邻接图(r=150)                         │
│  ┌────────────────────────────────────────────────────┐     │
│  │  Stage S1: 多数据集无监督预训练                      │     │
│  │  • 非对称交叉注意力融合 (基因Q ← 图像K/V)            │     │
│  │  • 2层GCN编码器 (100→256→128)                       │     │
│  │  • 四重损失: 重建 + 对比 + 邻近 + KL聚类             │     │
│  └────────────────────────────────────────────────────┘     │
│  ↓ 保存通用权重: model/common_model/common_model_all.pt      │
│  ┌────────────────────────────────────────────────────┐     │
│  │  Stage S2: 冻结骨干 + 轻量任务适配器                 │     │
│  │  • 加载LLM → freeze全部参数                          │     │
│  │  • 提取固定嵌入 H (N×128)                            │     │
│  │  • 仅训练适配器: MLP(CE) 或 Soft-KL(KL)              │     │
│  │  • 采样 1% 标注点 / 无标注用Leiden聚类               │     │
│  └────────────────────────────────────────────────────┘     │
│  ↓ 输出: 空间域识别结果 + ARI/NMI 评估                       │
└──────────────────────────────────────────────────────────────┘
```

---

## 二、Web Service 演示平台

为了更直观地展示 SFM-MA 的算法效果，我们构建了一个基于 **Flask** 的轻量级 Web 演示平台。评委和观众可以通过浏览器直接选择数据集、一键运行推理并查看空间域识别结果与定量指标。

### 2.1 平台架构

```
┌─────────────────────────────────────────────────────────────┐
│                        用户浏览器                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │ 数据集选择面板│  │ 参数配置面板 │  │   运行按钮   │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
│                         │                                   │
│  ┌─────────────────────────────────────────────────────┐  │
│  │            结果可视化区 (ECharts / Plotly)            │  │
│  │  ┌────────────────┐  ┌──────────────────────────┐  │  │
│  │  │ 空间域聚类图    │  │  ARI/NMI 指标卡片        │  │  │
│  │  │ (组织切片散点图)│  │  对比柱状图 / 雷达图      │  │  │
│  │  └────────────────┘  └──────────────────────────┘  │  │
│  └─────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │ REST API
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Backend: Flask (Python)                   │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │ /api/datasets│  │ /api/infer  │  │/api/visualize│        │
│  │   GET       │  │   POST      │  │   GET       │        │
│  └─────────────┘  └─────────────┘  └─────────────┘        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │/api/metrics │  │/api/download│  │/api/progress│        │
│  │   GET       │  │   GET       │  │   SSE       │        │
│  └─────────────┘  └─────────────┘  └─────────────┘        │
└─────────────────────────────────────────────────────────────┘
                              │ subprocess / import
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Core Algorithm (run_adapter.py)                 │
│  ┌──────────────────┐    ┌──────────────────────────────┐  │
│  │ pretrained LLM   │───▶│ frozen embedding extraction  │  │
│  │ common_model_all.pt   │                              │  │
│  └──────────────────┘    └──────────────────────────────┘  │
│                              │                              │
│                              ▼                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  lightweight adapter: MLP-CE or KL-soft classifier   │  │
│  │  input: 1% labels  /  output: spatial domain labels  │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 前端页面设计

页面采用 **左右分栏** 布局，风格简洁、结果直观：

**左侧控制面板（30% 宽度）**
- **数据集选择器**：下拉菜单列出全部 9 个数据集（含 DLPFC 12 slice、MERFISH_frontal_cortex 9 slice）
- **参数配置**：数据类型(labeled/unlabeled/only_gene)、适配器类型(CE/KL)、标注比例(1%)
- **运行按钮**：一键触发后端推理，带加载动画
- **推理日志窗口**：折叠面板，实时显示 tqdm 进度

**右侧结果展示区（70% 宽度）**
- **空间域聚类图**（上半部分，最大区域）：以组织切片为背景，每个 spot 按预测 domain 着色，支持缩放、悬停显示 spot ID 与置信度
- **指标对比区**（右上）：ARI / NMI 数字卡片 + 与基线方法（STAGATE / GraphST / DeepST）的同数据集对比柱状图
- **消融实验雷达图**（右下）：展示完整模型 vs 无预训练 / 无适配器 / 对称融合 / 无图结构 的 ARI 对比
- **下载区**：提供 `res_data.h5ad` 与 `CSV 指标表` 下载

### 2.3 后端 API 定义

| 接口 | 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|------|
| `/api/datasets` | GET | — | `[{"name":"DLPFC","slices":12},...]` | 获取支持的数据集列表 |
| `/api/infer` | POST | `data_name`, `section`, `data_type`, `adapter`, `label_rate` | `{"status":"done","ari":0.52,"nmi":0.51,"time_s":23}` | 触发推理 |
| `/api/visualize` | GET | `data_name`, `section` | `{"x":[...],"y":[...],"domain":[...],"colors":[...]}` | 获取散点图数据 |
| `/api/metrics` | GET | `data_name`（可选） | `{"DLPFC":{"ari":0.52,"nmi":0.51},...}` | 全量或单数据集指标 |
| `/api/download` | GET | `data_name`, `section`, `file_type` | `Blob` | 下载 h5ad 或 CSV |
| `/api/progress` | SSE | — | `{"epoch":120,"loss":0.51}` | 推理进度实时推送 |

### 2.4 核心交互流程

```
sequenceDiagram
    participant User as 用户
    participant FE as 前端 (HTML+JS)
    participant BE as Flask 后端
    participant ALG as run_adapter.py

    User->>FE: 选择数据集 & 点击"运行推理"
    FE->>BE: POST /api/infer (data_name, section, ...)
    BE->>ALG: subprocess.Popen(python run_adapter.py ...)
    ALG-->>BE: stdout (epoch/loss/ARI/NMI)
    BE-->>FE: SSE /api/progress (实时进度)
    ALG-->>BE: 完成，res_data.h5ad 已生成
    BE-->>FE: JSON {status:"done", ari:0.52, nmi:0.51}
    FE->>BE: GET /api/visualize
    BE->>ALG: 读取 h5ad 的 obsm['spatial'] + obs['domain']
    BE-->>FE: 散点图坐标与颜色映射
    FE->>User: 渲染空间域聚类图 + 指标卡片
    User->>FE: 点选"对比基线"按钮
    FE->>BE: GET /api/metrics?data_name=DLPFC
    BE-->>FE: SFM-MA vs STAGATE vs GraphST 对比 JSON
    FE->>User: 渲染对比柱状图 + 消融雷达图
```

### 2.5 部署方式

**开发环境（本地调试）**

```bash
cd ~/sfm-ma/web
pip install flask flask-cors gunicorn
python app.py          # 默认 http://localhost:5000
```

**生产环境（AutoDL / 云服务器）**

```bash
cd ~/sfm-ma/web
gunicorn -w 2 -b 0.0.0.0:5000 --timeout 300 app:app
```

> **注意**：AutoDL 实例需要暴露自定义端口。在控制台 → "更多" → "自定义服务" 中开启端口映射，将外部端口映射到实例内部的 `5000`。

**前端静态页面**

前端为纯 HTML + JavaScript（无需构建工具），直接放在 `web/static/` 目录下，由 Flask 的 `send_from_directory` 提供访问：

```
web/
├── app.py                  # Flask 主程序
├── static/
│   ├── index.html          # 单页应用入口
│   ├── css/
│   │   └── style.css       # 自定义样式
│   └── js/
│       ├── main.js         # 页面交互逻辑
│       └── chart.js        # ECharts 图表封装
└── templates/
    └── index.html          # Jinja2 模板（可选）
```

### 2.6 Web 团队成员职责（新增）

| 角色 | Web 相关职责 | 交付物 |
|------|-------------|--------|
| **成员B（工程负责人）** | Flask 后端开发、API 接口实现、模型加载与并发控制、服务器部署 | `app.py`、API 文档、部署脚本 |
| **成员C（实验负责人）** | 前端可视化页面（ECharts / Plotly）、结果图表设计、Demo 录屏与演示文案 | `index.html`、`main.js`、演示视频 |
| **成员A（算法负责人）** | 确保推理脚本能被后端稳定调用、提供推理接口文档、处理模型前/后向兼容 | `run_adapter.py` CLI 规范、接口文档 |

---

## 三、团队成员与分工

### 3.1 角色定义（原算法/工程/实验分工不变）

| 角色 | 姓名（待定） | 核心职责 | 产出物 |
|------|-------------|---------|--------|
| **算法负责人** | 成员A | 模型架构设计、损失函数优化、消融实验 | 核心代码、算法说明书 |
| **工程负责人** | 成员B | 数据管线、训练脚本、显存优化、AutoDL部署、**Web 后端开发** | 训练框架、可复现环境、Flask API |
| **实验负责人** | 成员C | 全量实验跑测、指标统计、可视化、**前端页面**、论文/报告撰写 | 实验结果、对比图表、Web Demo、技术报告 |

### 3.2 时间规划（8周制，含 Web 开发）

```
Week 1-2: 基础架构搭建
├─ [成员A] 论文研读 + 模型架构定稿 + 损失函数设计
├─ [成员B] 数据预处理管线 + 训练脚本框架 + Python环境/AutoDL配置
└─ [成员C] 数据集整理 + 测试基线算法(DeepST/GraphST等)

Week 3-4: 核心训练
├─ [成员A] S1预训练模块开发 + 非对称注意力融合实现
├─ [成员B] 显存优化(sparse.mm) + 多GPU支持 + 训练监控
└─ [成员C] 基线对比实验 + 中间结果可视化 + 消融实验设计

Week 5-6: 下游适配 + Web Demo
├─ [成员A] S2适配器(CE/KL) + 早停/采样策略
├─ [成员B] 模型权重存储/加载 + 批量推理脚本 + **Flask后端开发**
└─ [成员C] 全量9数据集评估 + 统计显著性检验 + **前端可视化页面**

Week 7-8: 包装提交 + Demo 录制
├─ [成员A] 算法创新点提炼 + 伪代码/流程图
├─ [成员B] 代码清理 + README + requirements + **服务器部署 + 域名映射**
└─ [成员C] 报告撰写 + PPT制作 + **Demo录屏** + 答辩预演
```

---

## 四、关键里程碑（含 Web Demo）

| 里程碑 | 截止周 | 验收标准 | 负责人 |
|--------|-------|---------|--------|
| S1预训练收敛 | W4 | loss < 0.8 且可复现 | 成员B |
| S2单数据推理通过 | W5 | DLPFC 151507 ARI > 0.5 | 成员A |
| 9数据集全部跑通 | W6 | result_data/ 下文件完整 | 成员C |
| **Web Demo 可用** | **W6** | **浏览器可访问 5000 端口，可选择数据集并查看结果** | **成员B + C** |
| 基线对比完成 | W6 | ≥4个基线方法ARI对比表 | 成员C |
| 报告初稿 | W7 | 12页技术报告 + 流程图 | 成员C |
| **Demo 视频录制** | **W7** | **3分钟展示视频（选数据集→推理→看结果）** | **成员C** |
| 提交物打包 | W8 | 代码/模型/报告/视频齐全 | 成员B |

---

## 五、技术攻坚路线图

### 5.1 核心算法流程

```
Stage S1: Multi-Dataset Unsupervised Pretraining
═══════════════════════════════════════════════════
Input: 9 datasets (128K spots, 28 slices)
  ├── PCA(100d) for gene & image features
  ├── Radius Graph (r=150) per slice
  ├── Asymmetric Cross-Attention Fusion
  │      X (gene) ──Q──> Query
  │      C (image) ──K,V──> Key, Value
  ├── 2-layer GCN Encoder (100→256→128)
  ├── 4 Losses:
  │      L_rec + L_ct + L_nc + L_kl
  └── Output: common_model_all.pt (通用权重)

                     │
                     ▼

Stage S2: Frozen-Backbone + Lightweight Task Adapter
═════════════════════════════════════════════════════
Input: Target Dataset (e.g., DLPFC 151507)
  ├── Load LLM → freeze all parameters (requires_grad=False)
  ├── Extract frozen embedding H (N×128)
  ├── Sample 1% labeled spots per class
  ├── Train Adapter:
  │      CE:  MLP(128→64→32→K) + CrossEntropyLoss
  │      KL:  Soft-classifier + KL-divergence
  └── Output: Spatial domain labels + ARI/NMI metrics
```

### 5.2 显存优化里程碑（已验证）

| 问题 | 解决方案 | 成效 |
|------|---------|------|
| `AvgReadout.to_dense()` | 改为 `torch.sparse.mm` | 显存从 **29GB → 30MB** |
| `index_add_` 全边张量 | Python 循环分块累加 → `sparse.mm` | 避免 4.5GB 单次分配 |
| PCA `n_components=100` | `min(100, n_samples, n_features)-1` | 兼容小样本数据集（如 osmFISH 33 cells） |
| PyG 依赖缺失 | 自研 `GraphConvLayer` 纯 PyTorch 实现 | 零外部图神经网络库依赖 |
| `autocast() fp16` | 移除混合精度，全程 fp32 | 避免 CUDA `Half` assert 错误 |
| `opt.py` 模块缺失 | 注入假 `opt` 模块到 `sys.modules` | 兼容原有 `utils.py` 调用 |
| **Flask SSE 进度推送** | **subprocess.stdout 实时读取 + yield** | **用户可实时看到 epoch 进度** |

---

## 六、实验评估方案

### 6.1 数据集清单（共 9 个数据集，28 个切片）

| 数据集 | 模态 | 类型 | 切片数 | 适配器类型 |
|--------|------|------|--------|-----------|
| DLPFC | gene + image | labeled | 12 | CE |
| Mouse_anterior_brain | gene + image | labeled | 1 | CE |
| Breast_Cancer | gene + image | labeled | 1 | CE |
| STARmap | gene only | labeled | 1 | CE |
| human_lung_cancer | gene + image | unlabeled | 1 | Leiden |
| human_ovarian_cancer | gene + image | unlabeled | 1 | Leiden |
| MERFISH | gene only | only_gene | 1 | CE |
| MERFISH_frontal_cortex | gene only | only_gene | 9 | CE |
| osmFISH | gene only | only_gene | 1 | CE |

### 6.2 对比基线

| 对比方法 | 类型 | 评估指标 |
|---------|------|---------|
| SCANPY (Louvain) | 传统聚类 | ARI, NMI |
| SpaGCN | GNN + 图像 | ARI, NMI |
| STAGATE | GNN | ARI, NMI |
| DeepST | 自编码 + 图像 | ARI, NMI |
| GraphST | 图对比学习 | ARI, NMI |
| **SFM-MA (Ours)** | **预训练 + 适配** | **ARI, NMI, SC, DB** |

### 6.3 消融实验矩阵

| 消融项 | 配置 | 预期影响 |
|--------|------|---------|
| 无预训练 | 直接训练下游模型 | ARI 显著下降 |
| 无适配器 | KMeans 直接聚类冻结嵌入 | ARI 大幅下降 |
| 对称融合 | Concat 取代非对称注意力 | ARI 下降 |
| 无图结构 | MLP 取代 GCN | ARI 严重下降 |
| 无图像模态 | 仅基因 expression | ARI 在 image-based 数据集下降 |

---

## 七、代码提交物结构（含 Web）

```
sfm-ma/
├── run_adapter.py              # 核心下游推理脚本（一行命令运行）
├── common-model/
│   └── train_common_model.py      # S1预训练代码
├── utils.py                    # 数据读取 / 图构建 / 工具函数
├── requirements.txt            # 依赖列表（PyTorch / scanpy / sklearn / flask）
├── model/
│   └── common_model/
│       ├── common_model_all.pt     # 预训练权重（约 5MB）
│       └── common_embedding.npy    # 全语料嵌入（可选）
├── result_data/                # 9 个数据集推理结果（.h5ad）
├── data/                       # 原始数据集（提交时不包含）
├── web/                        # ← 新增 Web 演示平台
│   ├── app.py                  # Flask 后端主程序
│   ├── static/
│   │   ├── index.html          # 前端单页应用
│   │   ├── css/style.css       # 样式表
│   │   └── js/
│   │       ├── main.js         # 页面交互与图表渲染
│   │       └── chart.js        # ECharts 图表封装
│   └── templates/
│       └── index.html          # Jinja2 模板（可选）
├── README.md                   # 快速开始指南
├── competition_plan.md         # 本规划报告
└── demo_video.mp4              # 3分钟演示视频（可选）
```

---

## 八、风险预案（含 Web Demo）

| 风险项 | 概率 | 应对策略 |
|--------|------|---------|
| 服务器到期 / 数据丢失 | 中 | **每周打包 `model/` 和 `result_data/` 下载到本地** |
| 基线复现困难 | 高 | 优先跑通 2-3 个核心基线，其余引用论文数值 |
| 预训练不收敛 | 低 | 已验证：RTX 4090D 24GB 可跑，loss 0.75 收敛 |
| 1% 标签采样不稳定 | 中 | 每个实验跑 **3 个随机种子** 取平均 |
| **Web 端口无法暴露** | **中** | **备选方案：本地录屏演示，提交 MP4 作为补充材料** |
| **Flask 并发阻塞** | **低** | **使用 gunicorn 多 worker 或异步线程池处理推理** |

---

## 九、立即执行任务清单

### 本周（W5-W6）目标：完成全部 9 个数据集的 S2 推理并汇总指标，Web Demo 雏形可用

**1. 服务器批量运行**（AutoDL，RTX 4090D，24GB）

```bash
cd ~/sfm-ma

# DLPFC × 12 切片
for sec in 151507 151508 151509 151510 151669 151670 151671 \
           151672 151673 151674 151675 151676; do
    python run_adapter.py --data_name DLPFC --section $sec \
        --data_type labeled --adapter ce --label_rate 0.01
done

# 单一数据集
python run_adapter.py --data_name Mouse_anterior_brain --data_type labeled --adapter ce --label_rate 0.01
python run_adapter.py --data_name Breast_Cancer --data_type labeled --adapter ce --label_rate 0.01
python run_adapter.py --data_name STARmap --data_type labeled --adapter ce --label_rate 0.01
python run_adapter.py --data_name human_lung_cancer --data_type unlabeled
python run_adapter.py --data_name human_ovarian_cancer --data_type unlabeled
python run_adapter.py --data_name MERFISH --data_type only_gene --adapter ce --label_rate 0.01

# MERFISH_frontal_cortex × 9 切片
for idx in 4_0 4_1 4_2 6_0 6_1 6_2 8_0 8_1 8_2; do
    python run_adapter.py --data_name MERFISH_frontal_cortex --idx $idx \
        --data_type only_gene --adapter ce --label_rate 0.01
done

python run_adapter.py --data_name osmFISH --data_type only_gene --adapter ce --label_rate 0.01
```

**2. Web Demo 启动命令**

```bash
cd ~/sfm-ma/web
pip install flask flask-cors gunicorn -q
python app.py   # 访问 http://服务器IP:5000
```

**3. 指标汇总脚本（由成员C开发）**

```python
# collect_results.py
import pandas as pd
from pathlib import Path
import scanpy as sc

records = []
for path in Path('result_data').rglob('res_data.h5ad'):
    adata = sc.read_h5ad(path)
    records.append({
        'dataset': path.parent.name,
        'slice': path.parent.parent.name if len(path.parts) > 2 else '-',
        'ari': adata.uns.get('ari', None),
        'nmi': adata.uns.get('nmi', None),
        'sc': adata.uns.get('sc', None),
        'db': adata.uns.get('db', None),
    })

df = pd.DataFrame(records)
df.to_csv('metrics_summary.csv', index=False)
print(df)
```

**4. 基线回访**

建议在服务器或本地复现至少 2 个基线（如 STAGATE / GraphST）进行对比。

---

## 十、附录：核心参数速查

| 参数 | 值 | 说明 |
|------|----|------|
| `in_dim` | 100 | PCA 后特征维度 |
| `hid_dim` | 256 | GCN 隐藏层维度 |
| `out_dim` | 128 | 嵌入维度 |
| `num_layers` | 2 | GCN 编码器层数 |
| `num_heads` | 4 | Transformer 注意力头数 |
| `dropout` | 0.1 | Dropout 率 |
| `pca_dim` | 100 | 统一的 PCA 降维目标 |
| `radius` | 150 | 空间邻接图半径 |
| `epochs` | 500 (早停30) | 预训练最大轮数 |
| `lr` | 1e-3 | Adam 学习率 |
| `adapter_epochs` | 250 (CE) / 500 (KL) | 适配器训练轮数 |
| `label_rate` | 0.01 | 采样标注比例 |
| `web_port` | 5000 | Flask 服务端口 |

---

**报告撰写日期**：2026-07-04  
**项目基础**：SFM-MA 空间转录组多模态预训练框架  
**当前进度**：S1 预训练完成（AutoDL RTX 4090D，loss 0.75 收敛），S2 适配器开发完成，9 数据集批量脚本就绪  
**下一步**：Web Demo 开发（Flask 后端 + ECharts 前端）
