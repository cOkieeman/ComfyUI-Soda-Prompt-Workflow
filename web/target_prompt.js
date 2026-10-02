import { app } from "../../scripts/app.js";

const targets = [
    ["Anima", "标签＋英文短描述"],
    ["Krea2", "英文自然语言"],
    ["Qwen2.1", "中文自然语言"],
];

app.registerExtension({
    name: "Soda.TargetPromptQuickPick",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "SodaPromptOutput") return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments);
            const target = this.widgets.find(widget => widget.name === "target");
            const adapt = this.widgets.find(widget => widget.name === "adapt_to_target");
            if (!target || !adapt) return result;
            const container = document.createElement("div");
            container.dataset.sodaTargetPrompt = "true";
            container.style.cssText = "width:100%;box-sizing:border-box;padding:7px;color:#eee;font-size:12px";
            const row = document.createElement("div");
            row.style.cssText = "display:flex;gap:6px";
            container.append(row);
            const status = document.createElement("div");
            status.style.cssText = "padding-top:6px;line-height:18px";
            container.append(status);
            const buttons = targets.map(([value]) => {
                const button = document.createElement("button");
                button.type = "button";
                button.textContent = value === "Qwen2.1" ? "Qwen 2.1" : value;
                button.title = `选择 ${value}；运行工作流时适配最终提示词`;
                button.style.cssText = "flex:1;padding:7px 3px;border:1px solid #626977;border-radius:5px;color:#eee;cursor:pointer";
                button.onclick = () => {
                    target.value = value;
                    adapt.value = true;
                    target.callback?.(value);
                    adapt.callback?.(true);
                    this.sodaUpdateTargetButtons();
                    this.graph?.change?.();
                };
                row.append(button);
                return button;
            });
            this.sodaUpdateTargetButtons = () => {
                targets.forEach(([value], index) => {
                    const active = target.value === value && adapt.value;
                    buttons[index].style.background = active ? "#315c96" : "#282b33";
                    buttons[index].setAttribute("aria-pressed", String(active));
                });
                const profile = targets.find(([value]) => value === target.value)?.[1] || "";
                status.textContent = adapt.value ? `最终输出：${target.value} · ${profile}（首次调用当前 AI，重复用缓存）`
                    : "目标适配关闭：原样输出。点击上方按钮可开启。";
                this.setDirtyCanvas(true, true);
            };
            for (const widget of [target, adapt]) {
                const callback = widget.callback;
                widget.callback = (...args) => {
                    const result = callback?.apply(widget, args);
                    this.sodaUpdateTargetButtons();
                    return result;
                };
            }
            const widget = this.addDOMWidget("soda_target_quick_pick", "div", container, {serialize: false});
            widget.serialize = false;
            widget.computeSize = width => [width, 70];
            this.sodaUpdateTargetButtons();
            this.setSize([this.size[0], Math.max(this.size[1], this.computeSize()[1])]);
            return result;
        };
        const configure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (info) {
            const result = configure?.apply(this, arguments);
            // Saved files keep the original native order, even when the host migrates converted inputs.
            ["use_edited", "edited_prompt", "target", "adapt_to_target", "refresh", "timeout_seconds", "anima_token_budget"]
                .forEach((name, index) => {
                    const widget = this.widgets?.find(widget => widget.name === name);
                    if (!widget) return;
                    if (Object.hasOwn(info.widgets_values_named || {}, name)) widget.value = info.widgets_values_named[name];
                    else if (info.widgets_values?.[index] !== undefined) widget.value = info.widgets_values[index];
                });
            this.sodaUpdateTargetButtons?.();
            return result;
        };
    },
});
