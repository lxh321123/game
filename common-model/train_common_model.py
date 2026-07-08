import os
import sys
import warnings
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp
import scanpy as sc
import torch
import torch.nn as nn
import torch.nn.functional as F
from sklearn.decomposition import PCA
from sklearn.neighbors import radius_neighbors_graph
from tqdm import tqdm

# 复用主代码中的工具函数和损失
sys.path.insert(0, str(Path(__file__).parent))
from utils import SCELoss, set_random_seed

warnings.filterwarnings('ignore')

# ==================== 1. 配置与日志 ====================
def setup_logger():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )
    return logging.getLogger(__name__)


logger = setup_logger()


# ==================== 2. 显存优化工具 ====================
def release_memory():
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()


def enable_benchmark():
    if torch.cuda.is_available():
        torch.backends.cudnn.benchmark = True
        torch.backends.cudnn.deterministic = False


enable_benchmark()


# ==================== 3. 数据预处理 ====================
def pca_reduce_dimension(adata, num_pca_dimension=100):
    """对基因表达和图像特征分别做 PCA 降维；若样本数或特征数不足则截断并零填充至 num_pca_dimension."""
    n_samples, n_features = adata.n_obs, adata.n_vars
    # PCA 的 n_components 上限受 min(样本数, 特征数) 约束
    n_comp = min(num_pca_dimension, n_samples, n_features)
    pca = PCA(n_components=n_comp, random_state=0)

    if sp.issparse(adata.X):
        x_data = adata.X.toarray().astype(np.float32)
    else:
        x_data = adata.X.astype(np.float32)
    x_data = np.nan_to_num(x_data, nan=0.0, posinf=1e6, neginf=-1e6)
    x_pca = pca.fit_transform(x_data).astype(np.float32)
    # 零填充以统一维度
    if x_pca.shape[1] < num_pca_dimension:
        pad = np.zeros((x_pca.shape[0], num_pca_dimension - x_pca.shape[1]), dtype=np.float32)
        x_pca = np.concatenate([x_pca, pad], axis=1)

    if 'image_feature' in adata.obsm and adata.obsm['image_feature'] is not None:
        c_data = adata.obsm['image_feature'].astype(np.float32)
        c_data = np.nan_to_num(c_data, nan=0.0, posinf=1e6, neginf=-1e6)
        # 图像特征维度也可能不足，重新计算可用组件数
        n_comp_c = min(num_pca_dimension, c_data.shape[0], c_data.shape[1])
        pca_c = PCA(n_components=n_comp_c, random_state=0)
        c_pca = pca_c.fit_transform(c_data).astype(np.float32)
        if c_pca.shape[1] < num_pca_dimension:
            pad = np.zeros((c_pca.shape[0], num_pca_dimension - c_pca.shape[1]), dtype=np.float32)
            c_pca = np.concatenate([c_pca, pad], axis=1)
    else:
        c_pca = np.zeros((x_pca.shape[0], num_pca_dimension), dtype=np.float32)

    return x_pca, c_pca


def build_adjacency_matrix_per_slice(coords, slice_ids, radius=150):
    """按 slice 分别构建 radius 邻接矩阵，避免跨 slice 连边."""
    unique_slices = np.unique(slice_ids)
    n_spots = len(coords)
    adj_matrix = sp.lil_matrix((n_spots, n_spots), dtype=np.int8)

    for slice_id in unique_slices:
        slice_mask = slice_ids == slice_id
        slice_indices = np.where(slice_mask)[0]
        if len(slice_indices) < 2:
            continue
        slice_coords = coords[slice_mask].astype(np.float32)
        slice_adj = radius_neighbors_graph(
            slice_coords, radius=radius, mode='connectivity',
            include_self=True, n_jobs=-1
        )
        row_idx = slice_indices[slice_adj.nonzero()[0]]
        col_idx = slice_indices[slice_adj.nonzero()[1]]
        adj_matrix[row_idx, col_idx] = 1

    return adj_matrix.tocsr()


