import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

async function settings(body) {
    const response = await api.fetchApi("/soda/ai-services", body ? {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
    } : undefined);
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || "无法读取 AI 服务配置；更新插件后请重启 ComfyUI。");
    return value;
}

async function openSettings() {
    let data;
    try { data = await settings(); }
    catch (error) { app.ui.dialog.show(error.message); return; }
    const dialog = document.createElement("dialog");
    dialog.style.cssText = "width:min(660px,90vw);box-sizing:border-box;background:#202126;color:#eee;border:1px solid #666;border-radius:10px;padding:20px";
    const add = (tag, text) => {
        const element = document.createElement(tag);
        element.textContent = text || "";
        dialog.append(element);
        return element;
    };
    add("h2", "AI 服务配置");
    add("p", "所有 Soda 反推、改写、扩写与最终适配共用当前服务。保存多套配置后，用下拉框切换并启用。TIPO 本地推理不受影响。");
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
    const vision = field("看图模型（图片反推；需要支持图片输入）");
    const key = field("API Key（地址不变时留空保留；更换地址需重填）", "password");
    key.autocomplete = "new-password";
    const strict = field("严格核对返回模型名称", "checkbox");
    strict.style.width = "auto";
    const status = add("p");
    let current = data.active;
    select.value = current;
    const showProfile = () => {
        const profile = data.profiles[current];
        base.value = profile.base_url;
        text.value = profile.text_model;
        vision.value = profile.vision_model;
        key.value = profile.api_key || "";
        key.placeholder = profile.key_configured ? "已配置；输入新密钥可替换" : "尚未配置";
        strict.checked = profile.strict_model;
        status.textContent = "地址使用 OpenAI 兼容接口的 Base URL，也可填写完整 /chat/completions 地址。密钥只保存在本机，不写入工作流。";
    };
    const remember = () => Object.assign(data.profiles[current], {
        base_url: base.value, text_model: text.value, vision_model: vision.value,
        api_key: key.value, strict_model: strict.checked,
    });
    select.onchange = () => { remember(); current = select.value; showProfile(); };
    const save = add("button", "保存并启用当前服务");
    save.style.cssText = "padding:10px 18px;background:#315c96;color:#fff;border:0;border-radius:5px;cursor:pointer";
    save.onclick = async () => {
        remember();
        save.disabled = true;
        try {
            data.active = current;
            data = await settings(data);
            key.value = "";
            window.dispatchEvent(new CustomEvent("soda-ai-service-changed", {detail: data}));
            app.graph?.change?.();
            status.textContent = `已启用 ${data.profiles[current].name}；配置已保存，尚未测试服务。下次运行使用此服务，保存配置本身不收费。`;
        } catch (error) { status.textContent = error.message; }
        finally { save.disabled = false; }
    };
    const close = add("button", "关闭");
    close.style.cssText = "padding:10px 18px;margin-left:10px;cursor:pointer";
    close.onclick = () => dialog.close();
    showProfile();
    document.body.append(dialog);
    dialog.addEventListener("close", () => { key.value = ""; dialog.remove(); }, {once: true});
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
