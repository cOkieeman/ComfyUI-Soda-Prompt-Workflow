"""Category selection between reverse/original prompt and the existing editing stages."""
import json

from . import core, suite, tag_tools as tools
from .suite_nodes import record


def organise(source_prompt, source, enabled, include_categories, category_edits):
    selected = json.loads(include_categories)
    edits = json.loads(category_edits)
    if not isinstance(selected, list) or any(c not in tools.CATEGORIES for c in selected):
        raise ValueError("标签类别取舍格式错误。")
    if not isinstance(edits, dict) or any(c not in tools.CATEGORIES or not isinstance(v, str) for c, v in edits.items()):
        raise ValueError("标签类别编辑格式错误。")
    matches = source.get("selected_prompt", source.get("faithful_prompt")) == source_prompt
    gallery = source.get("source_material", {}).get("gallery", {})
    # Never mistake a prose description or a manual edit for a comma-separated tag list.
    tags = source.get("tags", []) if matches else []
    if not tags and matches and source.get("stage") == "material_original":
        tags = gallery.get("selected_tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise ValueError("输入记录的标签格式错误。")
    original = gallery.get("raw_tags") or tags
    groups = tools.classify(tags, gallery.get("category_hints"))
    preview = {"groups": groups, "original_tags": original, "complete": gallery.get("complete", not bool(gallery)),
        "available": bool(tags), "message": "本地分类，不调用 AI。未分类标签默认保留。" if tags else "当前输入是自然语言或手动文本，原样通过；可在②选择使用画廊原标签或 Anima 标签反推。"}
    if not enabled or not tags:
        return source_prompt, source, preview
    keep = set(selected)
    retained = [tools.canonical(t) for t in tags if any(tools.canonical(t) in groups[c] for c in keep if c not in edits)]
    for c in tools.CATEGORIES:
        if c in keep and c in edits:
            retained.extend(tools.split_tags(edits[c]))
    retained = list(dict.fromkeys(retained))
    excluded = [t for t in map(tools.canonical, tags) if t not in retained]
    added = [t for t in retained if t not in set(map(tools.canonical, tags))]
    changed = bool(excluded or added)
    if not retained:
        raise ValueError("取舍后标签为空；请至少保留一类标签，或关闭标签整理。")
    tag_text = ", ".join(retained)
    natural = source.get("prompt_parts", {}).get("nl", "")
    if not natural:
        prefix = ", ".join(tags)
        if source_prompt.startswith(prefix):
            natural = source_prompt[len(prefix):].lstrip(", \r\n")
    # A caption can still describe removed clothes/characters; regeneration uses only the edited anchors.
    warning = "标签已修改；旧英文描述已移除以避免冲突，请在⑤重新适配生成描述。" if changed and natural else ""
    if changed:
        natural = ""
    prompt = "\n\n".join(v for v in (tag_text, natural) if v) if changed else source_prompt
    value = record("tag_organisation", source_record=source, faithful_prompt=prompt, selected_prompt=prompt,
        tags=retained, prompt_parts={"text": prompt, "tags": tag_text, "nl": natural}, api_calls=0,
        tag_organisation={"original_tags": original, "input_tags": tags, "groups": groups,
            "included_categories": selected, "category_edits": edits, "excluded_tags": excluded,
            "added_tags": added, "mapping_fingerprint": tools.fingerprint()},
        validation={"passed": True, "problems": [], "warnings": [warning] if warning else []})
    preview.update(message=warning or preview["message"], selected_tags=retained)
    return prompt, value, preview


class SodaTagOrganizer:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "source_prompt": ("STRING", {"forceInput": True}),
            "enabled": ("BOOLEAN", {"default": True}),
            "include_categories": ("STRING", {"default": json.dumps(list(tools.CATEGORIES)), "tooltip": "通过分类标签按钮选择；默认全部保留。"}),
            "category_edits": ("STRING", {"default": "{}", "tooltip": "通过分类标签按钮编辑；未编辑的类别沿用原标签。"}),
        }, "optional": {"source_record": ("STRING", {"forceInput": True})}}

    RETURN_TYPES = ("STRING",) * (3 + len(tools.CATEGORIES))
    RETURN_NAMES = ("selected_prompt", "record_json", "all_original_tags", *tools.CATEGORIES)
    FUNCTION = "run"
    CATEGORY = "Soda/Workbench"
    IS_CHANGED = classmethod(lambda cls, **kwargs: tools.fingerprint())

    def run(self, source_prompt, enabled=True, include_categories=json.dumps(list(tools.CATEGORIES)), category_edits="{}", source_record=""):
        source = core.parse_json(source_record) if source_record else {}
        prompt, value, preview = organise(source_prompt, source, enabled, include_categories, category_edits)
        outputs = (prompt, suite.dump(value), ", ".join(preview["original_tags"]),
                   *(", ".join(preview["groups"][c]) for c in tools.CATEGORIES))
        return {"ui": {"tag_preview": [preview]}, "result": outputs}


NODE_CLASS_MAPPINGS = {"SodaTagOrganizer": SodaTagOrganizer}
NODE_DISPLAY_NAME_MAPPINGS = {"SodaTagOrganizer": "Soda · 分类标签 / 取舍与映射"}