def create_normalized_adjacency(adj_matrix, device='cuda'):
    """对称归一化邻接矩阵."""
    if not sp.isspmatrix_coo(adj_matrix):
        adj_matrix = adj_matrix.tocoo()
    degrees = np.array(adj_matrix.sum(axis=1)).flatten().astype(np.float32)
    degrees[degrees == 0] = 1.0
    norm_values = 1.0 / degrees[adj_matrix.row]

    indices = torch.tensor([adj_matrix.row, adj_matrix.col], dtype=torch.long, device=device)
    values = torch.tensor(norm_values * adj_matrix.data, dtype=torch.float32, device=device)
    return torch.sparse_coo_tensor(indices, values, size=adj_matrix.shape, device=device)


def adjacency_to_edge_index(adj_matrix, device):
    if not sp.isspmatrix_csr(adj_matrix):
        adj_matrix = adj_matrix.tocsr()
    rows, cols = adj_matrix.nonzero()
    return torch.tensor([rows, cols], dtype=torch.long, device=device)


# ==================== 4. 模型层（与主代码保持一致） ====================
class GraphConvLayer(nn.Module):
    def __init__(self, in_dim, out_dim, dropout=0.1, bias=True):
        super().__init__()
        self.linear = nn.Linear(in_dim, out_dim, bias=bias)
        self.dropout = nn.Dropout(dropout)
        self.act = nn.ReLU()

    def forward(self, x, edge_index):
        if edge_index is None or edge_index.numel() == 0:
            return self.act(self.linear(x))
        num_nodes = x.size(0)
        row, col = edge_index
        self_loop = torch.arange(num_nodes, device=x.device).unsqueeze(0).repeat(2, 1)
        row_ws = torch.cat([row, self_loop[0]])
        col_ws = torch.cat([col, self_loop[1]])

        deg = torch.bincount(row_ws, minlength=num_nodes).float()
        deg = torch.where(deg == 0, torch.ones_like(deg), deg)
        deg_inv_sqrt = deg.pow(-0.5).clamp(min=1e-8)
        edge_weight = deg_inv_sqrt[row_ws] * deg_inv_sqrt[col_ws]
        edge_weight = torch.nan_to_num(edge_weight, nan=0.0, posinf=1.0, neginf=0.0)

        x_transformed = self.linear(x)
        x_transformed = torch.nan_to_num(x_transformed, nan=0.0, posinf=1e6, neginf=-1e6)

        # 使用稀疏矩阵乘法（强制 fp32，因为 CUDA sparse.mm 不支持 fp16）
        indices = torch.stack([row_ws, col_ws])
        adj = torch.sparse_coo_tensor(indices, edge_weight.float(), (num_nodes, num_nodes))
        out = torch.sparse.mm(adj, x_transformed.float())
        if x.dtype != out.dtype:
            out = out.to(x.dtype)
        return self.act(out)


class GCNEncoder(nn.Module):
    def __init__(self, in_dim, hid_dim, out_dim, num_layers=2, dropout=0.1):
        super().__init__()
        dims = [in_dim] + [hid_dim] * (num_layers - 1) + [out_dim]
        self.layers = nn.ModuleList([GraphConvLayer(dims[i], dims[i + 1], dropout) for i in range(len(dims) - 1)])
        self.dropout = nn.Dropout(dropout)

    def forward(self, x, edge_index):
        for i, layer in enumerate(self.layers[:-1]):
            x = self.dropout(layer(x, edge_index))
        return self.layers[-1](x, edge_index)


class GCNDecoder(nn.Module):
    def __init__(self, in_dim, hid_dim, out_dim, num_layers=2, dropout=0.1):
        super().__init__()
        dims = [in_dim] + [hid_dim] * (num_layers - 1) + [out_dim]
        self.layers = nn.ModuleList([GraphConvLayer(dims[i], dims[i + 1], dropout) for i in range(len(dims) - 1)])
        self.dropout = nn.Dropout(dropout)

    def forward(self, x, edge_index):
        for i, layer in enumerate(self.layers[:-1]):
            x = self.dropout(layer(x, edge_index))
        return self.layers[-1](x, edge_index)


