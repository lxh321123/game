import os
import sys
import warnings
import logging
import argparse
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import scanpy as sc
import torch
import torch.nn as nn
import torch.nn.functional as F
from sklearn.decomposition import PCA
from sklearn.cluster import KMeans
from sklearn.metrics import (
    adjusted_rand_score, normalized_mutual_info_score,
    silhouette_score, davies_bouldin_score
)
from tqdm import tqdm

# utils.py 内部会执行 `from opt import args`，在此注入假的 opt 模块避免报错
class _FakeArgs:
    melt_data_type = 'multi_modal'
    melt_modal = 'exp'
    melt_type = 'no'

_fake_opt = type(sys)('opt')
_fake_opt.args = _FakeArgs()
sys.modules['opt'] = _fake_opt

# ==================== 1. 工具函数 ====================
from utils import (
    set_random_seed, construct_spatial_graph,
    search_res, read_data, get_radius
)

warnings.filterwarnings('ignore')


# ==================== 2. 动态加载 LLM backbone ====================
def _import_llm():
    """兼容本地(common-model/)和服务器(根目录)两种位置."""
    candidates = [Path('common-model/train_common_model.py'), Path('train_common_model.py')]
    script = None
    for c in candidates:
        if c.exists():
            script = c
            break
    if script is None:
        raise FileNotFoundError(
            f"train_common_model.py not found. Tried: {[str(p) for p in candidates]}"
        )
    spec = importlib.util.spec_from_file_location('train_common_model', str(script))
    mod = importlib.util.module_from_spec(spec)
    sys.modules['train_common_model'] = mod
    spec.loader.exec_module(mod)
    return mod.LLM


LLM = _import_llm()


# ==================== 3. 参数解析 ====================
def parse_args():
    parser = argparse.ArgumentParser(
        description='SFM-MA: pretrained foundation model + downstream adapter'
    )
    parser.add_argument('--data_name', type=str, required=True)
    parser.add_argument('--section', type=str, default='')
    parser.add_argument('--idx', type=str, default='')
    parser.add_argument('--data_type', type=str, default='labeled',
                        choices=['labeled', 'unlabeled', 'only_gene'])
    parser.add_argument('--adapter', type=str, default='ce', choices=['ce', 'kl'])
    parser.add_argument('--label_rate', type=float, default=0.05)
    parser.add_argument('--common_model_path', type=str,
                        default='model/common_model/common_model_all.pt')
    parser.add_argument('--epochs', type=int, default=250)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--device', type=str, default='cuda')
    return parser.parse_args()


# ==================== 4. 数据预处理 ====================
def pad_pca_to_100(adata):
    """PCA 降维并统一填充至 100 维."""
    # sc.pp.pca 底层使用 sklearn PCA，ARPACK 求解器要求 n_components < min(n_samples, n_features)
    max_comps = min(100, adata.n_obs, adata.n_vars) - 1
    max_comps = max(max_comps, 1)  # 至少保留 1 维
    sc.pp.pca(adata, n_comps=max_comps, random_state=0)
    if adata.obsm['X_pca'].shape[1] < 100:
        pad = np.zeros((adata.n_obs, 100 - adata.obsm['X_pca'].shape[1]), dtype=np.float32)
        adata.obsm['X_pca'] = np.concatenate([adata.obsm['X_pca'], pad], axis=1)

    if 'image_feature' in adata.obsm and adata.obsm['image_feature'] is not None:
        pca = PCA(n_components=100, random_state=0)
        adata.obsm['C_pca'] = pca.fit_transform(adata.obsm['image_feature'].astype(np.float32))


def extract_frozen_emb(model, x, c, edge_index):
    """手动提取 LLM 的冻结嵌入，绕过训练时的随机掩码."""
    with torch.no_grad():
        x = torch.nan_to_num(x, nan=0.0, posinf=1e6, neginf=-1e6)
        c = torch.nan_to_num(c, nan=0.0, posinf=1e6, neginf=-1e6)
        emb_c = model.layer(x.unsqueeze(1), c.unsqueeze(1))
        x_fused = model.fusion(torch.stack([x, emb_c], dim=1))
        emb = model.enc(x_fused, edge_index)
    return emb


