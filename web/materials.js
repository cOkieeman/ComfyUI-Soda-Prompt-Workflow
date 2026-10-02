import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

function element(tag, text, parent) {
    const el = document.createElement(tag);
    if (text) el.textContent = text;
    parent?.append(el);
    return el;
}

function addMaterialPreview(node) {
    const container = element("div");
    container.dataset.sodaMaterialPreview = "true";
    container.style.cssText = "width:100%;box-sizing:border-box;padding:8px;background:#202126;border-radius:6px;color:#eee;overflow:hidden";
    const image = element("img", "", container);
    image.style.cssText = "width:100%;height:180px;object-fit:contain;background:#17181d";
    const status = element("div", "", container);
    status.style.cssText = "font-size:12px;line-height:18px;overflow-wrap:anywhere;padding-top:5px";
    const tags = element("textarea", "", container);
    tags.readOnly = true;
    tags.setAttribute("aria-label", "所选画廊图片的网站标签");
    tags.style.cssText = "box-sizing:border-box;width:100%;height:140px;margin-top:6px;padding:6px;background:#17181d;color:#ddd;border:1px solid #555;resize:none";
    let height = 0, revision = 0;
    const widget = node.addDOMWidget("soda_material_preview", "div", container, {serialize: false});
    widget.serialize = false;
    widget.computeSize = width => [width, height];
    const resize = () => {
        node.setSize([node.size[0], Math.max(node.size[1], node.computeSize()[1])]);
        node.setDirtyCanvas(true, true);
    };
    node.sodaRefreshMaterialPreview = async card => {
        const current = ++revision;
        const source = node.widgets.find(w => w.name === "source");
        const key = node.widgets.find(w => w.name === "card_key")?.value;
        const port = Number(node.widgets.find(w => w.name === "port")?.value);
        const galleryMode = source?.value === "作者画廊";
        container.hidden = !!source && source.value !== "DFlow 素材" && !galleryMode;
        image.hidden = true;
        image.removeAttribute("src");
        tags.hidden = true;
        tags.value = "";
        height = container.hidden ? 0 : 44;
        if (source) node.title = `素材入口 · ${source.value}`;
        if (galleryMode) {
            const gallery = node.getInputNode(node.inputs?.findIndex(input => input.name === "gallery_image") ?? 0);
            const promptGallery = node.getInputNode(node.inputs?.findIndex(input => input.name === "gallery_text") ?? 1);
            const selection = gallery?.widgets?.find(input => input.name === "selection_data");
            status.textContent = "尚未选择画廊图片，请在左侧单击一张图片。";
            if (!gallery || gallery !== promptGallery) {
                status.textContent = "请将同一个画廊的 images、prompts 连接到 gallery_image、gallery_text。";
            } else if (selection) {
                try {
                    const data = JSON.parse(selection.value || "{}");
                    const selections = Array.isArray(data.selections) ? data.selections : [];
                    const selected = selections[0];
                    if (selected) {
                        const site = selected.source_site === "gelbooru" ? "Gelbooru" : "Danbooru";
                        const name = `${site} #${selected.post_id}`;
                        node.title = `素材入口 · ${name}`;
                        status.textContent = `${name} · 已选 ${selections.length} 张${selections.length > 1 ? "（预览第 1 张）" : ""} · ${selected.prompt ? "有网站标签，可在②使用原词或重新反推。" : "暂无网站标签，请在②重新反推。"}`;
                        tags.value = typeof selected.prompt === "string" ? selected.prompt : "";
                        tags.hidden = !tags.value;
                        height = tags.hidden ? 72 : 220;
                        if (/^https?:\/\//i.test(selected.image_url || "")) {
                            image.alt = name;
                            image.hidden = false;
                            height += 180;
                            image.onerror = () => {
                                if (current !== revision) return;
                                image.hidden = true;
                                height -= 180;
                                status.textContent = `${name} · 已选择，缩略图读取失败；网站标签保留，可重新选图重试。`;
                                resize();
                            };
                            image.src = api.apiURL(`/danbooru_gallery/image_proxy?url=${encodeURIComponent(selected.image_url)}`);
                        }
                    }
                } catch {
                    status.textContent = "画廊选择数据无法读取，请在左侧重新选择图片。";
                }
            }
            resize();
            return;
        }
        status.textContent = key ? "正在读取所选 DFlow 素材…" : "尚未选择 DFlow 素材。";
        resize();
        if (container.hidden || !key) return;
        try {
            if (!card || card.key !== key) {
                const response = await api.fetchApi(`/soda/materials/dflow?port=${port}`);
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || "读取素材失败");
                card = data.cards.find(item => item.key === key);
            }
            if (current !== revision) return;
            if (!card) throw new Error("所选素材已不存在，请重新选择。");
            node.title = `素材入口 · ${card.name.slice(0, 40)}`;
            status.textContent = `${card.name} · ${card.prompt ? "有原提示词" : "暂无原提示词；请在②选择‘重新反推’"}`;
            if (card.image_path) {
                image.alt = card.name;
                image.hidden = false;
                height = 230;
                image.onerror = () => {
                    if (current !== revision) return;
                    image.hidden = true;
                    height = 62;
                    status.textContent = "图片读取失败，请确认 DFlow 正在运行并重新选择素材。";
                    resize();
                };
                image.src = api.apiURL(`/soda/materials/dflow/image?port=${port}&key=${encodeURIComponent(key)}`);
            } else {
                height = 62;
                status.textContent += " · 没有图片或高清缓存未就绪。";
            }
        } catch (error) {
            if (current !== revision) return;
            height = 62;
            status.textContent = error.message;
        }
        resize();
    };
    const removed = node.onRemoved;
    node.onRemoved = function (...args) {
        revision++;
        return removed?.apply(this, args);
    };
    for (const name of ["source", "port", "card_key"]) {
        const input = node.widgets.find(w => w.name === name);
        if (!input) continue;
        const callback = input.callback;
        input.callback = function (...args) {
            const result = callback?.apply(this, args);
            node.sodaRefreshMaterialPreview();
            return result;
        };
    }
    node.sodaRefreshMaterialPreview();
}

