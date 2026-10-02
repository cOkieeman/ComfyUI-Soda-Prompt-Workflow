"""Optional, offline BGE-M3 CLS embeddings over the user's precomputed tag cache."""
import threading
import re
from pathlib import Path

from . import tag_tools

_lock = threading.Lock()
_engine = None
_identity = None


def search(query, top_k=12):
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise ValueError("请输入不超过 500 字的中文或英文描述。")
    directory = tag_tools.settings().get("resource_directory", "")
    if not directory:
        raise ValueError("请先设置本地语义资源目录。模型不会自动下载。")
    root = Path(directory)
    required = [root / "models/bge-m3/config.json", root / "tags_embedding/metadata.parquet",
                root / "tags_embedding/emb_cn.safetensors", root / "tags_embedding/emb_en.safetensors"]
    if not all(path.is_file() for path in required):
        raise ValueError("资源不完整，需要 models/bge-m3 与 tags_embedding（metadata、emb_cn、emb_en）。")
    identity = (directory, tuple(p.stat().st_mtime_ns for p in required))
    global _engine, _identity
    with _lock:
        try:
            import torch
            import pyarrow.parquet as pq
            from safetensors.torch import load_file
            from transformers import AutoModel, AutoTokenizer
        except ImportError:
            raise ValueError("语义搜索依赖未安装，请用 ComfyUI 的 Python 安装 requirements-tags.txt。") from None
        if _engine is None or _identity != identity:
            _engine = None
            rows = pq.read_table(required[1], columns=["name", "cn_name", "category"]).to_pylist()
            embeddings = [load_file(str(p), device="cpu")[key] for p, key in
                          ((required[2], "emb_cn"), (required[3], "emb_en"))]
            if any(e.ndim != 2 or e.shape != (len(rows), 1024) or not torch.isfinite(e).all() for e in embeddings):
                raise ValueError("标签向量与 metadata 行数或维度不一致。")
            embeddings = [torch.nn.functional.normalize(e.float(), dim=1) for e in embeddings]
            tokenizer = AutoTokenizer.from_pretrained(root / "models/bge-m3", local_files_only=True, trust_remote_code=False)
            model = AutoModel.from_pretrained(root / "models/bge-m3", local_files_only=True,
                trust_remote_code=False, use_safetensors=True).to("cpu").eval()
            _engine = (rows, embeddings, tokenizer, model)
            _identity = identity
        rows, embeddings, tokenizer, model = _engine
        with torch.inference_mode():
            inputs = tokenizer(query.strip(), return_tensors="pt", truncation=True, max_length=256)
            dense = torch.nn.functional.normalize(model(**inputs).last_hidden_state[:, 0].float(), dim=1)[0]
            weight_cn = 0.7 if re.search(r'[\u4e00-\u9fff]', query) else 0.3
            scores = weight_cn * (embeddings[0] @ dense) + (1 - weight_cn) * (embeddings[1] @ dense)
            # Exact translated/tag names should outrank a merely related semantic neighbour.
            exact = torch.tensor([query.strip().lower() in [row['name'].replace('_', ' '), row['name'],
                *(part.strip().lower() for part in re.split(r'[,，、]', row['cn_name'] or ''))]
                for row in rows], dtype=scores.dtype)
            scores += exact
            count = min(max(int(top_k), 1), 30, len(rows))
            values, indices = torch.topk(scores, count)
        return [{"tag": rows[i]["name"], "translation": rows[i]["cn_name"],
                 "category": tag_tools.SITE_CATEGORIES.get(rows[i]["category"], "unknown"), "score": round(float(s), 4)}
                for s, i in zip(values.tolist(), indices.tolist())]