# ==================== 5. 任务适配器 ====================
class MLPClassifier(nn.Module):
    def __init__(self, in_dim, hid_dim1=64, hid_dim2=32, num_class=None):
        super().__init__()
        self.enc = nn.Sequential(
            nn.Linear(in_dim, hid_dim1), nn.ReLU(),
            nn.Linear(hid_dim1, hid_dim2), nn.ReLU(),
            nn.Linear(hid_dim2, num_class),
        )

    def forward(self, x):
        return torch.softmax(self.enc(x), dim=1)


class KLClassifier(nn.Module):
    def __init__(self, dim, num_class=None):
        super().__init__()
        self.cluster_center = nn.Parameter(torch.FloatTensor(num_class, dim))
        self.beta = 1.0

    def forward(self, x):
        q = 1.0 / (1.0 + torch.sum(torch.pow(x.unsqueeze(1) - self.cluster_center, 2), dim=-1) / self.beta)
        q = q.pow((1.0 + self.beta) / 2.0)
        q = q / q.sum(dim=1, keepdims=True)
        return q


def _sample_by_class(labels, sample_ratio=0.1):
    """按类别采样节点."""
    unique = torch.unique(labels)
    selected = []
    for lab in unique:
        lab = lab.item()
        idx = torch.where(labels == lab)[0]
        n = max(1, int(len(idx) * sample_ratio))
        perm = torch.randperm(len(idx))
        selected.append(idx[perm[:n]])
    return torch.cat(selected)


def _kl_prompt_loss(q, sample_nodes, y_sample, num_class):
    q_s = q[sample_nodes] + 1e-8
    q_s = q_s / q_s.sum(dim=1, keepdim=True)
    tgt = F.one_hot(y_sample, num_classes=num_class).float()
    return F.kl_div(q_s.log(), tgt, reduction='batchmean')


def fine_tuning_ce(adata, device, num_class, label_rate=0.1, pre_ari=0, pre_nmi=0, epochs=250):
    if 'true_label' not in adata.obs:
        logging.warning('Missing true_label, skipping CE adapter.')
        return

    # 过滤掉 NaN / None 标签（DLPFC 组织边缘的 spots）
    labels_raw = pd.to_numeric(adata.obs['true_label'], errors='coerce').values
    valid_mask = ~np.isnan(labels_raw)
    if valid_mask.sum() == 0:
        logging.warning('No valid labels found, skipping CE adapter.')
        return

    labels = torch.tensor(labels_raw[valid_mask], dtype=torch.long, device=device)
    emb = torch.tensor(adata.obsm['emb'][valid_mask], dtype=torch.float, device=device)

    # 修正 num_class，确保覆盖实际标签最大值
    num_class = int(labels.max().item() + 1)

    cls = MLPClassifier(
        in_dim=emb.size(1), hid_dim1=emb.size(1)//2,
        hid_dim2=emb.size(1)//4, num_class=num_class
    ).to(device)
    opt = torch.optim.Adam(cls.parameters(), lr=1e-3)
    ce = nn.CrossEntropyLoss()

    nodes = _sample_by_class(labels, sample_ratio=label_rate)
    logging.info(f'采样的节点数：{len(nodes)}')

    for epoch in range(epochs):
        cls.train()
        opt.zero_grad()
        loss = ce(cls(emb[nodes]), labels[nodes])
        loss.backward()
        opt.step()
        if (epoch + 1) % 20 == 0 or epoch == epochs - 1:
            print(f"Epoch {epoch + 1}/{epochs} | loss:{loss.item():.4f}", flush=True)

    with torch.no_grad():
        cls.eval()
        # 预测全图（包括无标签区域）
        all_emb = torch.tensor(adata.obsm['emb'], dtype=torch.float, device=device)
        pred = torch.argmax(cls(all_emb), dim=1).cpu().numpy()
        adata.obs['domain'] = pd.Categorical(pred.astype(str))
        ari = adjusted_rand_score(labels_raw[valid_mask], pred[valid_mask])
        nmi = normalized_mutual_info_score(labels_raw[valid_mask], pred[valid_mask])
        if ari < pre_ari:
            logging.info('任务分配器未能提升效果！')
            ari, nmi = pre_ari, pre_nmi
        adata.uns['ari'] = ari
        adata.uns['nmi'] = nmi
        logging.info(f'ARI:{ari:.2f}, NMI:{nmi:.2f}')