async function chooseMaterial(node) {
    const port = node.widgets.find(w => w.name === "port").value;
    const dialog = element("dialog");
    dialog.style.cssText = "width:min(1100px,90vw);height:82vh;background:#202126;color:#eee;border:1px solid #666;border-radius:12px;padding:20px;";
    const heading = element("div", "", dialog);
    heading.style.cssText = "display:flex;align-items:center;gap:16px;margin-bottom:16px";
    element("h2", "DFlow 素材入口", heading).style.margin = "0 auto 0 0";
    const open = element("button", "打开完整瀑布流", heading);
    open.onclick = () => window.open(`http://127.0.0.1:${Number(port)}`, "_blank", "noopener");
    const close = element("button", "关闭", heading);
    close.onclick = () => dialog.close();
    const filters = element("div", "", dialog);
    filters.style.cssText = "display:flex;gap:12px;margin-bottom:14px";
    const folder = element("select", "", filters);
    for (const [value, label] of [["all", "全部素材"], ["original", "原始收藏"], ["pending", "待反推"], ["worded", "有词区"], ["completed", "已完成"], ["metadata", "元数据库"]]) {
        const option = element("option", label, folder);
        option.value = value;
    }
    const search = element("input", "", filters);
    search.placeholder = "搜索名称、来源或提示词";
    search.style.flex = "1";
    const refresh = element("button", "刷新素材", filters);
    const status = element("p", "正在读取本机 DFlow…", dialog);
    const grid = element("div", "", dialog);
    grid.style.cssText = "display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:14px;align-items:start";
    const more = element("button", "加载更多", dialog);
    more.style.marginTop = "18px";
    let cards = [], limit = 50;
    const render = () => {
        const query = search.value.trim().toLowerCase();
        const found = cards.filter(c => (folder.value === "all" || c.folder === folder.value) &&
            `${c.name} ${c.prompt} ${c.metadata?.source || ""}`.toLowerCase().includes(query));
        grid.replaceChildren();
        status.textContent = `${found.length} 张素材。选择卡片后回到工作流，可用原词或重新反推。未缓存的图片需先在 DFlow 完成缓存。`;
        for (const card of found.slice(0, limit)) {
            const button = element("button", "", grid);
            button.style.cssText = "text-align:left;white-space:normal;background:#2b2d33;border:1px solid #555;border-radius:8px;overflow:hidden;padding:10px;color:inherit;cursor:pointer";
            if (card.image_path) {
                const img = element("img", "", button);
                img.loading = "lazy";
                img.alt = card.name;
                img.style.cssText = "width:100%;height:150px;object-fit:contain;background:#17181d";
                img.src = api.apiURL(`/soda/materials/dflow/image?port=${Number(port)}&key=${encodeURIComponent(card.key)}`);
            } else {
                element("p", "纯文字 / 图片缓存未就绪", button);
            }
            element("strong", card.name.slice(0, 75), button);
            element("p", `${card.folder} · ${card.prompt ? "有原词" : "暂无原词"}`, button);
            element("p", card.prompt.slice(0, 100), button).style.cssText = "font-size:12px;color:#c0c4cd";
            button.onclick = () => {
                const widget = node.widgets.find(w => w.name === "card_key");
                widget.value = card.key;
                widget.callback?.(widget.value);
                const source = node.widgets.find(w => w.name === "source");
                if (source) source.value = "DFlow 素材";
                node.title = `素材入口 · ${card.name.slice(0, 40)}`;
                node.sodaRefreshMaterialPreview?.(card);
                node.setDirtyCanvas(true, true);
                dialog.close();
            };
        }
        more.hidden = found.length <= limit;
    };
    async function load() {
        status.textContent = "正在读取本机 DFlow…";
        try {
            const response = await api.fetchApi(`/soda/materials/dflow?port=${Number(port)}`);
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "读取失败");
            cards = data.cards;
            limit = 50;
            render();
        } catch (error) {
            status.textContent = error.message;
        }
    }
    more.onclick = () => { limit += 50; render(); };
    refresh.onclick = load;
    folder.onchange = search.oninput = () => { limit = 50; render(); };
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove(), {once: true});
    dialog.showModal();
    await load();
}

