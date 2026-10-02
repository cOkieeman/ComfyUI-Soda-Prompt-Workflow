"""One workbench with lazy source selection; unselected providers never execute."""
from pathlib import Path

import folder_paths
import numpy as np
from PIL import Image, ImageOps

from . import core, suite
from .material_nodes import SodaDFlowSource, SodaMaterialPrompt, SodaDFlowWriteback
from .suite_nodes import SodaMetadata, SodaTextSuite, SodaVariant, record

LOCAL, GALLERY, DFLOW, TEXT = "本地图片 / PNG元数据", "作者画廊", "DFlow 素材", "文字输入"
CREATE = "文字创作"


def local_image_path(image_path):
    if not image_path.strip():
        raise ValueError("请上传本地图片或填写完整路径。")
    path = Path(image_path.strip().strip('"'))
    return path if path.is_absolute() else Path(folder_paths.get_annotated_filepath(str(path)))


class SodaGallerySource:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}, "hidden": {"selection_data": ("STRING", {"default": "{}", "forceInput": True})}}
    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("images", "prompts", "source_record")
    OUTPUT_IS_LIST = (True, True, True)
    OUTPUT_NODE = False
    FUNCTION = "run"
    CATEGORY = "Soda/Workbench"

    @classmethod
    def IS_CHANGED(cls, selection_data="{}", **kwargs):
        return selection_data

    def run(self, selection_data="{}"):
        import nodes
        cls = nodes.NODE_CLASS_MAPPINGS.get("DanbooruGalleryNode")
        if cls is None:
            raise ValueError("作者画廊插件未加载。")
        images, prompts = cls().get_selected_data(selection_data=selection_data)
        from .tag_tools import gallery_record
        selections = core.parse_json(selection_data).get("selections", [])
        records = []
        # Native gallery skips failed images. Refuse to attach another picture's provenance.
        aligned = len(selections) == len(prompts)
        for index, prompt in enumerate(prompts):
            selection = selections[index] if aligned else {}
            value = gallery_record(selection.get("source_site", "danbooru"), str(selection.get("post_id", "")), prompt)
            records.append(suite.dump(value))
        return images, prompts, records


class SodaUnifiedSource:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "source": ([LOCAL, GALLERY, DFLOW, TEXT], {"default": TEXT}),
            "image_path": ("STRING", {"default": "", "tooltip": "完整本机路径，或上传图片后自动填写的 input 文件名。也读取 PNG 原提示词。"}),
            "text": ("STRING", {"default": "", "multiline": True}),
            "port": ("INT", {"default": 4173, "min": 1024, "max": 65535}),
            "card_key": ("STRING", {"default": ""}),
        }, "optional": {
            "gallery_image": ("IMAGE", {"lazy": True}),
            "gallery_text": ("STRING", {"forceInput": True, "lazy": True}),
            "gallery_record": ("STRING", {"forceInput": True, "lazy": True}),
        }}
    RETURN_TYPES = ("IMAGE", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("image", "original_prompt", "source_record", "negative_prompt")
    FUNCTION = "run"
    CATEGORY = "Soda/Workbench"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def check_lazy_status(self, source, gallery_image=None, gallery_text=None, gallery_record="", **kwargs):
        if source != GALLERY:
            return []
        missing = [name for name, value in (("gallery_image", gallery_image), ("gallery_text", gallery_text)) if value is None]
        if gallery_record is None:
            missing.append("gallery_record")
        return missing

    async def run(self, source, image_path, text, port, card_key, gallery_image=None, gallery_text=None, gallery_record=None):
        if source == DFLOW:
            return await SodaDFlowSource().run(port, card_key)
        if source == TEXT:
            return (None, text, suite.dump({"source": TEXT}), "")
        if source == GALLERY:
            from .tag_tools import split_tags
            value = core.parse_json(gallery_record) if gallery_record else {"source": GALLERY,
                "gallery": {"selected_tags": split_tags(gallery_text or ""), "raw_tags": split_tags(gallery_text or ""), "complete": False}}
            return (gallery_image, gallery_text or "", suite.dump(value), "")
        if source != LOCAL:
            raise ValueError("未知素材入口。")
        path = local_image_path(image_path)
        with Image.open(path) as im:
            pixels = np.asarray(ImageOps.exif_transpose(im).convert("RGB"), dtype=np.float32) / 255
        import torch
        prompt, metadata = SodaMetadata().run(str(path))
        value = core.parse_json(metadata)
        value.update(source=LOCAL, image_path=str(path))
        return (torch.from_numpy(pixels)[None,], prompt, suite.dump(value), value.get("negative", ""))


class SodaUnifiedProcess(SodaMaterialPrompt):
    @classmethod
    def INPUT_TYPES(cls):
        schema = super().INPUT_TYPES()
        schema["required"]["action"] = (["使用素材原提示词", "重新反推", CREATE],)
        schema["required"]["variant_index"] = ("INT", {"default": 0, "min": 0, "max": 2})
        return schema

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("selected_prompt", "record_json", "all_variants")

    async def run(self, action, route, user_prompt, use_card_instruction, refresh, timeout_seconds,
                  variant_index=0, source_image=None, source_text=None, source_record=""):
        if action == CREATE:
            requirements = "\n".join(x for x in (source_text or "", user_prompt) if x.strip())
            if not requirements:
                raise ValueError("文字创作需要先填写文字需求。")
            variants, raw = await SodaTextSuite().run(requirements, refresh, timeout_seconds)
            prompt, selected = SodaVariant().run(raw, variant_index)
            value = core.parse_json(selected)
            value["source_material"] = core.parse_json(source_record) if source_record else {}
            return (prompt, suite.dump(value), variants)
        prompt, raw = await super().run(action, route, user_prompt, use_card_instruction, refresh,
                                       timeout_seconds, source_image, source_text, source_record)
        return (prompt, raw, "")


class SodaUnifiedWriteback(SodaDFlowWriteback):
    async def run(self, prompt, source_record, enabled):
        source = core.parse_json(source_record) if source_record else {}
        if enabled and not all(k in source for k in ("key", "port", "kind")):
            status = "当前入口不是 DFlow，已跳过回写。"
            return {"ui": {"text": [status]}, "result": (status,)}
        return await super().run(prompt, source_record, enabled)


NODE_CLASS_MAPPINGS = {c.__name__: c for c in (SodaGallerySource, SodaUnifiedSource, SodaUnifiedProcess, SodaUnifiedWriteback)}
NODE_DISPLAY_NAME_MAPPINGS = {
    "SodaGallerySource": "Soda · 作者画廊（按需执行）",
    "SodaUnifiedSource": "Soda · 统一素材入口开关",
    "SodaUnifiedProcess": "Soda · 原词 / 反推 / 文字创作",
    "SodaUnifiedWriteback": "Soda · DFlow 回写开关",
}
