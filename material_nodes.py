"""Read selected local DFlow cards and connect existing gallery outputs."""
import io
import json
import asyncio
from urllib.parse import quote

import aiohttp
import numpy as np
from PIL import Image, ImageOps

from . import core, providers, suite
from .suite_nodes import SodaReferenceSuite, record, controls


async def dflow_request(port, path, *, method="GET", body=None, binary=False):
    if not 1024 <= int(port) <= 65535 or not path.startswith("/api/"):
        raise ValueError("DFlow 仅允许本机端口与 API 路径。")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), trust_env=False) as session:
            async with session.request(method, f"http://127.0.0.1:{int(port)}{path}",
                                       json=body, allow_redirects=False) as response:
                if response.status not in (200, 201):
                    raise RuntimeError(f"DFlow HTTP {response.status}；请检查服务、卡片和图片缓存。")
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > 40 * 1024 * 1024:
                        raise ValueError("DFlow 数据超过 40 MiB，请选择较小图片。")
                return bytes(data) if binary else json.loads(data)
    except (aiohttp.ClientError, TimeoutError):
        raise RuntimeError("无法连接本机 DFlow。请先启动 DFlow 并确认端口。") from None


def normalize_card(item, kind, port):
    identifier = str(item["id"])
    if kind == "favorite":
        image_path = "/api/reverse/image/" + quote(identifier, safe="") if item.get("cacheStatus") == "ready" else ""
    else:
        stem = "worded" if kind == "worded" else "metadata"
        image_path = f"/api/{stem}/images/" + quote(identifier, safe="") if item.get("imageExt") else ""
    return {"key": kind + ":" + identifier, "id": identifier, "kind": kind, "port": int(port),
            "folder": "metadata" if kind == "metadata" else item.get("folder", "original"),
            "name": str(item.get("name") or item.get("summary") or item.get("title") or identifier),
            "prompt": str(item.get("positive") or item.get("prompt") or ""),
            "negative": str(item.get("negative") or ""), "image_path": image_path,
            "metadata": {k: item[k] for k in ("model", "loras", "seed", "steps", "cfg", "sampler", "scheduler", "width", "height", "source", "preset", "reversePreset", "customInstruction", "reverseStatus") if k in item}}


async def dflow_cards(port):
    # /state includes account configuration. Only whitelist favorite fields; never return or log account data.
    state = await dflow_request(port, "/api/state")
    cards = [normalize_card(x, "favorite", port) for x in state.get("favorites", [])]
    del state
    for kind, endpoint in (("worded", "/api/worded/entries"), ("metadata", "/api/metadata/images")):
        entries = await dflow_request(port, endpoint)
        cards.extend(normalize_card(x, kind, port) for x in entries)
    return cards


async def get_card(port, key):
    if not key:
        raise ValueError("请先点击节点的‘选择 DFlow 素材’并选择一张卡片。")
    card = next((x for x in await dflow_cards(port) if x["key"] == key), None)
    if card is None:
        raise ValueError("选中的 DFlow 卡片已不存在，请重新选择。")
    return card


class SodaDFlowSource:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "port": ("INT", {"default": 4173, "min": 1024, "max": 65535}),
            "card_key": ("STRING", {"default": "", "tooltip": "通过选择素材按钮填写，无需手输 ID。"}),
        }}
    RETURN_TYPES = ("IMAGE", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("image", "original_prompt", "source_record", "negative_prompt")
    FUNCTION = "run"
    CATEGORY = "Soda/Material Sources"
    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    async def run(self, port, card_key):
        card = await get_card(port, card_key)
        image = None
        if card["image_path"]:
            raw = await dflow_request(port, card["image_path"], binary=True)
            with Image.open(io.BytesIO(raw)) as im:
                pixels = np.asarray(ImageOps.exif_transpose(im).convert("RGB"), dtype=np.float32) / 255
            import torch
            image = torch.from_numpy(pixels)[None,]
        return (image, card["prompt"], suite.dump(card), card["negative"])


class SodaMaterialPrompt:
    @classmethod
    def IS_CHANGED(cls, **kwargs):
        from .local_vlm import changed
        return changed(cls, **kwargs)
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "action": (["使用素材原提示词", "重新反推"],),
            "route": ([suite.PIXEL, suite.ANIMA, suite.DFLOW, suite.LOCAL_QWEN],),
            "user_prompt": ("STRING", {"default": "", "multiline": True}),
            "use_card_instruction": ("BOOLEAN", {"default": True}),
            **controls(),
        }, "optional": {
            "source_image": ("IMAGE", {"lazy": True}),
            "source_text": ("STRING", {"forceInput": True, "lazy": True}),
            "source_record": ("STRING", {"forceInput": True}),
        }}
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("selected_prompt", "record_json")
    FUNCTION = "run"
    CATEGORY = "Soda/Material Sources"

    def check_lazy_status(self, action, source_image=None, source_text=None, **kwargs):
        name, value = ("source_image", source_image) if action == "重新反推" else ("source_text", source_text)
        return [name] if value is None else []

    async def run(self, action, route, user_prompt, use_card_instruction, refresh, timeout_seconds,
                  source_image=None, source_text=None, source_record=""):
        source = core.parse_json(source_record) if source_record else {}
        if action == "使用素材原提示词":
            if not source_text or not source_text.strip():
                raise ValueError("素材没有原提示词。请切换为重新反推；不会自动收费调用。")
            value = record("material_original", faithful_prompt=source_text, selected_prompt=source_text,
                           source_material=source, api_calls=0,
                           validation={"passed": True, "problems": [], "warnings": ["这是素材保存的原提示词或站点标签，不是新反推结果。"]})
            return (source_text, suite.dump(value))
        if action != "重新反推":
            raise ValueError("未知素材处理模式。")
        if source_image is None or source_image.shape[1] <= 1 or source_image.shape[2] <= 1:
            raise ValueError("素材没有可读取的图片，请在画廊选图或等待 DFlow 高清缓存完成。")
        extra = source.get("metadata", {}).get("customInstruction", "") if use_card_instruction else ""
        instruction = "\n".join(x for x in (extra, user_prompt) if x.strip())
        if instruction and route not in (suite.PIXEL, suite.LOCAL_QWEN):
            raise ValueError("图文修改要求仅适用于阿丹或本地 Qwen 路线；其他路线请关闭卡片附加要求并清空输入。")
        prompt, raw = await SodaReferenceSuite().run(source_image, route, 0, 1600, refresh, timeout_seconds, instruction)
        value = core.parse_json(raw)
        value["source_material"] = source
        return (prompt, suite.dump(value))


