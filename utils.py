import logging
import scanpy as sc
import torch
from scipy import sparse
from sklearn.neighbors import kneighbors_graph, radius_neighbors_graph
import pandas as pd
import numpy as np
import random
import torch.nn as nn
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score, homogeneity_score, silhouette_score, davies_bouldin_score
from sklearn.decomposition import PCA
import torch.nn.functional as F
import matplotlib.pyplot as plt
from sklearn.preprocessing import LabelEncoder
import scipy.sparse as sp
import os
import json

def set_random_seed(seed, deterministic=True):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.enabled = True
    # torch.backends.cudnn.deterministic = True
    if deterministic:
        torch.backends.cudnn.benchmark = False
        os.environ['PYTHONHASHSEED'] = str(seed)
        os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
        torch.use_deterministic_algorithms(True)

def protein_norm(x):
    s = np.sum(np.log1p(x[x > 0]))
    exp = np.exp(s / len(x))
    return np.log1p(x / exp)

def construct_spatial_graph(adata, radius=150, knear=3, method="radius"):
    coor = pd.DataFrame(adata.obsm['spatial'], index=adata.obs.index, columns=['raw', 'col'])

    if method == "radius":
        interaction = radius_neighbors_graph(coor, radius=radius, mode='connectivity', include_self=True)
        interaction = interaction.toarray()
        adata.obsm['graph_neigh_spa'] = interaction

        adj_spa = interaction + interaction.T
        adj_spa = np.where(adj_spa > 1, 1, adj_spa)
        adata.obsm['adj_spa'] = adj_spa
        adata.obsm['adj_spa_norm'] = preprocess_adj(adj_spa)


        inv_degree_diag = get_inv_degree_diag(adj_spa)
        adata.obsm['inv_degree_diag'] = inv_degree_diag

        logging.info(f"半径方法构造图的平均邻居数：{adj_spa.sum(axis=1).mean():.2f}")

    elif method == "knn":
        interaction = kneighbors_graph(coor, n_neighbors=knear + 1, mode="connectivity", include_self=False)
        interaction = interaction.toarray()

        adata.obsm['graph_neigh_knn'] = interaction
        adj_knn = interaction + interaction.T
        adj_knn = np.where(adj_knn > 1, 1, adj_knn)
        logging.info(f"knn方法构造的图的平均邻居数：{adj_knn.sum(axis=1).mean():.2f}")
        adata.obsm['adj_knn'] = adj_knn
        adata.obsm['adj_knn_norm'] = preprocess_adj(adj_knn)

    else:
        raise ValueError("method must be either 'radius' or 'knn'")

def construct_spatial_graph_for_Slideseqv2(adata, radius=150, knear=3, method="radius"):
    coor = adata.obsm['spatial']

    if method == "radius":
        adj = radius_neighbors_graph(coor, radius=radius, mode='connectivity', include_self=True)
    elif method == "knn":
        adj = kneighbors_graph(coor, n_neighbors=knear, mode='connectivity', include_self=True)
    else:
        raise ValueError("method must be 'radius' or 'knn'")

    adj = adj + adj.T
    adj.data = np.where(adj.data > 0, 1, 0)
    adj.eliminate_zeros()

    edge_index = torch.tensor(np.vstack(adj.nonzero()), dtype=torch.long)
    adata.uns['edge_index'] = edge_index

    degrees = np.array(adj.sum(axis=1)).flatten()
    print(f"平均邻居数：{degrees.mean():.2f}")

def preprocess_adj(adj):
    """Preprocessing of adjacency matrix for simple GCN model and conversion to tuple representation."""
    adj_normalized = normalize_adj(adj)+np.eye(adj.shape[0])
    return adj_normalized

def normalize_adj(adj):
    """Symmetrically normalize adjacency matrix."""
    adj = sp.coo_matrix(adj)
    rowsum = np.array(adj.sum(1))
    d_inv_sqrt = np.power(rowsum, -0.5).flatten()
    d_inv_sqrt[np.isinf(d_inv_sqrt)] = 0.
    d_mat_inv_sqrt = sp.diags(d_inv_sqrt)
    adj = adj.dot(d_mat_inv_sqrt).transpose().dot(d_mat_inv_sqrt)
    return adj.toarray()

