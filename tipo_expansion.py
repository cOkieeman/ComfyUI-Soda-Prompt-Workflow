"""Local TIPO expansion and the workbench's single expansion selector."""
import asyncio
import hashlib
import json
from pathlib import Path
import re
import sys
from uuid import uuid4
import weakref

import folder_paths

from . import core, suite
from .suite_nodes import SodaExpandSuite, controls, record, user_root
from .tipo_process import execute_worker


OFF = "关闭"
TIPO = "TIPO · 本地扩写"
AUTO = "自动（优先读取记录中的标签）"
NL = "自然语言"
TAGS = "标签"
MIXED = "标签＋描述（首段标签）"
KEEP = "保留原标签＋扩写描述"
GENERATED = "TIPO 标签＋描述"
NL_ONLY = "仅扩写描述"
MODELS = {
    "TIPO v2.1 · FP16": "TIPO-v2.1-1B-A200M_TIPO-v2.1-1B-A200M-f16.gguf",
    "TIPO v2.1 · Q8_0": "TIPO-v2.1-1B-A200M-Q8_0.gguf",
}
_generation_locks = weakref.WeakKeyDictionary()


def split_input(text, source, input_type):
    text = text.strip()
    if input_type == NL:
        return "", text
    if input_type == TAGS:
        return text, ""
    if input_type == MIXED:
        parts = re.split(r"\r?\n\s*\r?\n", text, maxsplit=1)
        if len(parts) == 1:
            parts = text.split("\n", 1)
        return parts[0].strip().rstrip(","), parts[1].strip() if len(parts) > 1 else ""
    if input_type != AUTO:
        raise ValueError("未知 TIPO 输入类型。")
    parts = source.get("prompt_parts", {})
    if parts.get("text") == text:
        return parts["tags"], parts["nl"]
    # Only use a structured record when it describes this exact text.
    if text == source.get("selected_prompt", source.get("faithful_prompt", "")).strip():
        tags = source.get("tags", [])
        if tags:
            tag_text = ", ".join(tags)
            if text.startswith(tag_text):
                return tag_text, text[len(tag_text):].lstrip(", \r\n")
    return "", text


def model_path(model):
    if model not in MODELS:
        raise ValueError("未知 TIPO 模型。")
    path = Path(folder_paths.models_dir) / "kgen" / MODELS[model]
    if not path.is_file():
        raise FileNotFoundError(f"TIPO 模型未就位，请放到：{path}")
    return path


async def run_tipo(tags, natural, path, parameters, timeout_seconds):
    # TIPO is optional: importing it here keeps off/Flash routes independent.
    import nodes

    node = nodes.NODE_CLASS_MAPPINGS.get("TIPO")
    if node is None:
        raise RuntimeError("TIPO 插件尚未加载，请重启 ComfyUI 后重试。")
    from comfy.cli_args import args
    plugin_root = Path(sys.modules[node.__module__].__file__).resolve().parents[1]
    return await execute_worker({"tags": tags, "natural": natural, "model_path": str(path.resolve()),
        "parameters": parameters, "plugin_root": str(plugin_root),
        "comfy_root": str(Path(folder_paths.__file__).resolve().parent),
        "cuda_device": args.cuda_device}, timeout_seconds)


async def expand_cached(tags, natural, path, parameters, refresh, timeout_seconds):
    identity = {"version": 2, "tags": tags, "natural": natural,
                "model": str(path.resolve()), "size": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns, "parameters": parameters,
                "refresh": refresh}
    key = hashlib.sha256(suite.dump(identity).encode("utf-8")).hexdigest()
    directory = user_root() / "tipo-cache-v1"
    target = directory / (key + ".json")
    loop = asyncio.get_running_loop()
    lock = _generation_locks.setdefault(loop, asyncio.Lock())
    # One worker at a time per host loop bounds GPU use; waiting counts toward the timeout.
    async with asyncio.timeout(timeout_seconds), lock:
        if target.is_file():
            return json.loads(target.read_text(encoding="utf-8")), True
        result = await run_tipo(tags, natural, path, parameters, timeout_seconds)
        if not result["description"]:
            raise RuntimeError("TIPO 返回的扩写描述为空；未保存缓存。")
        directory.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix("." + uuid4().hex + ".tmp")
        temporary.write_text(suite.dump(result), encoding="utf-8")
        temporary.replace(target)
        return result, False