app.registerExtension({
    name: "Soda.DFlowMaterials",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name === "SodaGallerySource") {
            const gallery = app.extensions.find(e => e.name === "Comfy.DanbooruGallery");
            if (gallery) await gallery.beforeRegisterNodeDef(nodeType, {...nodeData, name: "DanbooruGalleryNode"}, app);
            const refreshConsumers = node => {
                const consumers = new Set();
                for (const output of node.outputs || []) {
                    for (const id of output.links || []) {
                        const link = node.graph?.links[id];
                        const consumer = node.graph?.getNodeById(link?.target_id);
                        if (consumer?.widgets?.find(widget => widget.name === "source")?.value === "作者画廊") consumers.add(consumer);
                    }
                }
                for (const consumer of consumers) consumer.sodaRefreshMaterialPreview?.();
            };
            const created = nodeType.prototype.onNodeCreated;
            nodeType.prototype.onNodeCreated = function (...args) {
                const result = created?.apply(this, args);
                const selection = this.widgets?.find(widget => widget.name === "selection_data");
                if (selection) {
                    const callback = selection.callback;
                    selection.callback = (...values) => {
                        const result = callback?.apply(selection, values);
                        refreshConsumers(this);
                        return result;
                    };
                }
                return result;
            };
            const configure = nodeType.prototype.onConfigure;
            nodeType.prototype.onConfigure = function (...args) {
                const result = configure?.apply(this, args);
                refreshConsumers(this);
                return result;
            };
            return;
        }
        if (!["SodaDFlowSource", "SodaUnifiedSource"].includes(nodeData.name)) return;
        const configure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (...args) {
            const result = configure?.apply(this, args);
            this.sodaRefreshMaterialPreview?.();
            return result;
        };
        const connections = nodeType.prototype.onConnectionsChange;
        nodeType.prototype.onConnectionsChange = function (...args) {
            const result = connections?.apply(this, args);
            this.sodaRefreshMaterialPreview?.();
            return result;
        };
        const original = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function (...args) {
            const result = original?.apply(this, args);
            const addButton = (name, label, action) => {
                const button = element("button", label);
                button.type = "button";
                button.style.cssText = "width:100%;height:30px;border:1px solid #666;border-radius:5px;background:#343840;color:#eee;cursor:pointer";
                button.onclick = action;
                const widget = this.addDOMWidget(name, "button", button, {serialize: false});
                widget.serialize = false;
                widget.computeSize = width => [width, 32];
            };
            addButton("soda_pick_material", "选择 DFlow 素材", () => chooseMaterial(this));
            if (nodeData.name === "SodaUnifiedSource") {
                addButton("soda_upload", "上传本地图片", () => {
                    const input = element("input");
                    input.type = "file";
                    input.accept = "image/*";
                    input.hidden = true;
                    document.body.append(input);
                    input.addEventListener("cancel", () => input.remove(), {once: true});
                    input.onchange = async () => {
                        if (!input.files?.length) return;
                        try {
                            const form = new FormData();
                            form.append("image", input.files[0]);
                            const response = await api.fetchApi("/upload/image", {method: "POST", body: form});
                            if (!response.ok) throw new Error(`上传失败 HTTP ${response.status}`);
                            const data = await response.json();
                            this.widgets.find(w => w.name === "image_path").value = (data.subfolder ? data.subfolder + "/" : "") + data.name;
                            this.widgets.find(w => w.name === "source").value = "本地图片 / PNG元数据";
                            this.setDirtyCanvas(true, true);
                        } catch (error) { app.ui.dialog.show(error.message); }
                        finally { input.remove(); }
                    };
                    input.click();
                });
            }
            addButton("soda_open_dflow", "打开完整 DFlow 瀑布流", () => {
                const port = this.widgets.find(w => w.name === "port").value;
                window.open(`http://127.0.0.1:${Number(port)}`, "_blank", "noopener");
            });
            addMaterialPreview(this);
            return result;
        };
    },
});
