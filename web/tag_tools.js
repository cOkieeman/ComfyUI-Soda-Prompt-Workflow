import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

async function request(path, body) {
    const response = await api.fetchApi(`/soda/tags/${path}`, body === undefined ? undefined : {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "标签入口未就绪，更新后请重启 ComfyUI。");
    return data;
}

function modal(title) {
    const dialog = document.createElement("dialog");
    dialog.style.cssText = "width:min(840px,92vw);max-height:88vh;overflow:auto;box-sizing:border-box;background:#202126;color:#eee;border:1px solid #666;border-radius:10px;padding:20px";
    const add = (tag, text = "", parent = dialog) => {
        const element = document.createElement(tag);
        element.textContent = text;
        parent.append(element);
        return element;
    };
    add("h2", title);
    const close = add("button", "关闭");
    close.onclick = () => dialog.close();
    const show = () => {
        document.body.append(dialog);
        dialog.addEventListener("close", () => dialog.remove(), {once: true});
        dialog.showModal();
    };
    return {dialog, add, show};
}

function field(add, label, value = "", multiline = false, parent) {
    const text = add("label", label, parent);
    text.style.cssText = "display:block;margin:10px 0 4px;font-weight:600";
    const input = add(multiline ? "textarea" : "input", "", parent);
    input.value = value;
    input.setAttribute("aria-label", label);
    input.style.cssText = "box-sizing:border-box;width:100%;padding:8px;background:#15171b;color:#eee;border:1px solid #555;border-radius:4px";
    if (multiline) input.rows = 3;
    return input;
}

function ancestors(node) {
    const nodes = [], queue = [node], seen = new Set();
    while (queue.length && seen.size < 32) {
        const current = queue.shift();
        if (!current || seen.has(current.id)) continue;
        seen.add(current.id); nodes.push(current);
        for (const input of current.inputs || []) {
            const link = app.graph?.links?.[input.link];
            if (link) queue.push(app.graph.getNodeById(link.origin_id));
        }
    }
    return nodes;
}

function widget(node, name) { return node?.widgets?.find(w => w.name === name); }
function signature(node) {
    return JSON.stringify(ancestors(node).filter(n => n !== node).map(n => [n.id,
        (n.widgets || []).filter(w => w.serialize !== false && w.options?.serialize !== false).map(w => [w.name, w.value])]));
}

async function preview(node) {
    const chain = ancestors(node);
    const source = chain.find(n => n.type === "SodaUnifiedSource");
    const process = chain.find(n => n.type === "SodaUnifiedProcess");
    if (widget(source, "source")?.value === "作者画廊" && widget(process, "action")?.value === "使用素材原提示词") {
        const input = source.inputs?.find(i => i.name === "gallery_text");
        const link = app.graph?.links?.[input?.link];
        const gallery = link && app.graph.getNodeById(link.origin_id);
        const selections = JSON.parse(widget(gallery, "selection_data")?.value || "{}").selections || [];
        if (!selections.length) throw new Error("请先在作者画廊选择一张图片。");
        if (selections.length > 1) throw new Error("分类取舍作用于本次所有图片。请一次选择一张图片后编辑，避免误改其他图片。");
        const selected = selections[0];
        return request("preview", {site: selected.source_site || "danbooru", post_id: selected.post_id, prompt: selected.prompt || ""});
    }
    if (node.sodaTagPreview && node.sodaTagSignature === signature(node)) return node.sodaTagPreview;
    throw new Error("当前入口需要先运行一次，才能查看实际反推标签。自然语言输入会原样通过标签整理。");
}

async function openCategories(node) {
    let data, config;
    try { [data, config] = await Promise.all([preview(node), request("settings")]); }
    catch (error) { app.ui.dialog.show(error.message); return; }
    const {dialog, add, show} = modal("分类标签 / 取舍与映射");
    add("p", data.message);
    add("p", "取消类别勾选即可舍弃该类；改动标签后旧英文描述会移除，⑤将根据新标签重新适配。此处保存取舍和映射均不调用 AI。");
    const original = field(add, data.complete ? "完整原标签（保留供核对）" : "画廊当前原标签（尚未取得完整网站标签）", data.original_tags.join(", "), true);
    original.readOnly = true;
    const included = new Set(JSON.parse(widget(node, "include_categories").value));
    const edits = JSON.parse(widget(node, "category_edits").value);
    add("p", "类别取舍和编辑会随工作流保留。更换素材后若要取消之前的改动，点击恢复全部原标签。" );
    const status = add("p");
    const reset = add("button", "恢复全部原标签");
    const apply = add("button", "应用分类取舍");
    const grid = add("div");
    grid.style.cssText = "display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px";
    const rows = {};
    for (const [key, name] of Object.entries(config.categories)) {
        const section = add("section", "", grid);
        section.style.cssText = "margin:6px 0;padding:10px;border:1px solid #4d5058;border-radius:6px";
        const label = add("label", `${name} · ${data.groups[key].length} 个`, section);
        const check = document.createElement("input");
        check.type = "checkbox"; check.checked = included.has(key);
        check.setAttribute("aria-label", `保留${name}`);
        label.prepend(check);
        const input = add("textarea", "", section);
        input.value = edits[key] ?? data.groups[key].join(", ");
        input.setAttribute("aria-label", `编辑${name}标签`);
        input.style.cssText = "width:100%;box-sizing:border-box;margin-top:6px;padding:6px;background:#15171b;color:#eee;border:1px solid #555;border-radius:4px";
        input.rows = Math.min(4, Math.max(1, Math.ceil(input.value.length / 80)));
        rows[key] = {check, input, initial: data.groups[key].join(", ")};
    }
    reset.onclick = () => {
        for (const row of Object.values(rows)) { row.check.checked = true; row.input.value = row.initial; }
        status.textContent = "已恢复；点击应用分类取舍后生效。";
    };
    apply.onclick = () => {
        const keep = [], replacement = {};
        for (const [key, row] of Object.entries(rows)) {
            if (row.check.checked) keep.push(key);
            if (row.input.value.trim() !== row.initial) replacement[key] = row.input.value.trim();
        }
        if (!keep.length) { status.textContent = "请至少保留一类。"; return; }
        widget(node, "include_categories").value = JSON.stringify(keep);
        widget(node, "category_edits").value = JSON.stringify(replacement);
        widget(node, "enabled").value = true;
        node.sodaUpdateTagStatus?.();
        app.graph.change(); node.setDirtyCanvas(true, true);
        status.textContent = "取舍已应用。下一次运行后，最终提示词使用这些标签；分类输出端口仍保留输入分类供核对。";
    };
    add("h3", "保存单个标签的类别映射");
    const tag = field(add, "需要映射的标签", data.groups.unknown[0] || "");
    const category = add("select"); category.setAttribute("aria-label", "映射到类别");
    for (const [key, name] of Object.entries(config.categories)) {
        const option = add("option", name, category); option.value = key;
    }
    const saveMap = add("button", "保存标签映射");
    saveMap.onclick = async () => {
        saveMap.disabled = true;
        try {
            await request("settings", {mappings: {[tag.value.trim()]: category.value}});
            node.sodaTagSignature = null;
            app.graph.change();
            status.textContent = "映射已保存在本机；重新打开分类窗口或运行后生效。其他工作流也会使用此映射。";
        } catch (error) { status.textContent = error.message; }
        finally { saveMap.disabled = false; }
    };
    show();
}

async function openSearch() {
    let config;
    try { config = await request("settings"); }
    catch (error) { app.ui.dialog.show(error.message); return; }
    const {add, show} = modal("本地中文语义搜索标签");
    add("p", "BGE-M3 把中文描述匹配为网站标签候选。搜索使用 CPU，不占用绘图显存，不调用外部 AI。首次加载较慢；不会自动下载模型。候选不代表图片里已确认存在的内容。");
    const directory = field(add, "语义资源目录", config.resource_directory);
    add("p", "填写包含 models/bge-m3 和 tags_embedding 的资源根目录。保存路径本身不加载模型。");
    const status = add("p");
    const save = add("button", "保存资源目录");
    save.onclick = async () => {
        try { await request("settings", {resource_directory: directory.value}); status.textContent = "资源目录已保存；点击搜索才加载。"; }
        catch (error) { status.textContent = error.message; }
    };
    const query = field(add, "中文或英文描述", "");
    const search = add("button", "搜索本地标签");
    const results = add("div");
    const output = field(add, "选中的候选标签", "", true);
    output.readOnly = true;
    const copy = add("button", "复制所选标签");
    copy.onclick = async () => {
        try { await navigator.clipboard.writeText(output.value); status.textContent = "已复制，可粘贴到画廊 Tags 搜索框或分类标签编辑框。"; }
        catch { output.select(); status.textContent = "请复制已选中的文本。"; }
    };
    search.onclick = async () => {
        search.disabled = true; results.replaceChildren(); output.value = "";
        status.textContent = "正在本机加载 / 搜索 BGE-M3，首次可能需要一段时间……";
        const selected = new Set();
        try {
            const data = await request("search", {query: query.value, top_k: 12});
            for (const result of data.tags) {
                const button = add("button", `${result.tag} · ${result.translation} · 匹配分 ${result.score}`, results);
                button.style.cssText = "display:block;margin:6px 0;padding:8px;max-width:100%;text-align:left";
                button.onclick = () => {
                    if (selected.has(result.tag)) selected.delete(result.tag); else selected.add(result.tag);
                    button.style.outline = selected.has(result.tag) ? "2px solid #9a89ef" : "";
                    output.value = [...selected].join(", ");
                };
            }
            status.textContent = "点击候选标签选择，再复制使用。画廊搜索时仍需遵守网站标签数量限制。";
        } catch (error) { status.textContent = error.message; }
        finally { search.disabled = false; }
    };
    show();
}

app.registerExtension({
    name: "Soda.TagTools",
    beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "SodaTagOrganizer") return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function (...args) {
            const result = created?.apply(this, args);
            const container = document.createElement("div");
            const status = document.createElement("div");
            status.textContent = "全部类别默认保留；分类和映射不调用 AI。";
            this.sodaUpdateTagStatus = () => {
                const keep = JSON.parse(widget(this, "include_categories").value);
                const edits = JSON.parse(widget(this, "category_edits").value);
                status.textContent = widget(this, "enabled").value
                    ? `保留 ${keep.length}/13 类 · 编辑 ${Object.keys(edits).length} 类；下一次运行生效。`
                    : "标签整理关闭，原稿直接通过。";
            };
            const configured = this.onConfigure;
            this.onConfigure = function (...values) {
                const output = configured?.apply(this, values);
                this.sodaTagPreview = null;
                this.sodaUpdateTagStatus();
                return output;
            };
            for (const [text, action] of [["分类标签 / 取舍与映射", () => openCategories(this)], ["中文语义搜索标签（本地 BGE-M3）", openSearch]]) {
                const button = document.createElement("button");
                button.textContent = text;
                button.style.cssText = "width:100%;margin:4px 0;padding:9px;color:#eee;background:#343840;border:1px solid #666;border-radius:5px;cursor:pointer";
                button.onclick = action; container.append(button);
            }
            container.append(status);
            const control = this.addDOMWidget("soda_tag_tools", "div", container, {serialize: false});
            control.serialize = false; control.computeSize = width => [width, 120];
            const executed = this.onExecuted;
            this.onExecuted = function (message) {
                const output = executed?.call(this, message);
                if (message.tag_preview?.[0]) {
                    this.sodaTagPreview = message.tag_preview[0];
                    this.sodaTagSignature = signature(this);
                    status.textContent = this.sodaTagPreview.message;
                }
                return output;
            };
            this.setSize([Math.max(this.size[0], 460), Math.max(this.size[1], this.computeSize()[1])]);
            return result;
        };
    },
});
