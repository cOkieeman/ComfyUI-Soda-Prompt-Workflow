import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import folder_paths

from . import core, providers


def key_path():
    return (
        Path(folder_paths.get_user_directory())
        / "soda_prompt_workflow"
        / "secrets.toml"
    )


class SodaDeepSeekObserve:
    IS_CHANGED = classmethod(providers.changed)
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "image": ("IMAGE",),
                "image_index": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "tooltip": "单次只处理批次中的这一张，避免隐式批量计费。",
                    },
                ),
                "max_side": (
                    "INT",
                    {
                        "default": 1600,
                        "min": 0,
                        "max": 8192,
                        "step": 64,
                        "tooltip": "上传前等比缩小长边；0 保留原尺寸。",
                    },
                ),
                "refresh": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 2147483647,
                        "tooltip": "改变此值会重新调用看图 API；它不是生图 seed。",
                    },
                ),
                "timeout_seconds": ("INT", {"default": 120, "min": 10, "max": 600}),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("observation_json",)
    FUNCTION = "observe"
    CATEGORY = "Soda/Prompt Workflow"
    DESCRIPTION = (
        "将指定图片发给官方 DeepSeek Flash，只提取可见事实。相同输入复用 ComfyUI 缓存。"
    )

    async def observe(self, image, image_index, max_side, refresh, timeout_seconds):
        service = providers.resolve(key_path(), vision=True)
        key = providers.read_key(key_path(), service)
        if image_index >= len(image):
            raise ValueError("image_index 超出图片批次数量。")
        data_url, image_info = core.encode_image(
            image[image_index].detach().cpu().numpy(), max_side
        )
        messages = [
            {"role": "system", "content": core.OBSERVE_SYSTEM},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Analyze this reference image and return the observation JSON.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": data_url, "detail": "original"},
                    },
                ],
            },
        ]
        response, metadata = await core.chat(key, messages, timeout_seconds, 2500, service=service)
        record = core.validate_observation(response)
        record["source"] = image_info | {"image_index": image_index}
        record["api"] = metadata
        return (json.dumps(record, ensure_ascii=False, indent=2),)


class SodaDeepSeekPrompt:
    IS_CHANGED = classmethod(providers.changed)
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "observation_json": ("STRING", {"forceInput": True}),
                "target_model": (list(core.PROFILES), {"default": "Anima"}),
                "expand": (
                    "BOOLEAN",
                    {"default": False, "label_on": "扩写", "label_off": "忠实反推"},
                ),
                "requirements": (
                    "STRING",
                    {"default": "保留主体外观、服装、姿态和构图。", "multiline": True},
                ),
                "refresh": (
                    "INT",
                    {
                        "default": 0,
                        "min": 0,
                        "max": 2147483647,
                        "tooltip": "改变此值仅重新生成提示词，不重新看图。",
                    },
                ),
                "max_tokens": (
                    "INT",
                    {"default": 3500, "min": 256, "max": 16000, "step": 256},
                ),
                "timeout_seconds": ("INT", {"default": 120, "min": 10, "max": 600}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("prompt", "faithful_prompt", "expanded_prompt", "record_json")
    FUNCTION = "compose"
    CATEGORY = "Soda/Prompt Workflow"
    DESCRIPTION = "按目标模型整理提示词。扩写开启时 prompt 输出扩写稿，faithful_prompt 始终保留忠实稿。"

    async def compose(
        self,
        observation_json,
        target_model,
        expand,
        requirements,
        refresh,
        max_tokens,
        timeout_seconds,
    ):
        observation = core.parse_json(observation_json)
        messages = core.compose_messages(
            observation, target_model, expand, requirements
        )
        service = providers.resolve(key_path())
        response, metadata = await core.chat(
            providers.read_key(key_path(), service), messages, timeout_seconds, max_tokens, service=service)
        prompts = core.validate_prompts(response, expand)
        selected = prompts["expanded_prompt"] if expand else prompts["faithful_prompt"]
        record = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "profile_version": core.PROFILE_VERSION,
            "target_model": target_model,
            "expand": expand,
            "requirements": requirements,
            "observation": observation,
            "prompts": prompts,
            "api": metadata,
            "validation": {
                "response_structure": "checked",
                "target_tokenizer": "not_checked",
                "generation_quality": "not_tested",
            },
        }
        return (
            selected,
            prompts["faithful_prompt"],
            prompts["expanded_prompt"],
            json.dumps(record, ensure_ascii=False, indent=2),
        )


class SodaSavePromptRecord:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "record_json": ("STRING", {"forceInput": True}),
                "save": (
                    "BOOLEAN",
                    {"default": False, "label_on": "保存", "label_off": "不保存"},
                ),
            }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("saved_path",)
    FUNCTION = "save_record"
    OUTPUT_NODE = True
    CATEGORY = "Soda/Prompt Workflow"

    def save_record(self, record_json, save):
        if not save:
            return ("保存开关已关闭",)
        record = core.parse_json(record_json)
        prompts = core.validate_prompts(
            record.get("prompts", {}), bool(record.get("expand"))
        )
        target = record.get("target_model")
        if target not in core.PROFILES:
            raise ValueError("归档记录中的目标模型无效。")
        output_root = Path(folder_paths.get_output_directory()).resolve()
        parent = (output_root / "soda_prompt_workflow").resolve()
        if not parent.is_relative_to(output_root):
            raise ValueError("归档目录必须位于 ComfyUI output 内。")
        parent.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        directory = parent / f"{stamp}_{target}_{uuid4().hex[:8]}"
        directory.mkdir()
        (directory / "record.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (directory / "faithful.txt").write_text(
            prompts["faithful_prompt"], encoding="utf-8"
        )
        if prompts["expanded_prompt"]:
            (directory / "expanded.txt").write_text(
                prompts["expanded_prompt"], encoding="utf-8"
            )
        return (str(directory),)


NODE_CLASS_MAPPINGS = {
    "SodaDeepSeekObserve": SodaDeepSeekObserve,
    "SodaDeepSeekPrompt": SodaDeepSeekPrompt,
    "SodaSavePromptRecord": SodaSavePromptRecord,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "SodaDeepSeekObserve": "Soda · DeepSeek 看图",
    "SodaDeepSeekPrompt": "Soda · 模型提示词 / 扩写",
    "SodaSavePromptRecord": "Soda · 保存提示词记录",
}
