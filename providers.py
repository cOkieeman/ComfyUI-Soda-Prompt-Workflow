"""Local AI service profiles; credentials never enter workflows or API responses."""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

PRESETS = {
    "deepseek": {"name": "DeepSeek", "base_url": "https://api.deepseek.com", "text_model": "deepseek-flash",
        "vision_model": "deepseek-flash", "strict_model": True},
    "glm": {"name": "GLM / 智谱", "base_url": "https://open.bigmodel.cn/api/paas/v4", "text_model": "",
        "vision_model": "", "strict_model": False},
    "gemini": {"name": "Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "text_model": "", "vision_model": "", "strict_model": False},
    "custom": {"name": "自定义兼容服务", "base_url": "", "text_model": "",
        "vision_model": "", "strict_model": False},
}


def config_path(key_path):
    return Path(key_path).parent / "ai_services.json"


def load(key_path):
    data = {"active": "deepseek", "profiles": {name: dict(value) for name, value in PRESETS.items()}}
    path = config_path(key_path)
    if path.is_file():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            data["active"] = saved["active"]
            for name, profile in saved["profiles"].items():
                if name in data["profiles"]:
                    data["profiles"][name].update(profile)
        except (OSError, ValueError, KeyError, TypeError):
            raise RuntimeError("AI 服务配置无法读取，请打开 AI 服务配置重新保存。") from None
    if data["active"] not in PRESETS:
        raise ValueError("未知 AI 服务配置。")
    return data


def endpoint(base_url):
    url = base_url.strip().rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("服务地址需要完整 HTTP(S) 地址，不能包含密钥、账号、查询参数或片段。")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("远程服务地址请使用 HTTPS；HTTP 仅用于本机服务。")
    return url if parsed.path.endswith("/chat/completions") else url + "/chat/completions"


def public_config(key_path):
    data = load(key_path)
    for name, profile in data["profiles"].items():
        configured = bool(profile.pop("api_key", ""))
        if name == "deepseek":
            # Check availability without returning or parsing the legacy key.
            configured = configured or bool(os.environ.get("DEEPSEEK_API_KEY")) or Path(key_path).is_file()
        profile["key_configured"] = configured
    return data


def save(key_path, value):
    if not isinstance(value, dict) or value.get("active") not in PRESETS or not isinstance(value.get("profiles"), dict):
        raise ValueError("AI 服务配置格式错误。")
    data = load(key_path)
    for name, update in value["profiles"].items():
        if name not in PRESETS or not isinstance(update, dict):
            raise ValueError("未知 AI 服务配置。")
        profile = data["profiles"][name]
        old_url = profile["base_url"].rstrip("/")
        for field in ("base_url", "text_model", "vision_model"):
            item = update.get(field, profile[field])
            if not isinstance(item, str) or len(item) > 1000 or "\n" in item or "\r" in item:
                raise ValueError("服务地址和模型名称必须是单行文本。")
            profile[field] = item.strip()
        if profile["base_url"]:
            endpoint(profile["base_url"])
        strict = update.get("strict_model", profile["strict_model"])
        if not isinstance(strict, bool):
            raise ValueError("模型严格校验开关格式错误。")
        profile["strict_model"] = strict
        key = update.get("api_key", "")
        if not isinstance(key, str) or len(key) > 8192 or "\n" in key or "\r" in key:
            raise ValueError("API Key 必须是单行文本。")
        if key.strip():
            profile["api_key"] = key.strip()
        elif profile["base_url"].rstrip("/") != old_url:
            profile.pop("api_key", None)
    data["active"] = value["active"]
    active = data["profiles"][data["active"]]
    endpoint(active["base_url"])
    if not active["text_model"]:
        raise ValueError("请填写当前服务的文字模型名称。")
    path = config_path(key_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return public_config(key_path)


def resolve(key_path, vision=False):
    data = load(key_path)
    name = data["active"]
    profile = data["profiles"][name]
    model = profile["vision_model" if vision else "text_model"]
    if not model:
        raise ValueError("请在 AI 服务配置中填写支持图片输入的看图模型。" if vision else "请在 AI 服务配置中填写文字模型。")
    return {"id": name, "name": profile["name"], "endpoint": endpoint(profile["base_url"]),
        "model": model, "strict_model": profile["strict_model"]}


def read_key(key_path, service):
    from . import core
    data = load(key_path)
    profile = data["profiles"][service["id"]]
    key = profile.get("api_key", "").strip()
    if not key and service["id"] == "deepseek" and service["endpoint"] == "https://api.deepseek.com/chat/completions":
        return core.read_key(key_path)
    if not key:
        raise RuntimeError("当前 AI 服务尚未配置 API Key，请打开 AI 服务配置填写。")
    return key


def default_service():
    return {"id": "deepseek", "name": "DeepSeek", "endpoint": "https://api.deepseek.com/chat/completions",
        "model": "deepseek-flash", "strict_model": True}


def has_images(messages):
    return any(isinstance(message.get("content"), list) and any(part.get("type") == "image_url" for part in message["content"])
        for message in messages)


def fingerprint(key_path):
    data = public_config(key_path)
    profile = data["profiles"][data["active"]]
    profile.pop("key_configured", None)
    return hashlib.sha256(json.dumps({"active": data["active"], "profile": profile}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def default_fingerprint():
    return hashlib.sha256(json.dumps({"active": "deepseek", "profile": PRESETS["deepseek"]}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def changed(cls, **kwargs):
    from .nodes import key_path
    return fingerprint(key_path())


def register_routes():
    from aiohttp import web
    from server import PromptServer
    from .nodes import key_path

    @PromptServer.instance.routes.get("/soda/ai-services")
    async def get_services(request):
        try:
            return web.json_response(public_config(key_path()), headers={"Cache-Control": "no-store"})
        except (ValueError, RuntimeError) as error:
            return web.json_response({"error": str(error)}, status=400)

    @PromptServer.instance.routes.post("/soda/ai-services")
    async def set_services(request):
        origin = request.headers.get("Origin")
        if request.headers.get("Sec-Fetch-Site") == "cross-site" or (origin and urlsplit(origin).netloc != request.host):
            return web.json_response({"error": "请从当前 ComfyUI 页面保存配置。"}, status=403)
        try:
            return web.json_response(save(key_path(), await request.json()), headers={"Cache-Control": "no-store"})
        except (ValueError, RuntimeError, TypeError):
            return web.json_response({"error": "保存失败，请检查服务地址、模型和密钥格式。配置未修改。"}, status=400)
