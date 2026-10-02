"""Small local configuration/preview API. No credentials or AI calls."""
import asyncio
from aiohttp import web

from . import tag_tools


def register_routes():
    from server import PromptServer
    routes = PromptServer.instance.routes

    @routes.get("/soda/tags/settings")
    async def get_settings(request):
        try:
            return web.json_response({**tag_tools.settings(), "categories": tag_tools.CATEGORIES})
        except (ValueError, OSError):
            return web.json_response({"error": "本地标签配置无法读取。"}, status=400)

    @routes.post("/soda/tags/settings")
    async def save_settings(request):
        try:
            body = await request.json()
            value = tag_tools.save_settings(body.get("resource_directory"), body.get("mappings"))
            return web.json_response({**value, "categories": tag_tools.CATEGORIES})
        except (ValueError, TypeError, AttributeError, OSError) as error:
            message = str(error) if isinstance(error, ValueError) else "标签配置格式或保存路径错误。"
            return web.json_response({"error": message}, status=400)

    @routes.post("/soda/tags/preview")
    async def preview(request):
        try:
            body = await request.json()
            prompt = body.get("prompt", "")
            if not isinstance(prompt, str) or len(prompt) > 100000:
                raise ValueError("标签文本过长或格式错误。")
            value = await asyncio.to_thread(tag_tools.gallery_record, body.get("site", ""), str(body.get("post_id", "")), prompt)
            gallery = value["gallery"]
            groups = await asyncio.to_thread(tag_tools.classify, gallery["selected_tags"], gallery["category_hints"])
            return web.json_response({"groups": groups, "original_tags": gallery["raw_tags"],
                "complete": gallery["complete"], "available": bool(gallery["selected_tags"]),
                "message": gallery.get("warning", "整理画廊当前输出的标签；完整网站标签单独保留供核对。")})
        except (ValueError, TypeError, AttributeError) as error:
            return web.json_response({"error": str(error) if isinstance(error, ValueError) else "预览输入格式错误。"}, status=400)

    @routes.post("/soda/tags/search")
    async def search(request):
        try:
            from .tag_search import search as local_search
            body = await request.json()
            result = await asyncio.to_thread(local_search, body.get("query"), body.get("top_k", 12))
            return web.json_response({"tags": result})
        except (ValueError, TypeError, AttributeError) as error:
            return web.json_response({"error": str(error) if isinstance(error, ValueError) else "搜索输入格式错误。"}, status=400)
        except Exception:
            return web.json_response({"error": "本地 BGE-M3 加载或推理失败，请检查模型与缓存版本；未发起下载或外部 AI 请求。"}, status=500)
