"""Local tag organisation. Unknown tags are retained; no model is loaded at startup."""
import hashlib
import json
import re
import threading
from functools import lru_cache
from pathlib import Path

from .suite_nodes import user_root

CATEGORIES = {
    "meta": "质量 / 元标签", "count": "人数", "character": "角色", "series": "作品",
    "artist": "画师", "appearance": "外观", "clothing": "服装", "pose": "动作 / 构图",
    "expression": "表情", "background": "背景", "accessories": "配饰",
    "other": "其他", "unknown": "未分类",
}
SITE_CATEGORIES = {1: "artist", 3: "series", 4: "character", 5: "meta"}
_lock = threading.RLock()


def canonical(tag):
    tag = tag.replace(r"\(", "(").replace(r"\)", ")")
    return re.sub(r"\s+", "_", tag.strip().lower())


def split_tags(text):
    if not isinstance(text, str) or len(text) > 100000:
        raise ValueError("标签必须是长度合理的文本。")
    return list(dict.fromkeys(canonical(t) for t in re.split(r"[,，、\n]+", text) if t.strip()))


def settings():
    path = user_root() / "tag_tools.json"
    if not path.exists():
        return {"resource_directory": "", "mappings": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("mappings"), dict):
        raise ValueError("标签配置损坏，请检查本机 tag_tools.json。")
    return value


def save_settings(directory=None, mappings=None):
    with _lock:
        value = settings()
        if directory is not None:
            if not isinstance(directory, str) or len(directory) > 4096:
                raise ValueError("语义资源目录格式错误。")
            directory = directory.strip().strip('"')
            if directory and not (Path(directory) / "tags_embedding/metadata.parquet").is_file():
                raise ValueError("目录中需要 tags_embedding/metadata.parquet。请填写解压后的资源根目录。")
            value["resource_directory"] = directory
        if mappings is not None:
            if not isinstance(mappings, dict) or len(mappings) > 500:
                raise ValueError("每次最多保存 500 个标签映射。")
            for tag, category in mappings.items():
                if not isinstance(tag, str) or not tag.strip() or len(tag) > 200 or re.search(r"[,，\r\n]", tag):
                    raise ValueError("映射需要单个标签。")
                if category is not None and category not in CATEGORIES:
                    raise ValueError("未知标签类别。")
                if category is None:
                    value["mappings"].pop(canonical(tag), None)
                else:
                    value["mappings"][canonical(tag)] = category
        path = user_root() / "tag_tools.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        return value


def fingerprint():
    path = user_root() / "tag_tools.json"
    return hashlib.sha256(path.read_bytes() if path.exists() else b"").hexdigest()


@lru_cache(maxsize=2)
def catalog(directory):
    if not directory:
        return {}
    try:
        import pyarrow.parquet as pq
    except ImportError:
        raise ValueError("本地标签缓存需要 pyarrow；请安装 requirements-tags.txt。") from None
    table = pq.read_table(Path(directory) / "tags_embedding/metadata.parquet", columns=["name", "category"])
    return {canonical(row["name"]): SITE_CATEGORIES.get(row["category"], "general") for row in table.to_pylist()}


RULES = {
    "appearance": {"hair", "eyes", "skin", "ears", "tail", "wings", "horns", "bangs", "twintails", "ponytail", "braids", "ahoge", "pale_skin", "long_hair", "short_hair"},
    "clothing": {"shirt", "skirt", "dress", "boots", "sleeves", "collar", "uniform", "socks", "stockings", "pants", "shorts", "gloves", "jacket", "coat", "cape", "hat", "scarf", "belt", "apron", "shoes", "sandals", "thighhighs", "pantyhose", "swimsuit", "bikini", "kimono", "hoodie"},
    "pose": {"sitting", "standing", "lying", "kneeling", "crouching", "leaning_forward", "arms_up", "hand_up", "holding", "pointing", "waving", "looking_at_viewer", "looking_back", "from_side", "from_behind", "from_above", "from_below", "full_body", "upper_body", "portrait", "close-up"},
    "expression": {"smile", "smirk", "frown", "pout", "blush", "angry", "sad", "happy", "tongue_out", "open_mouth", "closed_mouth", "crying", "tears", "expressionless", "surprised", "embarrassed"},
    "background": {"background", "outdoors", "indoors", "night", "day", "sunset", "cityscape", "beach", "forest", "room", "street", "clouds", "sky", "ocean", "garden"},
    "accessories": {"ornament", "glasses", "headphones", "headset", "ribbon", "hairpin", "brooch", "sunglasses", "bow", "choker", "bracelet", "ring", "necklace", "earrings", "backpack", "bag"},
    "other": {"pixel_art", "lineart", "watercolor", "monochrome", "greyscale", "pastel_colors", "gradient", "backlighting", "signature", "text", "flower", "flowers"},
    "meta": {"safe", "general", "sensitive", "questionable", "explicit", "masterpiece", "best_quality", "high_quality", "highres", "absurdres"},
}


def classify(tags, hints=None):
    config = settings()
    local = catalog(config.get("resource_directory", ""))
    hints = {canonical(k): v for k, v in (hints or {}).items()}
    groups = {key: [] for key in CATEGORIES}
    for tag in tags:
        t = canonical(tag)
        category = config["mappings"].get(t) or hints.get(t) or local.get(t)
        if category not in CATEGORIES:
            category = "count" if re.fullmatch(r"\d+(?:girl|boy|other)s?|solo|multiple_(?:girls|boys|others)", t) else None
            if category is None:
                for key, rules in RULES.items():
                    if any(t == rule or t.endswith("_" + rule) for rule in rules):
                        category = key
                        break
        groups[category if category in CATEGORIES else "unknown"].append(t)
    return groups


@lru_cache(maxsize=128)
def gallery_record(site, post_id, prompt):
    """Ask the installed gallery for public tag metadata, retaining its original output on failure."""
    result = {"source": "作者画廊", "gallery": {"site": site, "post_id": str(post_id),
        "selected_tags": split_tags(prompt), "raw_tags": split_tags(prompt), "category_hints": {}, "complete": False}}
    if site not in ("danbooru", "gelbooru") or not str(post_id).isdigit():
        return result
    try:
        import nodes
        cls = nodes.NODE_CLASS_MAPPINGS.get("DanbooruGalleryNode")
        if not cls or not hasattr(cls, "get_posts_internal"):
            return result
        posts = json.loads(cls.get_posts_internal(tags="id:" + str(post_id), limit=1, source=site)[0])
        post = next((p for p in posts if str(p.get("id")) == str(post_id)), {})
        if not post.get("tag_string"):
            return result
        gallery = result["gallery"]
        gallery["raw_tags"] = list(dict.fromkeys(post["tag_string"].split()))
        for field, category in (("artist", "artist"), ("copyright", "series"), ("character", "character"), ("meta", "meta")):
            for tag in post.get("tag_string_" + field, "").split():
                gallery["category_hints"][canonical(tag)] = category
        gallery["complete"] = True
    except Exception:
        # Credentials, response bodies and third-party errors must not enter records.
        result["gallery"]["warning"] = "网站完整标签读取失败；保留画廊当前输出，未猜测角色或画师。"
    return result


def tag_intent(source):
    current = source
    for _ in range(16):
        if not isinstance(current, dict) or current.get("replace_oc"):
            break
        if isinstance(current.get("tag_organisation"), dict):
            value = current["tag_organisation"]
            return {k: value[k] for k in ("excluded_tags", "added_tags") if k in value}
        current = current.get("source_record")
    return {}
