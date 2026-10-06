import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

async function settings(body, action = "") {
    const response = await api.fetchApi(`/soda/ai-services${action}`, body ? {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
    } : undefined);
    let value;
    try { value = await response.json(); }
    catch { throw new Error("接口未返回有效 JSON；更新插件后请重启 ComfyUI，并检查服务地址。"); }
    if (!response.ok) throw new Error(value?.error || "无法读取 AI 服务配置；更新插件后请重启 ComfyUI。");
    return value;
}

async function openSettings() {
    let data;
    try { data = await settings(); }
    catch (error) { app.ui.dialog.show(error.message); return; }
    const dialog = document.createElement("dialog");
    dialog.style.cssText = "width:min(660px,90vw);max-height:90vh;overflow:auto;box-sizing:border-box;background:#202126;color:#eee;border:1px solid #666;border-radius:10px;padding:20px";
    const add = (tag, text) => {
        const element = document.createElement(tag);
        element.textContent = text || "";
        dialog.append(element);
        return element;
    };
    add("h2", "AI 服务配置");
    add("p", "远程反推、改写、扩写与最终适配共用当前服务。保存多套配置后，用下拉框切换并启用。本地 Qwen 看图和 TIPO 不受影响；⑤最终适配开启时仍调用此服务。");
    const field = (name, type = "text") => {
        const label = add("label", name);
        label.style.cssText = "display:block;margin:10px 0 4px";
        const input = add(type === "select" ? "select" : "input");
        input.setAttribute("aria-label", name);
        if (type !== "select") input.type = type;
        input.style.cssText = "width:100%;box-sizing:border-box;padding:9px;background:#17181d;color:#eee;border:1px solid #555;border-radius:4px";
        return input;
    };
    const select = field("AI 服务", "select");
    for (const [id, profile] of Object.entries(data.profiles)) {
        const option = document.createElement("option");
        option.value = id;
        option.textContent = profile.name;
        select.append(option);
    }
    const base = field("Base URL / 服务地址");
    const text = field("文字模型（改写、扩写、最终适配）");
    const textChoices = field("已拉取模型 → 文字模型", "select");
    const vision = field("看图模型（图片反推；需要支持图片输入）");
    const visionChoices = field("已拉取模型 → 看图模型", "select");
    const choiceLabels = [textChoices, visionChoices].map(input => dialog.children[Array.from(dialog.children).indexOf(input) - 1]);
    const clearModels = () => {
        for (const input of [textChoices, visionChoices]) { input.replaceChildren(); input.hidden = true; }
        for (const label of choiceLabels) { label.hidden = true; label.style.display = "none"; }
    };
    textChoices.onchange = () => { if (textChoices.value) { text.value = textChoices.value; text.oninput(); } };
    visionChoices.onchange = () => { if (visionChoices.value) { vision.value = visionChoices.value; vision.oninput(); } };
    const key = field("API Key（地址不变时留空保留；更换地址需重填）", "password");
    key.autocomplete = "new-password";
    const strict = field("严格核对返回模型名称", "checkbox");
    strict.style.width = "auto";
    const status = add("p");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    status.style.cssText = "line-height:1.5;overflow-wrap:anywhere";
    add("p", "拉取模型读取服务的 /models 列表，仍可手动填写。列表不保证每个模型都支持看图。测试会实际调用所选模型，可能产生少量费用；看图测试发送一张内置小样图。操作使用当前填写的配置，不会自动保存或启用。");
    let current = data.active;
    let closed = false;
    const profileValues = () => ({
        base_url: base.value, text_model: text.value, vision_model: vision.value,
        api_key: key.value, strict_model: strict.checked,
    });
    select.value = current;
    const showProfile = () => {
        const profile = data.profiles[current];
        base.value = profile.base_url;
        text.value = profile.text_model;
        vision.value = profile.vision_model;
        key.value = profile.api_key || "";
        key.placeholder = profile.key_configured ? "已配置；输入新密钥可替换" : "尚未配置";
        strict.checked = profile.strict_model;
        clearModels();
        status.textContent = "地址使用 OpenAI 兼容接口的 Base URL，也可填写完整 /chat/completions 地址。密钥只保存在本机，不写入工作流。";
    };
    const remember = () => Object.assign(data.profiles[current], profileValues());
    select.onchange = () => { remember(); current = select.value; showProfile(); };
    const changed = () => { status.textContent = "配置已修改，请重新测试；保存并启用后才用于工作流。"; };
    base.oninput = key.oninput = () => { clearModels(); changed(); };
    text.oninput = vision.oninput = strict.onchange = changed;
    const pull = add("button", "拉取模型");
    const testText = add("button", "测试文字模型");
    const testVision = add("button", "测试看图模型");
    const save = add("button", "保存并启用当前服务");
    for (const button of [pull, testText, testVision, save]) {
        button.type = "button";
        button.style.cssText = "padding:10px 14px;margin:4px 6px 4px 0;cursor:pointer";
    }
    save.style.cssText = "padding:10px 18px;background:#315c96;color:#fff;border:0;border-radius:5px;cursor:pointer";
    const busy = value => {
        for (const input of [select, base, text, vision, textChoices, visionChoices, key, strict, pull, testText, testVision, save]) input.disabled = value;
    };
    const runProbe = async (action, modelType) => {
        const body = {service: current, profile: profileValues()};
        if (modelType) body.model_type = modelType;
        busy(true);
        status.textContent = modelType ? "正在测试模型，请稍候（最多 30 秒）…" : "正在拉取模型，请稍候（最多 20 秒）…";
        try {
            const result = await settings(body, action);
            if (closed) return;
            if (!modelType) {
                if (!Array.isArray(result?.models) || !result.models.length || !result.models.every(model => typeof model === "string" && model.trim())) {
                    throw new Error("服务未返回可用模型，仍可手动填写模型名称。");
                }
                for (const [input, target] of [[textChoices, text], [visionChoices, vision]]) {
                    input.replaceChildren();
                    for (const model of ["", ...result.models]) {
                        const option = document.createElement("option");
                        option.value = model;
                        option.textContent = model || "选择模型（仍可手动填写）";
                        input.append(option);
                    }
                    input.value = result.models.includes(target.value) ? target.value : "";
                    input.hidden = false;
                }
                for (const label of choiceLabels) { label.hidden = false; label.style.display = "block"; }
                status.textContent = `已拉取 ${result.models.length} 个模型；请选择填入文字/看图模型，或继续手动输入。配置尚未保存。`;
            } else {
                if (result?.ok !== true) throw new Error("服务未返回有效的测试结果，请检查配置后重试。");
                status.textContent = `${modelType === "vision" ? "看图" : "文字"}模型测试通过 · ${result.elapsed_ms} ms · 返回模型：${result.response_model || "未提供标识"}。${result.warning || ""} 配置尚未保存；修改后需重新测试。`;
            }
        } catch (error) { if (!closed) status.textContent = error.message; }
        finally { busy(false); }
    };
    pull.onclick = () => runProbe("/models");
    testText.onclick = () => runProbe("/test", "text");
    testVision.onclick = () => runProbe("/test", "vision");
    save.onclick = async () => {
        remember();
        busy(true);
        try {
            data.active = current;
            data = await settings(data);
            key.value = "";
            window.dispatchEvent(new CustomEvent("soda-ai-service-changed", {detail: data}));
            app.graph?.change?.();
            if (closed) return;
            status.textContent = `已启用 ${data.profiles[current].name}；下次运行使用此服务。保存本身不调用模型，测试结果以测试时的配置为准。`;
        } catch (error) { if (!closed) status.textContent = error.message; }
        finally { busy(false); }
    };
    const close = add("button", "关闭");
    close.style.cssText = "padding:10px 18px;margin-left:10px;cursor:pointer";
    close.onclick = () => dialog.close();
    showProfile();
    document.body.append(dialog);
    dialog.addEventListener("close", () => {
        closed = true;
        key.value = "";
        for (const profile of Object.values(data.profiles)) delete profile.api_key;
        dialog.remove();
    }, {once: true});
    dialog.showModal();
}