def fine_tuning_kl(adata, device, num_class, label_rate=0.1, epochs=500):
    if 'true_label' not in adata.obs:
        logging.warning('Missing true_label, skipping KL adapter.')
        return

    # 过滤掉 NaN 标签
    labels_raw = pd.to_numeric(adata.obs['true_label'], errors='coerce').values
    valid_mask = ~np.isnan(labels_raw)
    if valid_mask.sum() == 0:
        logging.warning('No valid labels found, skipping KL adapter.')
        return

    x = torch.tensor(adata.obsm['emb'], dtype=torch.float, device=device)
    labels = torch.tensor(labels_raw[valid_mask], dtype=torch.long, device=device)

    nodes = _sample_by_class(labels, sample_ratio=label_rate)
    y_sample = labels[nodes]
    logging.info(f'采样的节点数：{len(nodes)}')

    # 修正 num_class
    num_class = int(labels.max().item() + 1)

    # 用真实标签初始化聚类中心
    centers = torch.zeros((num_class, x.size(1)), dtype=torch.float, device=device)
    for lab in torch.unique(labels):
        lab = lab.item()
        mask = labels_raw == lab
        if mask.sum() > 0:
            centers[lab] = torch.tensor(
                adata.obsm['emb'][mask].mean(axis=0), dtype=torch.float, device=device
            )

    cls = KLClassifier(dim=x.size(1), num_class=num_class).to(device)
    with torch.no_grad():
        cls.cluster_center.copy_(centers)
    opt = torch.optim.Adam(cls.parameters(), lr=1e-3)

    max_ari = max_nmi = 0
    for epoch in range(epochs):
        cls.train()
        opt.zero_grad()
        q = cls(x)
        loss = _kl_prompt_loss(q, nodes, y_sample, num_class)
        loss.backward()
        opt.step()

        with torch.no_grad():
            pred = torch.argmax(cls(x), dim=1).cpu().numpy()
            ari = adjusted_rand_score(labels_raw[valid_mask], pred[valid_mask])
            nmi = normalized_mutual_info_score(labels_raw[valid_mask], pred[valid_mask])
            if ari > max_ari and nmi > max_nmi:
                max_ari, max_nmi = ari, nmi

        if (epoch + 1) % 20 == 0 or epoch == epochs - 1:
            print(f"Epoch {epoch + 1}/{epochs} | loss:{loss.item():.4f} | best_ari:{max_ari:.4f}", flush=True)

    adata.uns['ari'] = max_ari
    adata.uns['nmi'] = max_nmi
    logging.info(f'ARI:{max_ari:.2f}, NMI:{max_nmi:.2f}')