def search_res(adata, n_clusters, method='leiden', use_rep='emb', start=0.01, end=1.5, increment=0.01):
    '''\
    Searching corresponding resolution according to given cluster number

    Parameters
    ----------
    adata : anndata
        AnnData object of spatial data.
    n_clusters : int
        Targetting number of clusters.
    method : string
        Tool for clustering. Supported tools include 'leiden' and 'louvain'. The default is 'leiden'.
    use_rep : string
        The indicated representation for clustering.
    start : float
        The start value for searching.
    end : float
        The end value for searching.
    increment : float
        The step size to increase.

    Returns
    -------
    res : float
        Resolution.

    '''
    print('Searching resolution...')
    label = 0
    sc.pp.neighbors(adata, n_neighbors=50, use_rep=use_rep)
    for res in sorted(list(np.arange(start, end, increment)), reverse=True):
        if method == 'leiden':
            sc.tl.leiden(adata, random_state=0, resolution=res)
            count_unique = len(pd.DataFrame(adata.obs['leiden']).leiden.unique())
            print('resolution={}, cluster number={}'.format(res, count_unique))
        elif method == 'louvain':
            sc.tl.louvain(adata, random_state=0, resolution=res)
            count_unique = len(pd.DataFrame(adata.obs['louvain']).louvain.unique())
            print('resolution={}, cluster number={}'.format(res, count_unique))
        if count_unique == n_clusters:
            label = 1
            break

    assert label == 1, "Resolution is not found. Please try bigger range or smaller step!."

    return res

def get_inv_degree_diag(adj):
    degree = np.sum(adj, axis=1)
    inv_degree = np.power(degree, -1)
    inv_degree[np.isinf(inv_degree)] = 0
    inv_degree_diag = np.diag(inv_degree)
    return inv_degree_diag

def target_distribution(output):
    p = output ** 2 / output.sum(dim=0)
    p = p / p.sum(dim=1, keepdims=True)
    return p

def _nan2zero(x):
    return torch.where(torch.isnan(x), torch.zeros_like(x), x)

def clustering(adata, num_class, refinement, use_obsm='emb', method='kmeans'):
    if method != 'leiden':
        true_label = adata.obs['true_label']

    # 对embedding进行PCA降维
    emb_pca = PCA(n_components=20, random_state=0).fit_transform(adata.obsm[use_obsm])
    adata.obsm[use_obsm] = emb_pca

    if method == 'kmeans':
        # kmeans聚类方法
        kmeans = KMeans(n_clusters=num_class, random_state=0, n_init=10)
        pred_label = kmeans.fit_predict(adata.obsm[use_obsm])
        adata.obs['kmeans'] = pred_label
        if refinement:
            pred_label = refine_label(adata, key='kmeans')
            adata.obs[method] = pred_label

        ari = adjusted_rand_score(true_label, pred_label)
        nmi = normalized_mutual_info_score(true_label, pred_label)
        hs = homogeneity_score(true_label, pred_label)
        logging.info("预训练结果：")
        logging.info(f'ARI: {ari:.2f}, NMI: {nmi:.2f}, HS: {hs:.2f}')

    elif method == 'mclust':
        # mclust聚类方法：
        adata = mclust_R(adata, num_class, modelNames='EEE', used_obsm=use_obsm, random_seed=0)
        pred_label = adata.obs['mclust']
        if refinement:
            pred_label = refine_label(adata, key='mclust')
            adata.obs[method] = pred_label
        ari = np.round(adjusted_rand_score(true_label, pred_label), 2)
        nmi = np.round(normalized_mutual_info_score(true_label, pred_label), 2)
        hs = np.round(homogeneity_score(true_label, pred_label), 2)
        logging.info("预训练结果：")
        logging.info(f"ARI: {ari}, NMI: {nmi}, HS: {hs}")

    elif method == 'leiden':
        sc.pp.neighbors(adata, use_rep='emb')
        sc.tl.umap(adata, random_state=0)
        res = search_res(adata, n_clusters=num_class, end=1.2)
        sc.tl.leiden(adata, resolution=res)
        if refinement:
            new_type = refine_label(adata, key='domain', radius=25)
            adata.obs[method] = new_type

        SC = silhouette_score(adata.obsm['emb'], adata.obs[method])
        DB = davies_bouldin_score(adata.obsm['emb'], adata.obs[method])
        adata.uns['sc'] = SC
        adata.uns['db'] = DB
        logging.info("预训练结果：")
        print(f"SC:{SC:.2f}, DB:{DB:.2f}")

    return ari if method != 'leiden' else None

