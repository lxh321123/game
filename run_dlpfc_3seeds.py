"""
DLPFC 3-seed 统计显著性验证
运行方式: python run_dlpfc_3seeds.py
"""
import subprocess
import sys
import json
from pathlib import Path
from datetime import datetime

DLPFC_SLICES = [
    "151507", "151508", "151509", "151510",
    "151669", "151670", "151671", "151672",
    "151673", "151674", "151675", "151676",
]

SEEDS = [0, 1, 2]

def run_one(slice_id, seed):
    cmd = [
        sys.executable, "run_adapter.py",
        "--data_name", "DLPFC",
        "--section", slice_id,
        "--data_type", "labeled",
        "--adapter", "ce",
        "--label_rate", "0.05",
        "--seed", str(seed),
        "--epochs", "500",
    ]
    start = datetime.now()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    elapsed = (datetime.now() - start).total_seconds()
    return proc, elapsed

def main():
    root = Path(__file__).parent.resolve()
    results = []

    print("=" * 70)
    print("DLPFC 3-seed 实验 (5% label rate, CE adapter)")
    print(f"切片数: {len(DLPFC_SLICES)}, 种子: {SEEDS}")
    print(f"总任务数: {len(DLPFC_SLICES) * len(SEEDS)}")
    print("=" * 70)

    for seed in SEEDS:
        for slice_id in DLPFC_SLICES:
            print(f"\n[{seed=}, {slice_id=}] 开始...", end=" ", flush=True)
            try:
                proc, elapsed = run_one(slice_id, seed)
                if proc.returncode != 0:
                    print(f"❌ exit={proc.returncode}, stderr={proc.stderr[:200]}")
                    continue

                # 从 log 中提取 CE adapter ARI/NMI（跳过 KMeans 基线）
                import re
                output = proc.stdout + "\n" + proc.stderr
                ari = nmi = None
                for line in output.splitlines():
                    m = re.search(r"ARI:([\d.]+), NMI:([\d.]+)", line)
                    if m and "KMeans" not in line and "基线" not in line:
                        ari = float(m.group(1))
                        nmi = float(m.group(2))
                if ari is not None:
                    results.append({
                        "slice": slice_id, "seed": seed,
                        "ari": ari, "nmi": nmi
                    })
                    print(f"✅ ARI={ari:.4f}, NMI={nmi:.4f} ({elapsed:.1f}s)")
                else:
                    print(f"⚠️ 未找到适配器ARI行, 最后5行(stderr):")
                    for line in proc.stderr.strip().splitlines()[-5:]:
                        print(f"  |{line.strip()}")
                    print(f"  耗时 {elapsed:.1f}s")

            except subprocess.TimeoutExpired:
                print(f"⏱️ 超时")
            except Exception as e:
                print(f"💥 {e}")

    # 汇总统计
    print("\n" + "=" * 70)
    print("汇总结果")
    print("=" * 70)

    import pandas as pd
    import numpy as np
    from scipy import stats

    df = pd.DataFrame(results)

    # 按 seed × slice 统计
    pivot = df.pivot_table(index="slice", columns="seed", values="ari")
    print("\n各切片 × 各 seed 的 ARI:")
    print(pivot.to_string(float_format="%.4f"))

    # 各 seed 的 mean
    print("\n各 seed 的 DLPFC mean ARI:")
    for seed in SEEDS:
        sdf = df[df["seed"] == seed]
        print(f"  seed={seed}: mean={sdf['ari'].mean():.4f}, std={sdf['ari'].std():.4f}")

    # 总体 mean (跨越所有 seed)
    overall_mean = df["ari"].mean()
    overall_std = df["ari"].std()
    print(f"\n总体 (3 seed × 12 slice): ARI = {overall_mean:.4f} ± {overall_std:.4f}")

    # 对比论文声称的 0.70
    paper_ari = 0.70
    t_stat, p_value = stats.ttest_1samp(df["ari"], paper_ari)
    print(f"\n统计检验: H0: mean ARI = {paper_ari}")
    print(f"  t-statistic = {t_stat:.4f}")
    print(f"  p-value = {p_value:.6f}")
    if p_value < 0.05:
        print(f"  ✅ p < 0.05: 结果与论文 {paper_ari} 有显著差异")
    else:
        print(f"  ✅ p ≥ 0.05: 结果与论文 {paper_ari} 无显著差异（吻合）")

    # 保存结果
    df.to_csv(root / "dlpfc_3seed_results.csv", index=False)
    print(f"\n结果已保存: dlpfc_3seed_results.csv")

    # 输出论文用格式
    print("\n论文用数字:")
    print(f"  DLPFC mean ARI = {overall_mean:.2f} $\\pm$ {overall_std:.2f}")
    print(f"  DLPFC mean NMI = {df['nmi'].mean():.2f} $\\pm$ {df['nmi'].std():.2f}")

if __name__ == "__main__":
    main()
