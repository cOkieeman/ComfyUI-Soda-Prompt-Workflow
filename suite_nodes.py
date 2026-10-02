"""ComfyUI-native routes for the two source projects."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from uuid import uuid4

import folder_paths

from . import core, providers, local_pipeline as local, suite, target_prompt as target_adapter
from .nodes import key_path
from .preset_config import read_preset


def user_root():
    return Path(folder_paths.get_user_directory()) / "soda_prompt_workflow"


async def call(stage, messages, refresh, timeout, max_tokens):
    return await suite.cached_chat(user_root() / "cache-v2", key_path(), stage, messages,
                                   refresh, timeout, max_tokens)


def controls():
    return {"refresh": ("INT", {"default": 0, "min": 0, "max": 2147483647,
                                "tooltip": "相同输入持久缓存；增加此值才重新调用并计费。"}),
            "timeout_seconds": ("INT", {"default": 180, "min": 10, "max": 600})}


def record(stage, **values):
    return {"suite_version": suite.VERSION, "stage": stage,
            "created_at": datetime.now(timezone.utc).isoformat(), **values}


def display(prompt, detail):
    return {"ui": {"text": [prompt]}, "result": (prompt, suite.dump(detail))}


class SodaReferenceSuite:
    IS_CHANGED = classmethod(providers.changed)
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"image": ("IMAGE",),
                             "route": ([suite.PIXEL, suite.ANIMA, suite.DFLOW],),
                             "image_index": ("INT", {"default": 0, "min": 0}),
                             "max_side": ("INT", {"default": 1600, "min": 0, "max": 8192}),
                             **controls()},
                "optional": {"user_prompt": ("STRING", {"default": "", "multiline": True,
                    "tooltip": "仅阿丹路线使用。留空纯反推、默认英文；中文附加要求会按原规则输出中文。填写修改要求将执行图文融合。"})}}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("faithful_prompt", "record_json")
    FUNCTION = "run"
    CATEGORY = "Soda/Full Prompt Suite"
    DESCRIPTION = "阿丹原文直接交 Flash 看图；另可选 WD + Flash 复核或 DFlow 忠实观察。阿丹路线不先打标、不改写原规则。"

    async def run(self, image, route, image_index, max_side, refresh, timeout_seconds, user_prompt=""):
        if not 0 <= image_index < len(image):
            raise ValueError("image_index 超出批次。")
        pixels = image[image_index].detach().cpu().numpy()
        data_url, image_info = core.encode_image(pixels, max_side)
        if user_prompt.strip() and route != suite.PIXEL:
            raise ValueError("附加要求仅用于阿丹像素级描述路线；其他路线请留空。")
        if route == suite.PIXEL:
            rule = read_preset("adan_pixel.txt")
            rule_hash = hashlib.sha256(rule.encode("utf-8")).hexdigest()
            messages = [{"role": "system", "content": rule}, suite.visual_message(
                user_prompt.strip() or "Describe this reference image.", data_url)]
            response, api = await suite.cached_chat(
                user_root() / "cache-v2", key_path(), "adan_pixel_original_v1", messages,
                refresh, timeout_seconds, 6000, text_output=True)
            if not isinstance(response, str) or not response.strip():
                raise ValueError("像素级反推没有返回有效文本；未自动重试。")
            prompt = response.strip()
            result = record("adan_pixel_original", source=image_info,
                            preset="像素级描述（by:阿丹）", preset_sha256=rule_hash,
                            user_prompt=user_prompt, mode="fusion" if user_prompt.strip() else "reverse",
                            faithful_prompt=prompt, selected_prompt=prompt, api=api,
                            validation={"passed": True, "problems": [], "scope": "nonempty_text",
                                "warnings": ["作者原规则的自然语言输出；仅检查非空，未校验视觉准确性或 Anima 槽位。"],
                                "visual_accuracy": "not guaranteed"})
        elif route == suite.ANIMA:
            tagged = local.tag_image(pixels, user_root() / "assets" / local.MODEL_FOLDER)
            response, api = await call("anima_reverse", suite.reverse_messages(tagged, data_url), refresh, timeout_seconds, 6000)
            original_response = response
            repairs = []
            kept, _ = local.reviewed_tags(tagged, response)
            slots, slot_audit = local.reconcile_reverse_slots(response.get("slots"), kept, tagged)
            response = response | {"slots": slots}
            if any(slot_audit.values()):
                repairs.append({"kind": "local_slot_reconciliation", **slot_audit, "api_calls": 0})
            report = suite.validate_reverse(tagged, response)
            if report["tokens"] > 512 and local.token_count(", ".join(report["tags"]))[0] < 512:
                repair, repair_api = await call("anima_nl_shorten", [
                    {"role": "system", "content": "Return JSON {\"nl\":\"two short English sentences\"}. Shorten only the supplied natural language while retaining key visible spatial/action/light relationships. No invented details. Tags are immutable. Aim under the given remaining T5 token budget."},
                    {"role": "user", "content": suite.dump({"nl": response["nl"], "remaining_budget": 512 - local.token_count(", ".join(report["tags"]))[0]})}],
                    refresh, timeout_seconds, 1800)
                response = response | {"nl": repair.get("nl")}
                repairs.append({"kind": "nl_shorten", "api": repair_api})
                report = suite.validate_reverse(tagged, response)
            if slot_audit["unassigned_tags"]:
                report["problems"].append("补回的标签槽位待人工确认：" + ", ".join(slot_audit["unassigned_tags"]))
                report["passed"] = False
            result = record("anima_reverse", source=image_info, wd=tagged, review=response,
                            original_review=original_response, repairs=repairs,
                            validation=report, api=api, faithful_prompt=report["text"], tags=report["tags"])
        elif route == suite.DFLOW:
            response, api = await call("dflow_observe", [
                {"role": "system", "content": suite.DFLOW_OBSERVE},
                suite.visual_message("请完整记录可见事实，为后续扩写提供依据。", data_url)], refresh, timeout_seconds, 4000)
            observed = core.validate_observation(response)
            result = record("dflow_observe", source=image_info, observation=observed, api=api,
                            faithful_prompt=observed["description"],
                            validation={"passed": True, "problems": [], "visual_accuracy": "not guaranteed"})
        else:
            raise ValueError("未知反推路线。")
        return (result["faithful_prompt"], suite.dump(result))


class SodaTextSuite:
    IS_CHANGED = classmethod(providers.changed)
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"requirements": ("STRING", {"multiline": True, "default": "成年探险者在水晶洞穴中阅读笔记，温暖灯光与蓝色晶体反光。"}), **controls()}}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("variants_text", "record_json")
    FUNCTION = "run"
    CATEGORY = "Soda/Full Prompt Suite"
    DESCRIPTION = "原版文字创作 A/B 交一稿、C/D 交 2–3 稿，分别进行真实 T5 校验；在记录中保留补充内容。"

    async def run(self, requirements, refresh, timeout_seconds):
        response, api = await call("anima_create", suite.create_messages(requirements), refresh, timeout_seconds, 6500)
        reports = suite.validate_creation(response)
        text = "\n\n--- variant ---\n\n".join(r["text"] for r in reports)
        result = record("anima_create", requirements=requirements, response=response, variants=reports,
                        faithful_prompt=reports[0]["text"], tags=reports[0]["tags"], api=api,
                        validation={"passed": all(r["passed"] for r in reports),
                                    "problems": [p for r in reports for p in r["problems"]]})
        return (text, suite.dump(result))


class SodaExpandSuite:
    IS_CHANGED = classmethod(providers.changed)
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "source_text": ("STRING", {"multiline": True, "default": ""}),
            "enabled": ("BOOLEAN", {"default": False}),
            "preset": ([suite.K2, suite.DFLOW_EXPAND],),
            "requirements": ("STRING", {"multiline": True, "default": "保留主体、服装、姿态和构图，只细化可见内容。"}),
            **controls()}, "optional": {"source_record": ("STRING", {"forceInput": True})}}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("selected_prompt", "record_json")
    FUNCTION = "run"
    CATEGORY = "Soda/Full Prompt Suite"
    DESCRIPTION = "关闭时零请求。开启后选 K2 七层英文长稿或 DFlow 中文通用适配；长稿另存，源稿保留。"

    async def run(self, source_text, enabled, preset, requirements, refresh, timeout_seconds, source_record=""):
        source = core.parse_json(source_record) if source_record.strip() else {}
        # A record must match the text, otherwise the independently edited text is the source.
        matches = source.get("faithful_prompt") == source_text
        tags = source.get("tags", []) if matches else []
        if not enabled:
            return (source_text, suite.dump(record("expansion_off", source_record=source,
                    faithful_prompt=source_text, selected_prompt=source_text, expanded_prompt="",
                    validation=source.get("validation", {"passed": True, "problems": []}), api_calls=0)))
        if not source_text.strip():
            raise ValueError("请连接反推结果或输入已有提示词。")
        if matches and not source.get("validation", {}).get("passed", True):
            raise ValueError("源稿仍有校验问题，请先查看记录；不自动收费扩写未通过的源稿。")
        response, api = await call("expand:" + preset,
                                  suite.expansion_messages(preset, source_text, requirements, tags),
                                  refresh, timeout_seconds, 7000)
        report = suite.validate_expansion(preset, response, tags)
        result = record("expansion", preset=preset, source_record=source, faithful_prompt=source_text,
                        expanded_prompt=report["text"], selected_prompt=report["text"],
                        response=response, validation=report, api=api)
        return (report["text"], suite.dump(result))


class SodaPromptOutput:
    IS_CHANGED = classmethod(providers.changed)
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "source_prompt": ("STRING", {"forceInput": True, "lazy": True}),
            "use_edited": ("BOOLEAN", {"default": False}),
            "edited_prompt": ("STRING", {"multiline": True, "default": ""}),
            "target": (target_adapter.TARGETS,),
        }, "optional": {"source_record": ("STRING", {"forceInput": True, "lazy": True}),
            "adapt_to_target": ("BOOLEAN", {"default": True, "tooltip": "将选中的原稿或手动输入适配到目标模型。首次使用当前 AI 服务；相同输入/目标/服务复用缓存。关闭后原样输出。"}),
            **controls(),
            "anima_token_budget": ("INT", {"default": 512, "min": 0, "max": 8192,
                "tooltip": "Anima 输出长度偏好，0表示不限制；不是模型硬上限。"})}}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("positive_prompt", "record_json")
    FUNCTION = "run"
    CATEGORY = "Soda/Full Prompt Suite"
    DESCRIPTION = "三个快捷按钮选择最终模型。适配开启时将原稿/手动输入整理为所选模型的提示词，相同输入缓存；positive_prompt 接生图正面文本输入。"

    def check_lazy_status(self, use_edited, source_prompt=None, source_record="", **kwargs):
        if use_edited:
            return []
        return [name for name, value in (("source_prompt", source_prompt), ("source_record", source_record)) if value is None]

    async def run(self, source_prompt, use_edited, edited_prompt, target, source_record="",
                  adapt_to_target=True, refresh=0, timeout_seconds=180, anima_token_budget=512):
        if target not in target_adapter.TARGETS:
            raise ValueError("未知目标模型。")
        source_prompt = source_prompt or ""
        prompt = edited_prompt.strip() if use_edited else source_prompt
        if not prompt.strip():
            raise ValueError("输出为空：请先反推，或填写手动修订稿。")
        source = core.parse_json(source_record) if source_record.strip() else {}
        matches = source.get("selected_prompt", source.get("faithful_prompt")) == source_prompt
        validation = dict(source.get("validation", {})) if matches and not use_edited else {
            "passed": True, "problems": [], "warnings": ["手动稿或独立输入未做槽位校验，请对照图片检查。"]}
        validation["warnings"] = list(validation.get("warnings", []))
        validation["problems"] = list(validation.get("problems", []))
        adaptation_input = prompt
        api, response, tags = None, None, []
        adapted = False
        already_adapted = (matches and not use_edited and source.get("adapted") is True
                           and source.get("ai_service", providers.default_fingerprint()) == providers.fingerprint(key_path())
                           and source.get("target") == target and source.get("target_profile_version") == target_adapter.VERSION
                           and source.get("validation", {}).get("passed") is True
                           and (target != "Anima" or source["validation"].get("budget") == anima_token_budget))
        if adapt_to_target and not already_adapted:
            response, api = await call("target_prompt_v1:" + target,
                target_adapter.messages(target, prompt, source if matches and not use_edited else {}, anima_token_budget),
                refresh, timeout_seconds, 4000)
            prompt, validation, tags = target_adapter.validate(target, response, anima_token_budget)
            adapted = True
        elif already_adapted:
            adapted = True
            tags = source.get("tags", [])
        else:
            validation["warnings"].append("目标适配关闭，当前输出为所选原稿；未自动转换模型格式。")
        if target == "Anima" and not adapted:
            tokens, _ = local.token_count(prompt)
            validation["tokens"] = tokens
            if anima_token_budget and tokens > anima_token_budget:
                validation["passed"] = False
                validation["problems"].append(f"Anima 输出超过所选 {anima_token_budget} token 预算；未截断，请手动精简。")
        result = record("prompt_output", target=target, edited=use_edited,
                        source_record=source, source_prompt=source_prompt,
                        faithful_prompt=source.get("faithful_prompt", source_prompt) if matches else adaptation_input,
                        selected_prompt=prompt, validation=validation,
                        adaptation_input=adaptation_input, adapted=adapted,
                        target_profile_version=target_adapter.VERSION, ai_service=providers.fingerprint(key_path()), api=api,
                        api_calls=1 if api and not api.get("cache_hit") else 0)
        if tags:
            result["tags"] = tags
        if response is not None:
            result["target_response"] = response
            result["omissions"] = response["omissions"]
        if matches and source.get("expanded_prompt"):
            result["expanded_prompt"] = source["expanded_prompt"]
        return (prompt, suite.dump(result))


class SodaVariant:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"record_json": ("STRING", {"forceInput": True}), "index": ("INT", {"default": 0, "min": 0, "max": 2})}}
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("selected_prompt", "record_json")
    FUNCTION = "run"
    CATEGORY = "Soda/Full Prompt Suite"

    def run(self, record_json, index):
        value = core.parse_json(record_json)
        variants = value.get("variants", [])
        if not 0 <= index < len(variants):
            raise ValueError("该档位没有这一号方案，请选择已有方案。")
        report = variants[index]
        value.update(selected_variant=index, faithful_prompt=report["text"], tags=report["tags"], validation=report)
        return (report["text"], suite.dump(value))


class SodaMetadata:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"image_path": ("STRING", {"default": "", "multiline": False})}}
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("original_prompt", "metadata_json")
    FUNCTION = "run"
    CATEGORY = "Soda/Full Prompt Suite"
    DESCRIPTION = "本机只读 PNG 元数据，不发送给 Flash；复杂连接图不臆测最终编码值。"

    @classmethod
    def IS_CHANGED(cls, image_path):
        path = Path(image_path)
        return (path.stat().st_mtime_ns, path.stat().st_size) if path.is_file() else image_path

    def run(self, image_path):
        value = local.metadata(image_path)
        return (value["positive"], suite.dump(value))


class SodaSuiteRecord:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"record_json": ("STRING", {"forceInput": True}), "save": ("BOOLEAN", {"default": True})}}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("status",)
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = "Soda/Full Prompt Suite"

    def run(self, record_json, save):
        value = core.parse_json(record_json)
        validation = value.get("validation", {})
        status = "结构校验通过；不代表看图完全正确" if validation.get("passed") else "需要检查：" + "; ".join(validation.get("problems", ["未知状态"]))
        if validation.get("passed") and validation.get("scope") == "nonempty_text":
            status = "已返回提示词；请对照参考图检查细节"
        if "tokens" in validation:
            status += "\nT5 token 数：" + str(validation["tokens"])
        if validation.get("warnings"):
            status += "\n" + "; ".join(validation["warnings"])
        if save:
            base = Path(folder_paths.get_output_directory()).resolve()
            destination = (base / "soda_prompt_workflow" / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:8])).resolve()
            if not destination.is_relative_to(base):
                raise ValueError("归档必须位于 output 内。")
            destination.mkdir(parents=True)
            (destination / "record.json").write_text(suite.dump(value), encoding="utf-8")
            for field in ("faithful_prompt", "expanded_prompt", "selected_prompt"):
                if isinstance(value.get(field), str) and value[field]:
                    (destination / (field + ".txt")).write_text(value[field], encoding="utf-8")
            status += "\n" + str(destination)
        return {"ui": {"text": [status]}, "result": (status,)}


NODE_CLASS_MAPPINGS = {cls.__name__: cls for cls in (
    SodaReferenceSuite, SodaTextSuite, SodaExpandSuite, SodaPromptOutput, SodaVariant, SodaMetadata, SodaSuiteRecord)}
NODE_DISPLAY_NAME_MAPPINGS = {
    "SodaReferenceSuite": "Soda · 完整反推 / 来源切换",
    "SodaTextSuite": "Soda · Anima 文字创作",
    "SodaExpandSuite": "Soda · K2 / DFlow 扩写开关",
    "SodaPromptOutput": "Soda · 目标适配 / 提示词输出",
    "SodaVariant": "Soda · 选择创作方案",
    "SodaMetadata": "Soda · 本地读取原图提示词",
    "SodaSuiteRecord": "Soda · 校验状态与完整归档",
}