from scipy.spatial.distance import cdist
class ContrastiveLoss(nn.Module):
    def __init__(self, alpha=0.1, gamma=2, beta=0.1, tau=0.1, device=None):
        super(ContrastiveLoss, self).__init__()
        self.tau = tau
        self.alpha = alpha
        self.gamma = gamma
        self.beta = beta
        self.device = device


    def forward(self, x, labels, locs):
        x_norm = F.normalize(x, dim=1, p=2)
        sim = x_norm @ x_norm.T

        self.tau = torch.as_tensor(self.tau, device=self.device)
        score = torch.exp(sim / self.tau)

        locs_diff = locs[:, None, :] - locs[None, :, :]
        dis = torch.norm(locs_diff, dim=-1)

        w_p = torch.exp(-dis ** 2 / (2 * (self.alpha ** 2)))

        w_n = torch.exp(2 + sim)

        same_labels = (labels[: None] == labels[None, :]).int()

        pos_score = torch.sum(w_p * same_labels * score, dim=1)
        neg_score = torch.sum(w_n * (1 - same_labels) * score, dim=1)

        p = pos_score / (pos_score + neg_score)
        loss = -torch.mean(torch.log(p))

        return loss

def refine_label(adata, radius=50, key='label'):
    n_neigh = radius
    new_type = []
    old_type = adata.obs[key].values
    position = adata.obsm['spatial']
    distance = ot.dist(position, position, metric='euclidean')

    n_cell = distance.shape[0]

    for i in range(n_cell):
        vec = distance[i, :]
        index = vec.argsort()
        neigh_type = []
        for j in range(1, n_neigh + 1):
            neigh_type.append(old_type[index[j]])
        max_type = max(neigh_type, key=neigh_type.count)
        new_type.append(max_type)

    new_type = [str(i) for i in list(new_type)]

    return new_type

def mclust_R(adata, num_cluster, modelNames='EEE', used_obsm='emb_pca', random_seed=2020):
    import rpy2.robjects as robjects
    from rpy2.robjects import numpy2ri
    numpy2ri.activate()
    robjects.r.library("mclust")

    np.random.seed(random_seed)
    robjects.r['set.seed'](random_seed)
    rmclust = robjects.r['Mclust']

    data = adata.obsm[used_obsm]
    r_data = numpy2ri.numpy2rpy(data)
    robjects.r.assign("r_data", r_data)
    robjects.r("""
        dimnames(r_data) <- list(NULL, paste("V", 1:ncol(r_data), sep=""))
    """)
    res = rmclust(robjects.r["r_data"], G=num_cluster, modelNames=modelNames)
    mclust_res = np.array(res[-2])

    adata.obs['mclust'] = mclust_res.astype('int')
    adata.obs['mclust'] = adata.obs['mclust'].astype('category')
    return adata


def draw_scatter(adata):
    label_encoder = LabelEncoder()
    true_label = label_encoder.fit_transform(adata.obs['ground_truth'])
    adata.obs['kmeans'] = label_encoder.inverse_transform(adata.obs['kmeans'])

    sc.pl.spatial(adata, img_key='hires', color=['ground_truth', 'kmeans'], size=1.5, show=False)
    path = "./image/scatter.png"
    plt.savefig(path, format='png', dpi=300)
    plt.close()

class SCELoss(nn.Module):
    def __init__(self, alpha=3):
        super().__init__()
        self.alpha = alpha

    def forward(self, data, data_rec):
        data_norm = F.normalize(data, dim=1, p=2)
        data_rec_norm = F.normalize(data_rec, dim=1, p=2)
        loss = (1.0 - (data_norm * data_rec_norm).sum(dim=1)).pow(self.alpha)
        loss = loss.mean()
        return loss

