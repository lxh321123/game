"""
批量执行全部 9 个数据集的 S2 推理。
顺序执行，避免 GPU 显存冲突。
"""
import subprocess
import sys
from pathlib import Path
from datetime import datetime
import json

# 数据集配置列表
DATASET_CONFIGS = [
    # DLPFC: 12 个切片，labeled
    *[
        {"name": "DLPFC", "section": sec, "data_type": "labeled", "adapter": "ce", "label_rate": 0.01}
        for sec in ["151507", "151508", "151509", "151510",
                    "151669", "151670", "151671", "151672",
                    "151673", "151674", "151675", "151676"]
    ],
    # labeled 单数据集
    {"name": "Mouse_anterior_brain", "data_type": "labeled", "adapter": "ce", "label_rate": 0.01},
    {"name": "Breast_Cancer", "data_type": "labeled", "adapter": "ce", "label_rate": 0.01},
    {"name": "STARmap", "data_type": "labeled", "adapter": "ce", "label_rate": 0.01},
    # unlabeled
    {"name": "human_lung_cancer", "data_type": "unlabeled"},
    {"name": "human_ovarian_cancer", "data_type": "unlabeled"},
    # only_gene
    {"name": "MERFISH", "data_type": "only_gene", "adapter": "ce", "label_rate": 0.01},
    *[
        {"name": "MERFISH_frontal_cortex", "idx": idx, "data_type": "only_gene", "adapter": "ce", "label_rate": 0.01}
        for idx in ["4_0", "4_1", "4_2", "6_0", "6_1", "6_2", "8_0", "8_1", "8_2"]
    ],
    {"name": "osmFISH", "data_type": "only_gene", "adapter": "ce", "label_rate": 0.01},
]


def build_command(cfg: dict) -> list:
    """根据配置构建 run_adapter.py 的命令行参数."""
    cmd = [
        sys.executable, "run_adapter.py",
        "--data_name", cfg["name"],
        "--data_type", cfg.get("data_type", "labeled"),
    ]
    if cfg.get("section"):
        cmd += ["--section", cfg["section"]]
    if cfg.get("idx"):
        cmd += ["--idx", cfg["idx"]]
    if cfg.get("adapter"):
        cmd += ["--adapter", cfg["adapter"]]
    if cfg.get("label_rate") is not None:
        cmd += ["--label_rate", str(cfg["label_rate"])]
    return cmd


def main():
    root = Path(__file__).parent.resolve()
    total = len(DATASET_CONFIGS)
    results = []

    print(f"批量推理启动，共 {total} 个任务，预计耗时 {total * 2}–{total * 5} 分钟")
    print("=" * 60)

    for i, cfg in enumerate(DATASET_CONFIGS, 1):
        name = cfg["name"]
        slice_id = cfg.get("section") or cfg.get("idx") or "default"
        print(f"\n[{i}/{total}] {name} / {slice_id} — {datetime.now().strftime('%H:%M:%S')}")

        cmd = build_command(cfg)
        start = datetime.now()

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=600,  # 单个任务最多 10 分钟
            )
            elapsed = (datetime.now() - start).total_seconds()

            if proc.returncode == 0:
                print(f"  ✅ 成功 | 耗时 {elapsed:.1f}s")
                results.append({"dataset": name, "slice": slice_id, "status": "OK", "time_s": round(elapsed, 1)})
            else:
                print(f"  ❌ 失败 | exit={proc.returncode}")
                print(f"  stderr: {proc.stderr[:300]}")
                results.append({"dataset": name, "slice": slice_id, "status": f"FAIL({proc.returncode})", "time_s": round(elapsed, 1)})

        except subprocess.TimeoutExpired:
            print(f"  ⏱️ 超时 (>10min)")
            results.append({"dataset": name, "slice": slice_id, "status": "TIMEOUT", "time_s": 600})
        except Exception as e:
            print(f"  💥 异常: {e}")
            results.append({"dataset": name, "slice": slice_id, "status": f"ERR: {e}", "time_s": 0})

    # 保存批量执行摘要
    summary_path = root / "batch_run_summary.json"
    summary_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print("\n" + "=" * 60)
    print(f"批量执行完成。摘要保存至: {summary_path}")
    ok = sum(1 for r in results if r["status"] == "OK")
    print(f"总计: {len(results)} | 成功: {ok} | 失败: {len(results) - ok}")


if __name__ == "__main__":
    main()
