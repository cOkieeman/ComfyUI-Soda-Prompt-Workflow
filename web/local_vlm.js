import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const ROUTE = "Qwen3.5 · 本地看图反推";
async function settings(body) {
    const response = await api.fetchApi("/soda/local-vlm", body ? {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
    } : undefined);
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || "本地反推配置不可用，更新后请重启 ComfyUI。");
    return value;
}

async function configure(node) {
    let data;
    try { data = await settings(); }
    catch (error) { app.ui.dialog.show(error.message); return; }
    const dialog = document.createElement("dialog");
    dialog.style.cssText = "width:min(720px,92vw);max-height:90vh;overflow:auto;box-sizing:border-box;background:#202126;color:#eee;border:1px solid #666;border-radius:10px;padding:20px";
    const add = (tag, text) => {
        const element = document.createElement(tag);
        element.textContent = text || "";
        dialog.append(element);
        return element;
    };
    add("h2", "本地 Qwen3.5 看图反推");
    add("p", "②在本机输出候选标签＋英文短描述，可继续分类整理。③OC、④远程扩写、⑤最终适配开启时仍使用 AI 服务。关闭⑤适配可直接使用本地反推稿。");
    const inputs = {};
    for (const [key, label, type] of [
        ["model_path", "Qwen3.5 主模型 GGUF 完整路径", "text"],
        ["mmproj_path", "配套投影模型 GGUF 完整路径", "text"],
        ["runtime_directory", "独立 llama-cpp 运行库目录（留空使用当前环境）", "text"],
        ["device", "设备", "select"],
        ["context_size", "上下文 token 数", "number"],
        ["max_tokens", "最大输出 token 数", "number"],
        ["seed", "种子", "number"],
    ]) {
        const heading = add("label", label);
        heading.style.cssText = "display:block;margin:10px 0 4px";
        const input = add(type === "select" ? "select" : "input");
        input.setAttribute("aria-label", label);
        input.style.cssText = "width:100%;box-sizing:border-box;padding:8px;background:#17181d;color:#eee;border:1px solid #555;border-radius:4px";
        if (type === "select") {
            for (const [value, text] of [["auto", "自动：显存足够用 GPU，否则 CPU"], ["cuda", "GPU / CUDA"], ["cpu", "CPU（较慢）"]]) {
                const option = document.createElement("option");
                option.value = value; option.textContent = text; input.append(option);
            }
        } else input.type = type;
        input.value = data.config[key];
        inputs[key] = input;
    }
    add("p", "只读取已有本机文件，不自动下载。运行时独立加载模型，结束或取消后释放；自动模式不会卸载其他程序的模型。路径就绪只表示文件存在，不代表已完成推理测试。");
    const status = add("p", data.paths_ready ? "模型文件路径已就绪。" : "请填写并保存两个模型路径。");
    const save = add("button", "保存配置");
    save.onclick = async () => {
        save.disabled = true;
        const body = Object.fromEntries(Object.entries(inputs).map(([key, input]) => [key, input.type === "number" ? Number(input.value) : input.value]));
        try {
            data = await settings(body);
            window.dispatchEvent(new CustomEvent("soda-local-vlm-changed", {detail: data}));
            app.graph?.change?.();
            status.textContent = "配置已保存；保存本身不运行模型。选择本地反推路线后，点击运行开始。";
        } catch (error) { status.textContent = error.message; }
        finally { save.disabled = false; }
    };
    const use = add("button", "使用本地 Qwen 反推");
    use.onclick = () => {
        const route = node.widgets?.find(w => w.name === "route");
        if (!route) return;
        route.value = ROUTE; route.callback?.(ROUTE);
        const action = node.widgets.find(w => w.name === "action");
        if (action) { action.value = "重新反推"; action.callback?.(action.value); }
        const timeout = node.widgets.find(w => w.name === "timeout_seconds");
        if (timeout) { timeout.value = 600; timeout.callback?.(600); }
        app.graph?.change?.(); app.graph?.setDirtyCanvas?.(true, true);
        status.textContent = "已选择本地反推，超时设为600秒。①需要选中图片；请先保存修改后的配置，再点击运行。";
    };
    const close = add("button", "关闭");
    close.onclick = () => dialog.close();
    for (const button of [save, use, close]) button.style.cssText = "padding:10px;margin:5px 8px 0 0;cursor:pointer";
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove(), {once: true});
    dialog.showModal();
}

app.registerExtension({
    name: "Soda.LocalVision",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (!["SodaUnifiedProcess", "SodaMaterialPrompt", "SodaReferenceSuite"].includes(nodeData.name)) return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function (...args) {
            const result = created?.apply(this, args);
            const container = document.createElement("div");
            const button = document.createElement("button");
            button.textContent = "本地 Qwen3.5 配置 / 使用";
            button.style.cssText = "width:100%;padding:8px;background:#343840;color:#eee;border:1px solid #666;border-radius:5px;cursor:pointer";
            button.onclick = () => configure(this);
            container.append(button);
            const status = document.createElement("div");
            status.style.cssText = "font-size:12px;line-height:18px;color:#c9cbd1;padding:4px";
            container.append(status);
            const update = data => { status.textContent = data.paths_ready ? "本地模型文件就绪 · ⑤适配仍按 AI 服务配置执行" : "本地反推尚未配置模型路径"; };
            settings().then(update).catch(() => { status.textContent = "配置入口未就绪，更新后请重启 ComfyUI。"; });
            const listener = event => update(event.detail);
            window.addEventListener("soda-local-vlm-changed", listener);
            const removed = this.onRemoved;
            this.onRemoved = function (...values) {
                window.removeEventListener("soda-local-vlm-changed", listener);
                return removed?.apply(this, values);
            };
            const widget = this.addDOMWidget("soda_local_vlm", "div", container, {serialize: false});
            widget.serialize = false;
            widget.computeSize = width => [width, 75];
            this.setSize([this.size[0], Math.max(this.size[1], this.computeSize()[1])]);
            return result;
        };
    },
});