class AvgReadoutSparse(nn.Module):
    def forward(self, emb, adj):
        emb = torch.nan_to_num(emb, nan=0.0, posinf=1e6, neginf=-1e6)
        # 保持稀疏矩阵乘法，避免 NxN 稠密矩阵爆显存
        vsum = torch.sparse.mm(adj, emb)
        deg = torch.sparse.sum(adj, dim=1).to_dense().unsqueeze(1).clamp(min=1e-8)
        return vsum / deg


class AttentionFusion(nn.Module):
    def __init__(self, in_dim, out_dim=1, dropout=0.1):
        super().__init__()
        self.fc = nn.Linear(in_dim, in_dim // 2)
        self.act = nn.ReLU()
        self.dropout = nn.Dropout(dropout)
        self.project = nn.Linear(in_dim // 2, out_dim)

    def forward(self, x):
        h = self.dropout(self.act(self.fc(x)))
        a = torch.softmax(self.project(h), dim=1)
        return (a * x).sum(dim=1)


class Transformer(nn.Module):
    def __init__(self, in_dim, out_dim, nhead, dropout=0.1):
        super().__init__()
        self.self_attn = nn.MultiheadAttention(in_dim, nhead, dropout=dropout, batch_first=True)
        self.norm = nn.LayerNorm(in_dim)
        self.fc = nn.Linear(in_dim, out_dim)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x, c, mask=None):
        attn, _ = self.self_attn(x, c, c, attn_mask=mask)
        attn = self.norm(attn + x)
        return self.dropout(self.fc(attn)).squeeze(1)


class LLM(nn.Module):
    """通用多模态基础模型（支持图像/无图像 fallback）."""

    def __init__(self, in_dim=100, hid_dim=256, out_dim=128, num_layers=2, num_heads=4, dropout=0.1, num_class=7):
        super().__init__()
        self.graph_neigh = None
        self.mask_rate = 0.3

        self.layer = Transformer(in_dim, in_dim, nhead=num_heads, dropout=dropout)
        self.fusion = AttentionFusion(in_dim, dropout=dropout)

        self.enc = GCNEncoder(in_dim, hid_dim, out_dim, num_layers=num_layers, dropout=dropout)
        self.dec = GCNDecoder(out_dim, hid_dim, in_dim, num_layers=num_layers, dropout=dropout)

        self.avg = AvgReadoutSparse()
        self.bfc = nn.Bilinear(out_dim, out_dim, 1, bias=False)
        torch.nn.init.xavier_uniform_(self.bfc.weight.data)

        # KL 聚类正则化模块
        self.cluster_centers = nn.Parameter(torch.FloatTensor(num_class, out_dim))
        self.beta = 1.0

    def forward(self, x, c, edge_index):
        x = torch.nan_to_num(x, nan=0.0, posinf=1e6, neginf=-1e6)
        c = torch.nan_to_num(c, nan=0.0, posinf=1e6, neginf=-1e6)

        # 非对称交叉注意力：基因作为 Query，图像作为 Key/Value
        emb_c = self.layer(x.unsqueeze(1), c.unsqueeze(1))
        x = self.fusion(torch.stack([x, emb_c], dim=1))

        # 数据增强掩码
        mask_nodes, mask_edge_idx, stay_edge_idx, perm_nodes = self.make_epoch_masks(x, edge_index)
        x_a = x[perm_nodes]
        x_mask = x.clone()
        x_mask[mask_nodes] = 0.0

        emb = self.enc(x, edge_index)
        emb_m = self.enc(x_mask, stay_edge_idx)
        emb_a = self.enc(x_a, edge_index)

        if self.graph_neigh is not None:
            g = self.avg(emb, self.graph_neigh)
        else:
            g = torch.mean(emb, dim=0, keepdim=True).expand_as(emb)

        score = self.bfc(g, emb).squeeze(1)
        x_rec = self.dec(emb, edge_index)

        # 软分配 q（Student t 分布）
        q = 1.0 / (1.0 + torch.sum(torch.pow(emb.unsqueeze(1) - self.cluster_centers, 2), dim=-1) / self.beta)
        q = q.pow((1.0 + self.beta) / 2.0)
        q = q / q.sum(dim=1, keepdims=True)

        return q, emb, x_rec, emb_m, emb_a, score

    @torch.no_grad()
    def make_epoch_masks(self, x, edge_index, mask_rate=0.3):
        num_nodes = x.size(0)
        num_edges = edge_index.size(1) if edge_index is not None else 0
        perm_nodes = torch.randperm(num_nodes, device=x.device)
        num_mask_nodes = int(num_nodes * mask_rate)
        mask_nodes = perm_nodes[:num_mask_nodes]

        if num_edges > 0:
            perm_edges = torch.randperm(num_edges, device=edge_index.device)
            num_mask_edges = int(num_edges * mask_rate)
            stay_edges = perm_edges[num_mask_edges:]
            stay_edge_idx = edge_index[:, stay_edges]
        else:
            stay_edge_idx = edge_index
        return mask_nodes, None, stay_edge_idx, perm_nodes

    def pack_for_save(self):
        """训练结束后持久化 epoch 掩码缓存（未使用时可留空）."""
        pass


# ==================== 5. 多数据集联合预训练 ====================
def train_common_model(x, c, edge_index, graph_neigh, model, device, model_path, epochs=500, lr=1e-3):
    """在所有数据集拼接成的大图上做无监督预训练，包含重建+对比+邻近三重损失（不加入 KL 聚类损失）."""
    model.to(device)
    scaler = None  # 禁用 GradScaler，因 CUDA sparse.mm 不支持 fp16

    # 损失函数
    sce_loss = SCELoss().to(device)
    cos_dis = lambda a, b: 1.0 - F.cosine_similarity(a, b, dim=-1, eps=1e-8)
    tmd_loss = nn.TripletMarginWithDistanceLoss(distance_function=cos_dis, margin=1)
    bce_loss = nn.BCEWithLogitsLoss()

    optimizer = torch.optim.Adam(model.parameters(), lr=lr)

    best_loss = float('inf')
    patience = 30
    patience_counter = 0

    # 训练日志：记录各损失分量
    history = {'epoch': [], 'total': [], 'rec': [], 'ct': [], 'nc': []}

    epoch_iter = tqdm(range(epochs), desc='Pretraining')
    for epoch in epoch_iter:
        model.train()
        optimizer.zero_grad()

        _, emb, x_rec, emb_m, emb_a, score = model(x, c, edge_index)

        rec_loss = sce_loss(x_rec, x)
        ct_loss = tmd_loss(emb, emb_m, emb_a)
        nc_loss = bce_loss(score, torch.ones_like(score))

        loss = rec_loss + ct_loss + nc_loss

        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()

        # 记录历史（转为 float 避免 GPU 张量驻留内存）
        history['epoch'].append(epoch + 1)
        history['total'].append(loss.item())
        history['rec'].append(rec_loss.item())
        history['ct'].append(ct_loss.item())
        history['nc'].append(nc_loss.item())

        epoch_iter.set_description(
            f'Epoch {epoch + 1}/{epochs} | '
            f'Loss:{loss.item():.4f}(rec:{rec_loss.item():.4f},ct:{ct_loss.item():.4f},nc:{nc_loss.item():.4f})'
        )

        # 早停
        if loss.item() < best_loss:
            best_loss = loss.item()
            patience_counter = 0
            torch.save(model.state_dict(), model_path)
        else:
            patience_counter += 1
            if patience_counter >= patience:
                logger.info(f'Early stopping at epoch {epoch + 1} (best_loss={best_loss:.4f})')
                break

        if (epoch + 1) % 50 == 0:
            release_memory()

    # 加载最佳模型并提取嵌入
    model.load_state_dict(torch.load(model_path))
    model.eval()
    with torch.no_grad():
        _, emb_best, *_ = model(x, c, edge_index)

    release_memory()

    # ==================== 训练可视化 ====================
    _plot_training_curves(history, model_path)

    return model, emb_best, history


def _plot_training_curves(history, model_path):
    """绘制训练损失曲线并保存."""
    import matplotlib
    matplotlib.use('Agg')  # 无 GUI 服务器用
    import matplotlib.pyplot as plt

    save_dir = Path(model_path).parent
    save_dir.mkdir(exist_ok=True, parents=True)

    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    fig.suptitle('SFM-MA Pretraining Loss Curves', fontsize=14)

    # 总损失
    ax = axes[0, 0]
    ax.plot(history['epoch'], history['total'], 'k-', lw=1.5, label='Total')
    ax.set_title('Total Loss')
    ax.set_xlabel('Epoch')
    ax.set_ylabel('Loss')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # 重建损失
    ax = axes[0, 1]
    ax.plot(history['epoch'], history['rec'], 'C0-', lw=1.5, label='Reconstruction')
    ax.set_title('Reconstruction Loss (SCE)')
    ax.set_xlabel('Epoch')
    ax.set_ylabel('Loss')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # 对比损失
    ax = axes[1, 0]
    ax.plot(history['epoch'], history['ct'], 'C1-', lw=1.5, label='Triplet Contrast')
    ax.set_title('Contrastive Loss (Triplet)')
    ax.set_xlabel('Epoch')
    ax.set_ylabel('Loss')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # 邻近损失
    ax = axes[1, 1]
    ax.plot(history['epoch'], history['nc'], 'C2-', lw=1.5, label='Spatial Proximity')
    ax.set_title('Spatial Proximity Loss (BCE)')
    ax.set_xlabel('Epoch')
    ax.set_ylabel('Loss')
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    save_path = save_dir / 'training_curves.png'
    plt.savefig(save_path, dpi=300, bbox_inches='tight')
    plt.close(fig)
    logger.info(f'Training curves saved to {save_path}')


# ==================== 6. 主函数 ====================
def main():
    set_random_seed(0, deterministic=False)
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    data_root = Path('data')
    pca_dim = 100
    radius = 150
    epochs = 500
    lr = 1e-3

    # 全部 9 个数据集配置
    datasets = [
        {"name": "DLPFC", "type": "multi_section",
         "sections": ["151507", "151508", "151509", "151510",
                      "151669", "151670", "151671", "151672",
                      "151673", "151674", "151675", "151676"],
         "data_type": "labeled"},
        {"name": "Mouse_anterior_brain", "type": "single", "data_type": "labeled"},
        {"name": "Breast_Cancer", "type": "single", "data_type": "labeled"},
        {"name": "STARmap", "type": "single", "data_type": "labeled"},
        {"name": "human_lung_cancer", "type": "single", "data_type": "unlabeled"},
        {"name": "human_ovarian_cancer", "type": "single", "data_type": "unlabeled"},
        {"name": "MERFISH", "type": "single", "data_type": "only_gene"},
        {"name": "MERFISH_frontal_cortex", "type": "single", "data_type": "only_gene"},
        {"name": "osmFISH", "type": "single", "data_type": "only_gene"},
    ]

    # 确定聚类类别数：取 labeled 数据集中最大的类别数（或固定值）
    num_class_map = {
        "DLPFC": 7, "Mouse_anterior_brain": 52,
        "Breast_Cancer": 20, "STARmap": 7,
        "MERFISH": 10, "MERFISH_frontal_cortex": 8, "osmFISH": 12,
    }
    num_class = max(num_class_map.values())  # 统一取最大 52

    X_list, C_list, Loc_list, slice_ids = [], [], [], []
    slice_counter = 0

    logger.info("Loading datasets...")
    for cfg in datasets:
        name = cfg["name"]
        logger.info(f"  Loading {name} ...")

        if cfg["type"] == "multi_section":
            for sec in cfg["sections"]:
                path = data_root / name / name / f"{sec}_pre_data.h5ad"
                if not path.exists():
                    logger.warning(f"    {path} not found, skipping")
                    continue
                adata = sc.read_h5ad(str(path))
                x_pca, c_pca = pca_reduce_dimension(adata, pca_dim)
                loc = adata.obsm['spatial'].astype(np.float32)
                X_list.append(x_pca); C_list.append(c_pca); Loc_list.append(loc)
                slice_ids.extend([slice_counter] * len(loc))
                slice_counter += 1
                del adata
                release_memory()
        else:
            if cfg["data_type"] == "only_gene":
                if name == "MERFISH_frontal_cortex":
                    # 全部 9 个 slice 一次性加载
                    slices = ["4_0", "4_1", "4_2", "6_0", "6_1", "6_2", "8_0", "8_1", "8_2"]
                    for s in slices:
                        path = data_root / name / f"Donor_{s}_data_pre.h5ad"
                        if not path.exists():
                            logger.warning(f"    {path} not found, skipping")
                            continue
                        adata = sc.read_h5ad(str(path))
                        x_pca, c_pca = pca_reduce_dimension(adata, pca_dim)
                        loc = adata.obsm['spatial'].astype(np.float32)
                        X_list.append(x_pca); C_list.append(c_pca); Loc_list.append(loc)
                        slice_ids.extend([slice_counter] * len(loc))
                        slice_counter += 1
                        del adata
                        release_memory()
                else:
                    path = data_root / name / f"{name}.h5ad"
                    if not path.exists():
                        logger.warning(f"    {path} not found, skipping")
                        continue
                    adata = sc.read_h5ad(str(path))
                    x_pca, c_pca = pca_reduce_dimension(adata, pca_dim)
                    loc = adata.obsm['spatial'].astype(np.float32)
                    X_list.append(x_pca); C_list.append(c_pca); Loc_list.append(loc)
                    slice_ids.extend([slice_counter] * len(loc))
                    slice_counter += 1
                    del adata
                    release_memory()
            else:
                path = data_root / name / f"{name}.h5ad"
                if not path.exists():
                    logger.warning(f"    {path} not found, skipping")
                    continue
                adata = sc.read_h5ad(str(path))
                x_pca, c_pca = pca_reduce_dimension(adata, pca_dim)
                loc = adata.obsm['spatial'].astype(np.float32)
                X_list.append(x_pca); C_list.append(c_pca); Loc_list.append(loc)
                slice_ids.extend([slice_counter] * len(loc))
                slice_counter += 1
                del adata
                release_memory()

    if not X_list:
        logger.error("No datasets loaded! Exiting.")
        return

    X = np.concatenate(X_list, axis=0)
    C = np.concatenate(C_list, axis=0)
    Loc = np.concatenate(Loc_list, axis=0)
    slice_ids = np.array(slice_ids)

    logger.info(f"Corpus shape: X={X.shape}, C={C.shape}, spots={len(Loc)}, slices={slice_counter}")

    # 构建大图
    logger.info("Building spatial graph...")
    interaction = build_adjacency_matrix_per_slice(Loc, slice_ids, radius=radius)
    logger.info(f"Adjacency: {interaction.shape}, nnz={interaction.nnz}")

    x_tensor = torch.tensor(X, dtype=torch.float32, device=device)
    c_tensor = torch.tensor(C, dtype=torch.float32, device=device)
    edge_index = adjacency_to_edge_index(interaction, device)
    graph_neigh = create_normalized_adjacency(interaction, device=device)

    # 模型
    model = LLM(in_dim=pca_dim, hid_dim=256, out_dim=128,
                num_layers=2, num_heads=4, dropout=0.1, num_class=num_class).to(device)
    model.graph_neigh = graph_neigh

    model_path = Path('model/common_model')
    model_path.mkdir(exist_ok=True, parents=True)
    model_path = model_path / 'common_model_all.pt'

    logger.info("Starting multi-dataset pretraining...")
    model, emb, history = train_common_model(
        x_tensor, c_tensor, edge_index, graph_neigh,
        model, device, str(model_path), epochs=epochs, lr=lr
    )

    # 保存全语料嵌入（可选）
    np.save(model_path.parent / 'common_embedding.npy', emb.cpu().numpy())

    # 保存训练日志
    log_df = pd.DataFrame(history)
    log_df.to_csv(model_path.parent / 'training_log.csv', index=False)
    logger.info(f'Training log saved to {model_path.parent / "training_log.csv"}')

    logger.info(f"Pretraining completed. Model saved to {model_path}")


if __name__ == '__main__':
    main()
