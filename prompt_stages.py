"""Keep the author's prefix/suffix and optional OC replacement stages."""
from . import core, suite
from .nodes import key_path
from .preset_config import read_preset
from .suite_nodes import controls, record, user_root


class SodaPromptStages:
    IS_CHANGED = classmethod(suite.providers.changed)
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "source_prompt": ("STRING", {"forceInput": True}),
            "prefix": ("STRING", {"default": "", "multiline": True}),
            "suffix": ("STRING", {"default": "", "multiline": True}),
            "replace_oc": ("BOOLEAN", {"default": False}),
            "oc_definition": ("STRING", {"default": "", "multiline": True}),
            "oc_features": ("STRING", {"default": "发型、发色、眼睛、服装、配饰", "multiline": True}),
            **controls(),
        }, "optional": {"source_record": ("STRING", {"forceInput": True})}}
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("selected_prompt", "record_json")
    FUNCTION = "run"
    CATEGORY = "Soda/Full Prompt Suite"

    async def run(self, source_prompt, prefix, suffix, replace_oc, oc_definition, oc_features,
                  refresh, timeout_seconds, source_record=""):
        source = core.parse_json(source_record) if source_record else {}
        if not prefix.strip() and not suffix.strip() and not replace_oc:
            if source.get("faithful_prompt") == source_prompt or source.get("selected_prompt") == source_prompt:
                return (source_prompt, source_record)
            return (source_prompt, suite.dump(record("prompt_stages_off", faithful_prompt=source_prompt,
                    validation={"passed": True, "problems": []}, api_calls=0)))
        prompt = ", ".join(x.strip() for x in (prefix, source_prompt, suffix) if x.strip())
        api = None
        if replace_oc:
            if not source_prompt.strip() or not oc_definition.strip():
                raise ValueError("OC 替换需要原提示词和 OC 设定，未发起请求。")
            rule = read_preset("author_oc.txt")
            messages = [{"role": "system", "content": rule}, {"role": "user", "content":
                "原始提示词：\n" + prompt + "\n\n用户的OC设定：\n" + oc_definition + "\n\n要替换的内容：\n" + oc_features}]
            prompt, api = await suite.cached_chat(user_root() / "cache-v2", key_path(),
                "author_oc_original_v1", messages, refresh, timeout_seconds, 6000, text_output=True)
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("提示词处理输出为空。")
        result = record("prefix_suffix_oc", original_prompt=source_prompt, prefix=prefix, suffix=suffix,
                        source_record=source, replace_oc=replace_oc, oc_definition=oc_definition,
                        oc_features=oc_features, faithful_prompt=prompt, selected_prompt=prompt,
                        api=api, api_calls=1 if api and not api.get("cache_hit") else 0,
                        validation={"passed": True, "problems": [], "scope": "nonempty_text",
                            "warnings": ["前后缀/OC 处理后的文本请人工复核；未声称通过 Anima 八槽校验。"]})
        tags = source.get("tags", [])
        matches = source.get("selected_prompt", source.get("faithful_prompt")) == source_prompt
        if not replace_oc and matches and tags:
            tag_text = ", ".join(tags)
            if source_prompt.startswith(tag_text):
                natural = source_prompt[len(tag_text):].lstrip(", \r\n")
                result["tags"] = tags
                result["prompt_parts"] = {"text": prompt, "tags": tag_text,
                    "nl": ", ".join(x.strip() for x in (prefix, natural, suffix) if x.strip())}
        return (prompt, suite.dump(result))


NODE_CLASS_MAPPINGS = {"SodaPromptStages": SodaPromptStages}
NODE_DISPLAY_NAME_MAPPINGS = {"SodaPromptStages": "Soda · 前后缀 / 作者 OC 替换开关"}