class SodaExpansionChoice:
    IS_CHANGED = classmethod(suite.providers.changed)
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "source_text": ("STRING", {"multiline": True, "default": ""}),
            "method": ([OFF, TIPO, suite.K2, suite.DFLOW_EXPAND], {"default": OFF}),
            "requirements": ("STRING", {"multiline": True, "default": "保留主体、服装、姿态和构图，只细化可见内容。",
                "tooltip": "仅 Flash 路线使用。TIPO 的场景要求请写入上游文本，它不执行聊天指令。"}),
            "tipo_input": ([AUTO, NL, TAGS, MIXED], {"default": AUTO,
                "tooltip": "自动只识别匹配记录中的标签，其余按自然语言处理。粘贴 booru 标签请选择标签或标签＋描述。"}),
            "tipo_output": ([KEEP, GENERATED, NL_ONLY], {"default": KEEP}),
            "tipo_model": (list(MODELS), {"default": next(iter(MODELS))}),
            "tipo_device": (["cuda", "cpu"], {"default": "cuda"}),
            "tipo_seed": ("INT", {"default": 1234, "min": 0, "max": 2147483647,
                "tooltip": "固定种子便于对照；改种子可生成另一稿。"}),
            "tipo_length": (["very_short", "short", "long", "very_long"], {"default": "short"}),
            "tipo_temperature": ("FLOAT", {"default": 0.5, "min": 0.01, "max": 2.0, "step": 0.01}),
            "tipo_width": ("INT", {"default": 896, "min": 64, "max": 16384}),
            "tipo_height": ("INT", {"default": 1152, "min": 64, "max": 16384}),
            "tipo_ban_tags": ("STRING", {"default": "", "multiline": True}),
            **controls(),
        }, "optional": {"source_record": ("STRING", {"forceInput": True})}}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("selected_prompt", "record_json")
    FUNCTION = "run"
    CATEGORY = "Soda/Workbench"
    DESCRIPTION = "一次只执行选中的扩写方式；TIPO 使用本地 GGUF，Flash 保留原规则。默认关闭。"

    async def run(self, source_text, method, requirements, tipo_input, tipo_output,
                  tipo_model, tipo_device, tipo_seed, tipo_length, tipo_temperature,
                  tipo_width, tipo_height, tipo_ban_tags, refresh, timeout_seconds,
                  source_record=""):
        if method in (OFF, suite.K2, suite.DFLOW_EXPAND):
            return await SodaExpandSuite().run(source_text, method != OFF,
                suite.K2 if method == OFF else method, requirements, refresh,
                timeout_seconds, source_record)
        if method != TIPO:
            raise ValueError("未知扩写方式。")
        if not source_text.strip():
            raise ValueError("请先输入文本或连接提示词。")
        source = core.parse_json(source_record) if source_record.strip() else {}
        tags, natural = split_input(source_text, source, tipo_input)
        path = model_path(tipo_model)
        parameters = {"seed": tipo_seed, "device": tipo_device, "length": tipo_length,
            "temperature": tipo_temperature, "width": tipo_width, "height": tipo_height,
            "ban_tags": tipo_ban_tags}
        try:
            result, cache_hit = await expand_cached(tags, natural, path, parameters, refresh, timeout_seconds)
        except TimeoutError:
            raise TimeoutError("TIPO 本地扩写超时，推理进程已停止；未保存缓存。") from None
        if tipo_output == KEEP:
            prompt = "\n\n".join(x for x in (tags, result["description"]) if x)
        elif tipo_output == GENERATED:
            prompt = "\n\n".join(x for x in (result["generated_tags"], result["description"]) if x)
        elif tipo_output == NL_ONLY:
            prompt = result["description"]
        else:
            raise ValueError("未知 TIPO 输出形式。")
        detail = record("tipo_expansion", source_record=source,
            faithful_prompt=source_text, expanded_prompt=prompt, selected_prompt=prompt,
            tipo={"model": path.name, "parameters": parameters, "input_type": tipo_input,
                "output_type": tipo_output, "input_tags": tags, "input_nl": natural,
                "result": result, "cache_hit": cache_hit}, api_calls=0,
            validation={"passed": True, "problems": [], "scope": "nonempty_text",
                "warnings": ["TIPO 是文字扩写；请检查角色、动作和背景是否与原稿一致。"]})
        return prompt, suite.dump(detail)


NODE_CLASS_MAPPINGS = {"SodaExpansionChoice": SodaExpansionChoice}
NODE_DISPLAY_NAME_MAPPINGS = {"SodaExpansionChoice": "Soda · 扩写选择（TIPO / Flash）"}
