"""Local AI service profiles; credentials never enter workflows or API responses."""
import hashlib
import asyncio
import base64
import io
import json
import os
import time
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


def updated_profile(profile, update):
    if not isinstance(update, dict):
        raise ValueError("AI 服务配置格式错误。")
    profile = dict(profile)
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
    return profile


def save(key_path, value):
    if not isinstance(value, dict) or value.get("active") not in PRESETS or not isinstance(value.get("profiles"), dict):
        raise ValueError("AI 服务配置格式错误。")
    data = load(key_path)
    for name, update in value["profiles"].items():
        if name not in PRESETS or not isinstance(update, dict):
            raise ValueError("未知 AI 服务配置。")
        data["profiles"][name] = updated_profile(data["profiles"][name], update)
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


def draft_service(key_path, value, model_type=None):
    """Resolve unsaved form values without activating or persisting them."""
    from . import core
    if not isinstance(value, dict) or not isinstance(value.get("service"), str) or value["service"] not in PRESETS:
        raise ValueError("请选择有效的 AI 服务。")
    name = value["service"]
    profile = updated_profile(load(key_path)["profiles"][name], value.get("profile"))
    url = endpoint(profile["base_url"])
    model = ""
    if model_type is not None:
        if model_type not in ("text", "vision"):
            raise ValueError("请选择文字或看图测试。")
        model = profile[model_type + "_model"]
        if not model:
            raise ValueError("请填写看图模型。" if model_type == "vision" else "请填写文字模型。")
    key = profile.get("api_key", "").strip()
    if not key and name == "deepseek" and url == core.ENDPOINT:
        key = core.read_key(key_path)
    if not key:
        raise ValueError("请填写 API Key；更换服务地址后需要重新填写。")
    return {"id": name, "name": profile["name"], "endpoint": url,
        "model": model, "strict_model": profile["strict_model"]}, key


async def fetch_models(key_path, value):
    import aiohttp
    service, key = draft_service(key_path, value)
    url = service["endpoint"][:-len("/chat/completions")] + "/models"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(url, headers={"Authorization": f"Bearer {key}"}, allow_redirects=False) as response:
                if response.status != 200:
                    hints = {401: "密钥无效。", 403: "没有读取模型列表的权限。",
                        404: "此服务未提供 /models 接口，请手动填写模型名称。",
                        405: "此服务未提供 /models 接口，请手动填写模型名称。", 429: "接口限流，请稍后重试。"}
                    raise RuntimeError(f"拉取模型失败（HTTP {response.status}）："
                        + hints.get(response.status, "请检查服务地址或服务状态；仍可手动填写模型。"))
                data = await response.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError):
        raise RuntimeError("拉取模型网络失败或超时，请检查服务地址和网络。") from None
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError("模型列表响应不是有效 JSON，请手动填写模型。") from None
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise RuntimeError("模型列表不是 OpenAI 兼容格式，请手动填写模型。")
    models = sorted({item["id"].strip() for item in data["data"]
        if isinstance(item, dict) and isinstance(item.get("id"), str)
        and 0 < len(item["id"].strip()) <= 1000 and "\n" not in item["id"] and "\r" not in item["id"]
        and key not in item["id"]})
    if not models:
        raise RuntimeError("服务未返回可用模型，请检查账户权限或手动填写模型。")
    return {"models": models}


async def test_service(key_path, value):
    from . import core
    from PIL import Image
    model_type = value.get("model_type") if isinstance(value, dict) else None
    if model_type not in ("text", "vision"):
        raise ValueError("请选择文字或看图测试。")
    service, key = draft_service(key_path, value, model_type)
    content = "Reply with OK only."
    if model_type == "vision":
        buffer = io.BytesIO()
        Image.new("RGB", (32, 32), "white").save(buffer, format="PNG")
        image_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        content = [{"type": "text", "text": "What is the main color of this image? Reply briefly."},
            {"type": "image_url", "image_url": {"url": image_url}}]
    started = time.monotonic()
    try:
        _, meta = await core.chat_text(key, [{"role": "user", "content": content}], 30, 64, service=service)
    except RuntimeError as error:
        # Never forward upstream response bodies or credentials to the browser.
        raise RuntimeError(str(error).replace(key, "[已隐藏]")) from None
    response_model = meta.get("response_model")
    if not isinstance(response_model, str) or key in response_model:
        response_model = ""
    mismatch = response_model != service["model"]
    if mismatch and service["strict_model"]:
        raise RuntimeError("测试失败：服务返回的模型名称与请求不一致；请检查模型名称或关闭严格核对。")
    return {"ok": True, "model_type": model_type, "response_model": response_model,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "warning": "服务返回模型别名或未提供模型标识。" if mismatch else ""}


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

    async def probe(request, operation):
        origin = request.headers.get("Origin")
        if request.headers.get("Sec-Fetch-Site") == "cross-site" or (origin and urlsplit(origin).netloc != request.host):
            return web.json_response({"error": "请从当前 ComfyUI 页面拉取模型或测试。"}, status=403)
        headers = {"Cache-Control": "no-store"}
        try:
            return web.json_response(await operation(key_path(), await request.json()), headers=headers)
        except (ValueError, RuntimeError) as error:
            return web.json_response({"error": str(error)}, status=400, headers=headers)
        except (TypeError, KeyError):
            return web.json_response({"error": "请求或服务响应格式错误，请检查配置。"}, status=400, headers=headers)

    @PromptServer.instance.routes.post("/soda/ai-services/models")
    async def get_models(request):
        return await probe(request, fetch_models)

    @PromptServer.instance.routes.post("/soda/ai-services/test")
    async def check_service(request):
        return await probe(request, test_service)
