"""
汇总 result_data/ 下所有推理结果，提取 ARI/NMI/SC/DB 指标到 CSV。
"""
import json
import pandas as pd
import scanpy as sc
from pathlib import Path


def extract_metrics(path: Path) -> dict:
    """从单个 h5ad 提取指标."""
    try:
        adata = sc.read_h5ad(str(path))
        return {
            'ari': float(adata.uns.get('ari', float('nan'))),
            'nmi': float(adata.uns.get('nmi', float('nan'))),
            'sc': float(adata.uns.get('sc', float('nan'))),
            'db': float(adata.uns.get('db', float('nan'))),
        }
    except Exception:
        return {'ari': float('nan'), 'nmi': float('nan'), 'sc': float('nan'), 'db': float('nan')}


def collect():
    root = Path('result_data')
    if not root.exists():
        print('result_data/ 目录不存在')
        return None

    rows = []
    for path in sorted(root.rglob('res_data.h5ad')):
        rel = path.relative_to(root)
        parts = rel.parts  # (dataset, [optional_slice], 'res_data.h5ad')

        dataset = parts[0]
        slice_id = parts[1] if len(parts) > 2 else ''

        metrics = extract_metrics(path)
        rows.append({
            'dataset': dataset,
            'slice': slice_id,
            **metrics,
        })

    df = pd.DataFrame(rows)
    return df


def aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """按 dataset 聚合（均值 ± 标准差），适用于多切片数据集."""
    numeric_cols = ['ari', 'nmi', 'sc', 'db']
    agg = []

    for name, group in df.groupby('dataset'):
        row = {'dataset': name}
        for col in numeric_cols:
            vals = group[col].dropna()
            if vals.empty:
                row[f'{col}_mean'] = float('nan')
                row[f'{col}_std'] = float('nan')
            else:
                row[f'{col}_mean'] = round(vals.mean(), 4)
                row[f'{col}_std'] = round(vals.std(), 4) if len(vals) > 1 else 0.0
        agg.append(row)

    return pd.DataFrame(agg)


def main():
    df = collect()
    if df is None or df.empty:
        print('未找到任何结果文件')
        return

    # 原始明细
    df.to_csv('metrics_detail.csv', index=False, encoding='utf-8-sig')
    print(f'明细已保存: metrics_detail.csv ({len(df)} 条)')

    # 按数据集聚合
    agg = aggregate(df)
    agg.to_csv('metrics_summary.csv', index=False, encoding='utf-8-sig')
    print(f'摘要已保存: metrics_summary.csv ({len(agg)} 个数据集)')

    # 终端打印
    print('\n' + '=' * 60)
    print('汇总结果 (mean ± std):')
    print('=' * 60)
    print(agg.to_string(index=False))
    print('=' * 60)

    # 同时输出 JSON 便于前端读取
    agg.to_json('metrics_summary.json', orient='records', force_ascii=False, indent=2)
    print('JSON 已保存: metrics_summary.json')


if __name__ == '__main__':
    main()
