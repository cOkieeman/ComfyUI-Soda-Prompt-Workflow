"""Pinned WD inference and deterministic Anima validation; no network calls."""
import hashlib
import importlib.util
import io
import json
import re
from functools import lru_cache
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image
from tokenizers import Tokenizer

ROOT = Path(__file__).parent
VENDOR = ROOT / "vendor" / "anima_tagger"
MODEL_FOLDER = "wd-eva02-tagger-2026-canary-onnx-v2"
HASHES = {
    "model.onnx": "fd78fbdf9390cbd163e4dd28f754a5bbf83bc7a111c4d20270f22415a0f66c95",
    "selected_tags.csv": "3f78c28ee0d50779edb320733f76aeaf4184694cbd09c631deef6889865f9178",
}
SLOTS = ("rating", "count", "character", "appearance", "clothing_props", "pose_expression", "framing", "scene")
LAYERS = ("Composition & Pose", "Hair & Head Accessories", "Face & Expression", "Clothing & Details", "Props & Floating Elements", "Lighting & Color", "Background")


def load_vendor(name):
    spec = importlib.util.spec_from_file_location("soda_vendor_" + name, VENDOR / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = load_vendor("anima_validate")
wd = load_vendor("wd_tagger")


def load_model(directory):
    directory = Path(directory)
    for name, expected in HASHES.items():
        path = directory / name
        if not path.is_file():
            raise RuntimeError(f"缺少原版 WD 资源：{path}。请按 SOURCES.md 中的固定版本补齐资源。")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise RuntimeError(f"WD 资源哈希不匹配：{name}，已停止。")
    names, cats = wd.load_tags(directory / "selected_tags.csv")
    if len(names) != 16473:
        raise RuntimeError("WD 词表不是固定版本的 16473 维。")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 8
    session = ort.InferenceSession(str(directory / "model.onnx"), sess_options=options, providers=["CPUExecutionProvider"])
    return session, names, np.array(cats)


def tag_image(pixels, directory):
    array = np.asarray(pixels)
    if array.ndim != 3 or array.shape[-1] not in (3, 4) or not np.isfinite(array).all():
        raise ValueError("WD 需要有效 RGB/RGBA 图片。")
    buffer = io.BytesIO()
    Image.fromarray(np.rint(np.clip(array, 0, 1) * 255).astype(np.uint8)).save(buffer, format="PNG")
    buffer.seek(0)
    session, names, cats = load_model(str(directory))
    inp = session.get_inputs()[0]
    shape = inp.shape
    nhwc = isinstance(shape[-1], int) and shape[-1] == 3
    size = shape[1] if nhwc else shape[2]
    arr = wd.to_array(wd.preprocess(buffer, size))
    x = arr[None] if nhwc else np.transpose(arr, (2, 0, 1))[None]
    scores = np.asarray(session.run(None, {inp.name: np.ascontiguousarray(x)})[0]).reshape(-1)
    if len(scores) != len(names) or not np.isfinite(scores).all():
        raise RuntimeError("WD 模型输出维度或数值无效。")
    probabilities = scores if scores.min() >= 0 and scores.max() <= 1 else 1 / (1 + np.exp(-scores))
    result = {"model": MODEL_FOLDER, "thresholds": {"general": 0.35, "character": 0.85, "auto_keep": 0.8}}
    for name, category, minimum in (("rating", 9, 0), ("general", 0, 0.35), ("character", 4, 0.85)):
        result[name] = dict(sorted(((names[i], float(probabilities[i])) for i in np.where(cats == category)[0] if probabilities[i] >= minimum), key=lambda x: -x[1]))
    return result


def candidates(tagged):
    scored = {validator.normalize_tag(k): v for group in ("general", "character") for k, v in tagged[group].items()}
    rating = max(tagged["rating"], key=tagged["rating"].get)
    scored[rating] = tagged["rating"][rating]
    high = {k: v for k, v in scored.items() if v >= 0.8 or k == rating}
    low = {k: v for k, v in scored.items() if k not in high}
    return scored, high, low


def reviewed_tags(tagged, review):
    scored, high, low = candidates(tagged)
    decisions = review.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("核验响应缺少逐标签 decisions。")
    checked = {}
    for item in decisions:
        if not isinstance(item, dict) or item.get("tag") not in low or item["tag"] in checked:
            raise ValueError("核验包含新增、重复或非低分标签。")
        if not isinstance(item.get("keep"), bool) or not isinstance(item.get("reason"), str) or not item["reason"].strip():
            raise ValueError("每个低分标签必须有明确保留决定和视觉理由。")
        checked[item["tag"]] = item
    if set(checked) != set(low):
        raise ValueError("Flash 没有逐项核验全部低分标签。")
    kept = list(high) + [tag for tag, item in checked.items() if item["keep"]]
    return kept, scored


def reconcile_reverse_slots(slots, kept, tagged):
    """Reconcile transport slots with authoritative WD/visual review decisions.

    This cannot add a visual fact or change a keep decision. Preserve the
    model's grouping where possible; unknown missing tags remain in the text
    but require manual grouping review rather than a false validation pass.
    """
    if not isinstance(slots, dict) or set(slots) - set(SLOTS):
        raise ValueError("反推槽位必须是八槽位对象，不能含未知槽位。")
    result = {slot: [] for slot in SLOTS}
    audit = {name: [] for name in ("restored_tags", "removed_tags", "deduplicated_tags", "unassigned_tags")}
    allowed, seen = set(kept), set()
    for slot in SLOTS:
        values = slots.get(slot, [])
        if not isinstance(values, list) or not all(isinstance(tag, str) and tag.strip() and "," not in tag for tag in values):
            raise ValueError("反推槽位必须为单标签字符串数组。")
        for value in values:
            tag = validator.normalize_tag(value)
            if tag not in allowed:
                audit["removed_tags"].append(tag)
            elif tag in seen:
                audit["deduplicated_tags"].append(tag)
            else:
                result[slot].append(tag)
                seen.add(tag)
    characters = {validator.normalize_tag(tag) for tag in tagged["character"]}
    hints = {"cat girl": "appearance", "cat boy": "appearance", "cleavage": "appearance",
             "paw print": "clothing_props", "signature": "scene", "artist name": "scene"}
    for tag in kept:
        if tag in seen:
            continue
        if tag in tagged["rating"]:
            slot = "rating"
        elif re.fullmatch(r"(?:\d+[+]?(?:girl|boy|other)s?|solo|multiple girls|multiple boys)", tag):
            slot = "count"
        elif tag in characters:
            slot = "character"
        else:
            slot = hints.get(tag)
        if slot is None:
            slot = "scene"
            audit["unassigned_tags"].append(tag)
        result[slot].append(tag)
        seen.add(tag)
        audit["restored_tags"].append(tag)
    return result, audit


@lru_cache(maxsize=1)
def tokenizer():
    # No heuristic fallback: validation must use the shipped upstream tokenizer.
    path = VENDOR / "models" / "t5_tokenizer" / "tokenizer.json"
    if hashlib.sha256(path.read_bytes()).hexdigest() != "2432e8aa83414a69884a5cbd54a68f528c042d1f30780affbefcbb85091f022e":
        raise RuntimeError("原版 T5 tokenizer 哈希不匹配。")
    tok = Tokenizer.from_file(str(path))
    tok.no_truncation()
    return tok


def token_count(text):
    ids = tokenizer().encode(text, add_special_tokens=False).ids
    return len(ids), 2 in ids


def forbidden(text):
    return re.findall(r"\b(?:masterpiece|best quality|worst quality|ultra detailed|score_\w+|BREAK)\b|\([^\n()]+:\s*[0-9.]+\)", text, flags=re.I)


def validate_anima(slots, nl, allowed=None, scores=None, enforce_limit=True, require_nl=True):
    if not isinstance(slots, dict) or set(slots) != set(SLOTS):
        raise ValueError("标签必须包含全部八个槽位。")
    ordered = []
    for slot in SLOTS:
        values = slots[slot]
        if not isinstance(values, list) or not all(isinstance(x, str) and x.strip() and "," not in x for x in values):
            raise ValueError("槽位必须为单标签字符串数组。")
        ordered.extend(validator.normalize_tag(x) for x in values)
    source_folded = []
    if allowed is not None:
        expected, source_folded, _ = validator.fold_redundant(list(dict.fromkeys(allowed)), scores)
        actual, _, _ = validator.fold_redundant(list(dict.fromkeys(ordered)), scores)
        if set(ordered) - set(allowed) or set(actual) != set(expected):
            raise ValueError("标签组装新增或遗漏了已经核验的标签。")
    if not ordered:
        raise ValueError("标签为空。")
    tags, folded, blocked = validator.fold_redundant(list(dict.fromkeys(ordered)), scores)
    multi, _ = validator.detect_multi(tags)
    conflicts = validator.find_conflicts(tags, is_multi=multi)
    if not isinstance(nl, str):
        raise ValueError("NL 必须是字符串。")
    nl = re.sub(r"\s+", " ", nl).strip()
    if nl and not nl.endswith("."):
        nl += "."
    text = ", ".join(tags) + ("\n\n" + nl if nl else "")
    count, unknown = token_count(text)
    problems = []
    if require_nl and not 2 <= len(re.findall(r"[^.!?]+[.!?](?:\s|$)", nl)) <= 4:
        problems.append("标准反推/创作需要 2–4 句自然语言")
    if conflicts:
        problems.append("槽位冲突需要回图确认")
    if forbidden(text):
        problems.append("含上游规范禁用的质量词、BREAK 或权重语法")
    if unknown:
        problems.append("T5 分词出现未知字符")
    if enforce_limit and count > 512:
        problems.append("超过原版兼容预算 512 token")
    return {"text": text, "tags": tags, "nl": nl, "tokens": count, "tokenizer": "upstream t5 tokenizer.json; special tokens excluded", "limit_enforced": enforce_limit, "folded": folded, "source_folded": source_folded, "blocked_folds": blocked, "conflicts": conflicts, "problems": problems, "passed": not problems}


def conditioning_text(graph, link):
    if not isinstance(link, list) or len(link) != 2 or link[1] != 0:
        return None
    node = graph.get(str(link[0]), {})
    if not isinstance(node, dict) or node.get("class_type") not in ("CLIPTextEncode", "PCLazyTextEncode"):
        return None
    inputs = node.get("inputs", {})
    if not isinstance(inputs, dict):
        return None
    text = inputs.get("text")
    return text if isinstance(text, str) else None


def sampler_prompts(graph):
    pairs = []
    for node in graph.values():
        if not isinstance(node, dict) or node.get("class_type") not in ("KSampler", "KSamplerAdvanced", "SamplerCustom"):
            continue
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            return None
        positive = conditioning_text(graph, inputs.get("positive"))
        negative = conditioning_text(graph, inputs.get("negative"))
        if positive is None or negative is None:
            return None
        pairs.append((positive, negative))
    return pairs[0] if pairs and all(pair == pairs[0] for pair in pairs) else None


def metadata(path):
    path = Path(path)
    with Image.open(path) as im:
        info = im.info
        result = {"path": str(path), "size": list(im.size), "parameters": info.get("parameters", ""), "positive": "", "negative": "", "settings": "", "encoder_texts": []}
        parameters = result["parameters"]
        if isinstance(parameters, str) and "\nNegative prompt:" in parameters:
            result["positive"], tail = parameters.split("\nNegative prompt:", 1)
            result["negative"], _, result["settings"] = tail.partition("\nSteps:")
        elif isinstance(parameters, str):
            result["positive"], _, result["settings"] = parameters.partition("\nSteps:")
        try:
            graph = json.loads(info.get("prompt", "{}"))
        except (TypeError, json.JSONDecodeError):
            graph = {}
            result["graph_warning"] = "prompt 元数据不是有效 JSON。"
        if not isinstance(graph, dict):
            graph = {}
            result["graph_warning"] = "prompt 元数据不是节点对象。"
        for identifier, node in graph.items():
            if isinstance(node, dict) and node.get("class_type") in ("CLIPTextEncode", "PCLazyTextEncode") and isinstance(node.get("inputs"), dict):
                value = node.get("inputs", {}).get("text")
                if isinstance(value, str):
                    result["encoder_texts"].append({"node": identifier, "text": value})
        if not parameters:
            prompts = sampler_prompts(graph)
            if prompts is not None:
                result["positive"], result["negative"] = prompts
            elif result["encoder_texts"]:
                result["graph_warning"] = "无法唯一确定正负面文本；编码节点清单仅供查看，不作为原提示词。"
    if not result["parameters"] and not result["encoder_texts"]:
        result["graph_warning"] = "图片没有可读取的原提示词；可选择重新反推。"
    result["note"] = "parameters 是保存时的元数据；连接到其他节点的文本不会被臆测成最终编码文本。"
    return result