app.registerExtension({
    name: "Soda.AIServiceSettings",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (!["SodaUnifiedSource", "SodaPromptOutput"].includes(nodeData.name)) return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function (...args) {
            const result = created?.apply(this, args);
            const container = document.createElement("div");
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = "AI 服务配置 / 切换模型";
            button.style.cssText = "width:100%;padding:8px;background:#343840;color:#eee;border:1px solid #666;border-radius:5px;cursor:pointer";
            button.onclick = openSettings;
            container.append(button);
            const status = document.createElement("div");
            status.style.cssText = "padding:4px;color:#c9cbd1;font-size:12px;line-height:18px;overflow-wrap:anywhere";
            container.append(status);
            const update = data => {
                const profile = data.profiles[data.active];
                status.textContent = `${profile.name} · 文字：${profile.text_model || "未配置"} · 看图：${profile.vision_model || "未配置"}`;
            };
            settings().then(update).catch(() => { status.textContent = "配置入口未就绪，更新后请重启 ComfyUI。"; });
            const listener = event => update(event.detail);
            window.addEventListener("soda-ai-service-changed", listener);
            const removed = this.onRemoved;
            this.onRemoved = function (...values) {
                window.removeEventListener("soda-ai-service-changed", listener);
                return removed?.apply(this, values);
            };
            const widget = this.addDOMWidget("soda_ai_services", "div", container, {serialize: false});
            widget.serialize = false;
            widget.computeSize = width => [width, 75];
            this.setSize([this.size[0], Math.max(this.size[1], this.computeSize()[1])]);
            return result;
        };
    },
});
