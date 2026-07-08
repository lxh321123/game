"""Flask backend for SFM-MA Web Demo."""
import os
import sys
import json
import time
from pathlib import Path
from threading import Thread

from flask import Flask, request, jsonify, Response, send_from_directory

app = Flask(__name__, static_folder='static', static_url_path='')
# CORS(app)  # 本地调试不需要跨域，若部署到服务器再装 flask-cors

# 全局进度状态
progress_state = {'dataset': '', 'epoch': 0, 'loss': 0.0, 'done': True, 'log': ''}


def _run_infer_thread(data):
    """在后台线程中执行推理，并实时更新 progress_state."""
    global progress_state
    progress_state = {
        'dataset': data.get('data_name', ''),
        'epoch': 0,
        'loss': 0.0,
        'done': False,
        'log': ''
    }

    try:
        args_list = [
            sys.executable, 'run_adapter.py',
            '--data_name', data['data_name'],
            '--data_type', data.get('data_type', 'labeled'),
            '--adapter', data.get('adapter', 'ce'),
            '--label_rate', str(data.get('label_rate', 0.05)),
            '--epochs', '100',  # 演示模式：适中轮数
        ]
        if data.get('section'):
            args_list += ['--section', str(data['section'])]
        if data.get('idx'):
            args_list += ['--idx', str(data['idx'])]

        import subprocess
        import re
        workdir = Path(__file__).parent.parent.resolve()
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'

        proc = subprocess.Popen(
            args_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, cwd=str(workdir), env=env
        )

        log_lines = []
        for line in proc.stdout:
            log_lines.append(line)
            stripped = line.strip()

            # 解析 Epoch 进度
            m = re.search(r'Epoch\s+(\d+)/(\d+)\s+\|\s+loss:([\d.]+)', stripped)
            if m:
                progress_state['epoch'] = int(m.group(1))
                progress_state['loss'] = round(float(m.group(3)), 4)

            # 标记完成
            if 'Results saved to' in stripped:
                progress_state['done'] = True

            progress_state['log'] = ''.join(log_lines[-400:])

        proc.wait()
    finally:
        progress_state['done'] = True
        progress_state['log'] = ''.join(log_lines[-600:])


# ── REST API ──────────────────────────────────────────

@app.route("/api/datasets", methods=["GET"])
def api_datasets():
    return jsonify([
        {"name": "DLPFC", "type": "labeled", "slices":
         ["151507", "151508", "151509", "151510", "151669",
          "151670", "151671", "151672", "151673", "151674",
          "151675", "151676"]},
        {"name": "Mouse_anterior_brain", "type": "labeled", "slices": [""]},
        {"name": "Breast_Cancer", "type": "labeled", "slices": [""]},
        {"name": "STARmap", "type": "labeled", "slices": [""]},
        {"name": "human_lung_cancer", "type": "unlabeled", "slices": [""]},
        {"name": "human_ovarian_cancer", "type": "unlabeled", "slices": [""]},
        {"name": "MERFISH", "type": "only_gene", "slices": [""]},
        {"name": "MERFISH_frontal_cortex", "type": "only_gene",
         "slices": ["4_0", "4_1", "4_2", "6_0", "6_1",
                    "6_2", "8_0", "8_1", "8_2"]},
        {"name": "osmFISH", "type": "only_gene", "slices": [""]},
    ])


@app.route("/api/infer", methods=["POST"])
def api_infer():
    data = request.get_json(force=True)
    if progress_state.get('done') is False:
        return jsonify({"status": "busy", "msg": "Another inference is running."}), 503
    thread = Thread(target=_run_infer_thread, args=(data,))
    thread.start()
    return jsonify({"status": "started"})


@app.route("/api/progress", methods=["GET"])
def api_progress():
    def event_stream():
        while True:
            state = {
                'dataset': progress_state.get('dataset', ''),
                'epoch': progress_state.get('epoch', 0),
                'loss': progress_state.get('loss', 0.0),
                'done': progress_state.get('done', True),
                'log': progress_state.get('log', '')
            }
            yield f"data: {json.dumps(state)}\n\n"
            if state['done']:
                break
            time.sleep(0.5)
    return Response(event_stream(), mimetype="text/event-stream")


@app.route("/api/metrics", methods=["GET"])
def api_metrics():
    """从 result_data/ 中汇总已完成的指标."""
    rows = []
    root = Path(__file__).parent.parent / 'result_data'
    if not root.exists():
        return jsonify(rows)

    for path in sorted(root.rglob('res_data.h5ad')):
        try:
            adata = _load_h5ad(path)
            if adata is None:
                continue
            parts = path.relative_to(root).parts
            rows.append({
                'dataset': parts[0] if parts else '',
                'slice': parts[1] if len(parts) > 1 else '',
                'ari': float(adata.uns.get('ari', 0)),
                'nmi': float(adata.uns.get('nmi', 0)),
                'sc': float(adata.uns.get('sc', 0)),
                'db': float(adata.uns.get('db', 0)),
            })
        except Exception:
            pass
    return jsonify(rows)


@app.route("/api/visualize/<dataset>", methods=["GET"])
def api_visualize(dataset):
    """返回组织切片的空间坐标与预测域标签，用于散点图渲染."""
    section = request.args.get('section', '')
    idx = request.args.get('idx', '')

    root = Path(__file__).parent.parent / 'result_data'
    if dataset == 'DLPFC':
        path = root / dataset / section / 'res_data.h5ad'
    elif dataset == 'MERFISH_frontal_cortex':
        path = root / dataset / idx / 'res_data.h5ad'
    else:
        path = root / dataset / 'res_data.h5ad'

    if not path.exists():
        return jsonify({"error": "Result not found. Please run inference first."}), 404

    adata = _load_h5ad(path)
    if adata is None:
        return jsonify({"error": "Cannot read h5ad."}), 500

    spatial = adata.obsm['spatial']
    domain = adata.obs.get('domain', adata.obs.get('leiden', []))
    labels = adata.obs.get('true_label', [])

    return jsonify({
        'x': spatial[:, 0].tolist(),
        'y': spatial[:, 1].tolist(),
        'domain': domain.astype(str).tolist(),
        'true_label': labels.astype(str).tolist() if len(labels) else [],
    })


@app.route("/")
def index():
    return send_from_directory(app.static_folder, 'index.html')


@app.route("/demo")
def demo():
    return send_from_directory(app.static_folder, 'demo.html')


# ── helpers ───────────────────────────────────────────

_adata_cache = {}


def _load_h5ad(path):
    """带缓存的 h5ad 读取."""
    key = str(path.resolve())
    if key in _adata_cache:
        return _adata_cache[key]
    try:
        import scanpy as sc
        ad = sc.read_h5ad(str(path))
        _adata_cache[key] = ad
        return ad
    except Exception:
        return None


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