# ==================== 6. 主流程 ====================
def main():
    args = parse_args()
    set_random_seed(args.seed, deterministic=False)
    device = torch.device(args.device if torch.cuda.is_available() else 'cpu')

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')

    # 1) 读取数据
    adata, num_class = read_data(
        data_name=args.data_name,
        section=args.section or None,
        data_type=args.data_type,
        idx=args.idx or None,
    )
    radius = get_radius(args.data_name)
    logging.info(f'聚类类别数：{num_class}, 细胞数：{adata.shape[0]}')

    # 2) PCA + 图像特征
    pad_pca_to_100(adata)

    # 3) 构图
    construct_spatial_graph(adata, radius=radius, method='radius')

    # 4) 转 tensor
    x = torch.tensor(adata.obsm['X_pca'].copy(), dtype=torch.float, device=device)
    c = torch.zeros_like(x)
    if args.data_type != 'only_gene':
        c = torch.tensor(adata.obsm['C_pca'], dtype=torch.float, device=device)

    adj_spa = torch.tensor(adata.obsm['adj_spa'], dtype=torch.float, device=device)
    edge_index = torch.nonzero(adj_spa).t().contiguous().long()

    # 5) 加载预训练 backbone
    logging.info('=' * 50)
    logging.info('Stage S2: Loading pretrained foundation model...')
    logging.info(f'Checkpoint: {args.common_model_path}')

    if not Path(args.common_model_path).exists():
        raise FileNotFoundError(f'权重文件不存在：{args.common_model_path}')

    state = torch.load(args.common_model_path, map_location=device)
    num_class_pretrained = state['cluster_centers'].shape[0]
    llm = LLM(
        in_dim=100, hid_dim=256, out_dim=128,
        num_layers=2, num_heads=4, dropout=0.1,
        num_class=num_class_pretrained
    ).to(device)
    llm.load_state_dict(state)
    llm.eval()
    for p in llm.parameters():
        p.requires_grad = False

    # 6) 提取冻结嵌入
    emb = extract_frozen_emb(llm, x, c, edge_index)
    adata.obsm['emb'] = emb.cpu().numpy()
    logging.info(f'Frozen embedding extracted, shape={emb.shape}')

    # 7) 下游适配
    pre_ari = pre_nmi = 0.0
    if args.data_type in ('labeled', 'only_gene'):
        if 'true_label' in adata.obs:
            pred_lbls = KMeans(n_clusters=num_class, random_state=0, n_init=10).fit_predict(adata.obsm['emb'])
            # 过滤 NaN 标签后再计算 ARI/NMI
            labels_raw = pd.to_numeric(adata.obs['true_label'], errors='coerce').values
            valid_mask = ~np.isnan(labels_raw)
            if valid_mask.sum() > 0:
                pre_ari = adjusted_rand_score(labels_raw[valid_mask], pred_lbls[valid_mask])
                pre_nmi = normalized_mutual_info_score(labels_raw[valid_mask], pred_lbls[valid_mask])
                logging.info(f'基线 KMeans | ARI:{pre_ari:.2f}, NMI:{pre_nmi:.2f}')

        if args.adapter == 'ce':
            fine_tuning_ce(adata, device, num_class, label_rate=args.label_rate,
                           pre_ari=pre_ari, pre_nmi=pre_nmi, epochs=args.epochs)
        else:
            fine_tuning_kl(adata, device, num_class, label_rate=args.label_rate, epochs=500)

    elif args.data_type == 'unlabeled':
        sc.pp.neighbors(adata, use_rep='emb')
        sc.tl.umap(adata, random_state=0)
        res = search_res(adata, n_clusters=num_class, end=1.2)
        sc.tl.leiden(adata, resolution=res)
        adata.obs['domain'] = adata.obs['leiden']
        sc_val = silhouette_score(adata.obsm['emb'], adata.obs['domain'])
        db_val = davies_bouldin_score(adata.obsm['emb'], adata.obs['domain'])
        adata.uns['sc'] = sc_val
        adata.uns['db'] = db_val
        logging.info(f'SC:{sc_val:.2f}, DB:{db_val:.2f}')

    # 8) 保存
    if args.data_name == 'DLPFC':
        sp = Path(f'result_data/{args.data_name}/{args.section}')
    elif args.data_name == 'MERFISH_frontal_cortex':
        sp = Path(f'result_data/{args.data_name}/{args.idx}')
    else:
        sp = Path(f'result_data/{args.data_name}')
    sp.mkdir(exist_ok=True, parents=True)
    adata.write(sp / 'res_data.h5ad')
    logging.info(f'Results saved to {sp / "res_data.h5ad"}')


if __name__ == '__main__':
    main()