class MSLoss(nn.Module):
    def __init__(self):
        super().__init__()

    def forward(self, emb_exp, emb_img):
        emb_exp_norm = F.normalize(emb_exp, dim=1, p=2)
        emb_img_norm = F.normalize(emb_img, dim=1, p=2)

        sim_exp = emb_exp_norm @ emb_exp_norm.T
        sim_img = emb_img_norm @ emb_img_norm.T

        loss = F.mse_loss(sim_exp, sim_img)

        return loss

# 温度
class InfoNCE(nn.Module):
    def __init__(self, temperature=0.5):
        super().__init__()
        self.temperature = temperature

    def forward(self, emb1, emb2):
        num_nodes = emb1.size(0)
        emb1_norm = F.normalize(emb1, dim=1, p=2)
        emb2_norm = F.normalize(emb2, dim=1, p=2)
        similarity = (emb1_norm @ emb2_norm.T) / self.temperature
        label = torch.arange(num_nodes).to(emb1.device)
        loss = F.cross_entropy(similarity, label)
        return loss

def permutation(x):
    perm = torch.randperm(x.size(0))
    return x[perm]


def nt_xent(z1, z2, temp=0.2, eps=1e-8):

    z1 = F.normalize(z1 + eps, dim=-1)
    z2 = F.normalize(z2 + eps, dim=-1)

    sim = torch.matmul(z1, z2.T) / temp     # B × B
    sim_row_max = sim.max(dim=1, keepdim=True)[0]
    lse = torch.logsumexp(sim - sim_row_max, dim=1) + sim_row_max.squeeze(1)
    pos = torch.sum(z1 * z2, dim=1) / temp

    return -(pos - lse).mean()

def sample_nodes_by_class(labels, sample_ratio=0.05):
    unique_labels = torch.unique(labels)
    sampled_indices = []
    for label in unique_labels:
        class_indices = torch.nonzero(labels == label).squeeze(1)

        num_samples = int(len(class_indices) * sample_ratio)
        num_samples = max(num_samples, 1)

        sampled_class_indices = class_indices[torch.randperm(len(class_indices))[:num_samples]]

        sampled_indices.append(sampled_class_indices)

    sampled_indices = torch.cat(sampled_indices)

    return sampled_indices

def sample_nodes_by_class2(labels, sample_ratio=0.05):
    unique_labels = torch.unique(labels)
    sampled_indices_by_class = {}
    num_sample_nodes = 0
    sampled_indices_ = []
    for label in unique_labels:
        class_indices = torch.nonzero(labels == label).squeeze(1)
        num_samples = int(len(class_indices) * sample_ratio)
        num_samples = max(num_samples, 1)

        sampled_class_indices = class_indices[torch.randperm(len(class_indices))[:num_samples]]
        sampled_indices_.append(sampled_class_indices)

        sampled_indices_by_class[label.item()] = sampled_class_indices
        num_sample_nodes += len(sampled_class_indices)

    sampled_indices_ = torch.cat(sampled_indices_)
    return sampled_indices_by_class, num_sample_nodes, sampled_indices_

def cluster_loss(emb, y_train, device):
    # 调试信息
    loss = torch.tensor(0.0, dtype=torch.float, device=device)
    y_train = y_train.to(device)
    unique_labels = torch.unique(y_train)
    for label in unique_labels:
        mask = (y_train == label)
        label_indices = mask.nonzero(as_tuple=True)[0]
        if len(label_indices) < 2:
            print(f"Warning: Label {label} has less than 2 samples, skipping")
            continue

        emb_label = emb[label_indices]
        emb_label_norm = F.normalize(emb_label, dim=1, p=2)
        sim_label = torch.mm(emb_label_norm, emb_label_norm.t())
        sim_label = torch.clamp((sim_label + 1) / 2, min=1e-8, max=1 - 1e-8)  # 防止数值问题
        label_matrix = torch.ones_like(sim_label, device=device)
        label_matrix.fill_diagonal_(0)
        loss_label = F.binary_cross_entropy(sim_label, label_matrix)
        loss += loss_label
    if len(unique_labels) == 0:
        return torch.tensor(0.0, device=device)
    return loss / len(unique_labels)