class SodaDFlowWriteback:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"prompt": ("STRING", {"forceInput": True}),
                             "source_record": ("STRING", {"forceInput": True}),
                             "enabled": ("BOOLEAN", {"default": False})}}
    RETURN_TYPES = ("STRING",)
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = "Soda/Material Sources"
    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    async def run(self, prompt, source_record, enabled):
        if not enabled:
            status = "DFlow 回写关闭；预览满意后再开启。"
        else:
            reference = core.parse_json(source_record)
            card = await get_card(reference["port"], reference["key"])
            if card["metadata"].get("reverseStatus") == "processing":
                raise ValueError("这张卡片正在被其他任务处理，请等待；没有覆盖。")
            if not prompt.strip():
                raise ValueError("不能回写空提示词。")
            if card["prompt"] == prompt.strip():
                status = "DFlow 已有相同结果，无重复写入。"
            else:
                stem = {"favorite": "favorites", "worded": "worded/entries", "metadata": "metadata/entries"}[card["kind"]]
                await dflow_request(card["port"], "/api/" + stem + "/" + quote(card["id"], safe=""),
                                    method="PATCH", body={"prompt": prompt.strip()})
                status = "已回写原卡片；待反推卡片按 DFlow 规则移入有词区。"
        return {"ui": {"text": [status]}, "result": (status,)}


NODE_CLASS_MAPPINGS = {c.__name__: c for c in (SodaDFlowSource, SodaMaterialPrompt, SodaDFlowWriteback)}
NODE_DISPLAY_NAME_MAPPINGS = {"SodaDFlowSource": "Soda · DFlow 素材入口",
                           "SodaMaterialPrompt": "Soda · 素材原词 / 重新反推",
                           "SodaDFlowWriteback": "Soda · 回写 DFlow 原卡片"}


def local_thumbnail(image_path):
    from .unified import local_image_path
    with Image.open(local_image_path(image_path)) as original:
        image = ImageOps.exif_transpose(original).convert("RGB")
        image.thumbnail((320, 320))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=80)
    return output.getvalue()


def register_routes():
    from aiohttp import web
    from server import PromptServer

    @PromptServer.instance.routes.get("/soda/materials/local/image")
    async def local_image(request):
        try:
            data = await asyncio.to_thread(local_thumbnail, request.query.get("image_path", ""))
            return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "no-store"})
        except (OSError, ValueError, Image.DecompressionBombError):
            return web.Response(status=400)

    @PromptServer.instance.routes.get("/soda/materials/dflow")
    async def list_cards(request):
        try:
            cards = await dflow_cards(int(request.query.get("port", "4173")))
            return web.json_response({"cards": cards})
        except (ValueError, RuntimeError) as error:
            return web.json_response({"error": str(error)}, status=400)

    @PromptServer.instance.routes.get("/soda/materials/dflow/image")
    async def card_image(request):
        try:
            port = int(request.query.get("port", "4173"))
            card = await get_card(port, request.query.get("key", ""))
            if not card["image_path"]:
                return web.Response(status=404)
            data = await dflow_request(port, card["image_path"], binary=True)
            # Browser previews are bounded thumbnails; node execution reads the original cache.
            with Image.open(io.BytesIO(data)) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                im.thumbnail((320, 320))
                output = io.BytesIO()
                im.save(output, format="JPEG", quality=80)
            return web.Response(body=output.getvalue(), content_type="image/jpeg")
        except (ValueError, RuntimeError):
            return web.Response(status=400)


try:
    from server import PromptServer
except ImportError:
    pass  # Unit tests without a ComfyUI server.
else:
    register_routes()