def KL_prompt_loss(q_ic, labeled_nodes, true_labels, alpha=0.99, num_class=None):
    p_ic_traditional = target_distribution(q_ic)
    p_ic_prompt = torch.zeros_like(q_ic)
    for i, (node_idx, true_label) in enumerate(zip(labeled_nodes, true_labels)):
        p_ic_prompt[node_idx] = F.one_hot(true_label, num_classes=num_class)

    p_ic_final = alpha * p_ic_prompt + (1 - alpha) * p_ic_traditional
    kl_loss = F.kl_div(torch.log(q_ic + 1e-8), p_ic_final, reduction='batchmean')
    return kl_loss

def read_data(data_name, section=None, data_type=None, idx="4_0"):
    # 读取数据
    if data_name == "DLPFC":
        data_path = "data/DLPFC/DLPFC/"
        adata = sc.read_h5ad(data_path + f'{section}_pre_data.h5ad')
        label_encoder = LabelEncoder()
        true_label = label_encoder.fit_transform(adata.obs['ground_truth'])
        adata.obs['true_label'] = true_label
        num_class = pd.get_dummies(adata.obs['ground_truth']).shape[1]
        logging.info(f'聚类数：{num_class}')
        logging.info(adata)

    elif data_name == "Breast_Cancer":
        adata = sc.read_h5ad("data/Breast_Cancer/Breast_Cancer.h5ad")
        num_class = adata.obs['true_label'].max() + 1
        logging.info(f'聚类数：{num_class}')

    elif data_name == "Mouse_anterior_brain":
        adata = sc.read_h5ad("data/Mouse_anterior_brain/Mouse_anterior_brain.h5ad")
        label_encoder = LabelEncoder()
        true_label = label_encoder.fit_transform(adata.obs['ground_truth'])
        adata.obs['true_label'] = true_label
        num_class = pd.get_dummies(adata.obs['ground_truth']).shape[1]
        logging.info(f'聚类数：{num_class}')

    elif data_name == "STARmap":
        adata = sc.read_h5ad("data/STARmap/STARmap.h5ad")
        adata.obs['true_label'] = adata.obs['tissue_id_refine']
        num_class = pd.get_dummies(adata.obs['tissue_id_refine']).shape[1]
        logging.info(f"聚类数：{num_class}")

    elif data_name == "human_lung_cancer":
        adata = sc.read_h5ad("data/human_lung_cancer/human_lung_cancer.h5ad")
        num_class = 10

    elif data_name == "human_ovarian_cancer":
        adata = sc.read_h5ad("data/human_ovarian_cancer/human_ovarian_cancer.h5ad")
        num_class = 7

    from opt import args
    if data_type == "only_gene" or args.melt_data_type == "only_gene":
        if data_name == "MERFISH_frontal_cortex":
            adata = sc.read_h5ad(f"data/MERFISH_frontal_cortex/Donor_{idx}_data_pre.h5ad")
        else:
            adata = sc.read_h5ad(f"data/{data_name}/{data_name}.h5ad")

        if data_name == "osmFISH":
            adata.obs['true_label'] = LabelEncoder().fit_transform(adata.obs['Region'])
            num_class = pd.get_dummies(adata.obs['Region']).shape[1]
        elif data_name == "MERFISH":
            adata.obs['true_label'] = LabelEncoder().fit_transform(adata.obs['region'])
            num_class = pd.get_dummies(adata.obs['region']).shape[1]
        elif data_name == "MERFISH_frontal_cortex":
            adata.obs['true_label'] = LabelEncoder().fit_transform(adata.obs['tissue'])
            num_class = pd.get_dummies(adata.obs['tissue']).shape[1]

    return adata, num_class

def get_radius(data_name):
    if data_name == "DLPFC":
        radius = 150
    elif data_name == "osmFISH":
        radius = 800
    elif data_name == "MERFISH":
        radius = 80
    elif data_name == "MERFISH_frontal_cortex":
        radius = 80
    elif data_name == "STARmap":
        radius = 550
    elif data_name == "human_lung_cancer":
        radius = 550
    elif data_name == "human_ovarian_cancer":
        radius = 150
    elif data_name == "Mouse_anterior_brain":
        radius = 300
    elif data_name == "Breast_Cancer":
        radius = 150
    else:
        raise ValueError(f"Unknown data_name: {data_name}")
    return radius

########### 训练通用模型专用 ############







