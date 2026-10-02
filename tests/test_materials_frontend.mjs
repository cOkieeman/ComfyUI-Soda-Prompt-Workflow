import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

function setup() {
    class Element {
        constructor() { this.style = {}; this.dataset = {}; this.children = []; }
        append(child) { this.children.push(child); }
        removeAttribute(name) { delete this[name]; }
        setAttribute(name, value) { this[name] = value; }
    }
    class SourceNode {
        constructor() { this.widgets = []; this.size = [500, 800]; }
        onNodeCreated() {
            for (const [name, value] of [["source", "文字输入"], ["image_path", ""],
                ["text", ""], ["port", 4173], ["card_key", ""]]) this.widgets.push({name, value});
        }
        onConfigure(info) { info.widgets_values.forEach((value, index) => { this.widgets[index].value = value; }); }
        addDOMWidget(name, type, element, options) {
            const widget = {name, type, element, options};
            this.widgets.push(widget);
            return widget;
        }
        computeSize() { return [500, 800]; }
        setSize(size) { this.size = size; }
        setDirtyCanvas() {}
        getInputNode(slot) { return this.inputNodes?.[slot]; }
    }
    class GalleryNode {
        constructor() { this.widgets = []; this.outputs = [{links: [1]}]; }
        onNodeCreated() { this.widgets.push({name: "selection_data", value: "{}", callback() {}}); }
        onConfigure(info) { this.widgets[0].value = info.widgets_values[0]; }
    }
    let extension;
    const pending = [];
    const api = {apiURL: path => path, fetchApi: path => new Promise(resolve => pending.push({path, resolve}))};
    const app = {extensions: [{name: "Comfy.DanbooruGallery", async beforeRegisterNodeDef() {}}],
        registerExtension: value => { extension = value; }};
    const source = fs.readFileSync(new URL("../web/materials.js", import.meta.url), "utf8")
        .replace(/^import .*;\r?\n/gm, "");
    vm.runInNewContext(source, {app, api, document: {createElement: () => new Element()}, window: {}});
    extension.beforeRegisterNodeDef(SourceNode, {name: "SodaUnifiedSource"});
    const node = new SourceNode();
    node.onNodeCreated();
    const widget = name => node.widgets.find(input => input.name === name);
    const container = widget("soda_material_preview").element;
    const [image, status] = container.children;
    const reply = (request, cards) => request.resolve({ok: true, json: async () => ({cards})});
    const addGallery = async () => {
        await extension.beforeRegisterNodeDef(GalleryNode, {name: "SodaGallerySource"});
        const gallery = new GalleryNode();
        gallery.onNodeCreated();
        gallery.graph = {links: {1: {target_id: 1}}, getNodeById: () => node};
        node.inputNodes = [gallery, gallery];
        return gallery;
    };
    return {node, widget, container, image, status, pending, reply, addGallery};
}

test("old saved workflow restores inputs and selected preview without running the graph", async () => {
    const view = setup();
    const values = ["DFlow 素材", "old.png", "handwritten prompt", 4173, "favorite:existing"];
    view.node.onConfigure({widgets_values: values});
    assert.deepEqual(view.node.widgets.slice(0, 5).map(input => input.value), values);
    assert.equal(view.widget("soda_material_preview").options.serialize, false);
    assert.equal(view.pending.length, 1);
    view.reply(view.pending[0], [{key: "favorite:existing", name: "Existing picture", image_path: "/cached.png", prompt: ""}]);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(view.image.hidden, false);
    assert.match(view.image.src, /key=favorite%3Aexisting/);
    assert.match(view.status.textContent, /重新反推/);
});

test("gallery selection callback immediately updates linked source image and tags without execution", async () => {
    const view = setup();
    const gallery = await view.addGallery();
    view.widget("source").value = "作者画廊";
    view.node.title = "素材入口 · old DFlow picture";
    const selection = gallery.widgets[0];
    selection.value = JSON.stringify({selections: [{post_id: "123", source_site: "danbooru",
        image_url: "https://cdn.donmai.us/test.jpg", prompt: "blue hair, cat ears"}]});
    selection.callback();
    assert.equal(view.container.hidden, false);
    assert.equal(view.image.hidden, false);
    assert.match(view.image.src, /image_proxy\?url=https%3A/);
    assert.match(view.status.textContent, /Danbooru #123/);
    assert.equal(view.container.children[2].value, "blue hair, cat ears");
    assert.equal(view.container.children[2].readOnly, true);
    assert.match(view.node.title, /Danbooru #123/);
    assert.equal(view.widget("text").value, "");
    assert.equal(view.pending.length, 0);
    selection.value = '{"selections":[]}';
    selection.callback();
    assert.equal(view.image.hidden, true);
    assert.equal(view.image.src, undefined);
    assert.equal(view.container.children[2].value, "");
    assert.match(view.status.textContent, /尚未选择/);
    assert.doesNotMatch(view.node.title, /#123/);
});

test("saved gallery selection restores preview and source changes ignore inactive gallery", async () => {
    const view = setup();
    const gallery = await view.addGallery();
    gallery.onConfigure({widgets_values: [JSON.stringify({selections: [
        {post_id: "456", source_site: "gelbooru", image_url: "https://img.gelbooru.com/456.png", prompt: "white dress"},
        {post_id: "789", image_url: "https://img.gelbooru.com/789.png", prompt: "blue dress"},
    ]})]});
    view.node.onConfigure({widgets_values: ["作者画廊", "", "manual words", 4173, "favorite:old"]});
    assert.match(view.status.textContent, /Gelbooru #456/);
    assert.match(view.status.textContent, /2 张/);
    assert.equal(view.widget("text").value, "manual words");
    view.widget("source").value = "文字输入";
    view.widget("source").callback();
    gallery.widgets[0].callback();
    assert.equal(view.container.hidden, true);
    assert.doesNotMatch(view.node.title, /#456|old/);
    assert.equal(view.image.src, undefined);
});

test("disconnecting gallery or invalid saved data clears preview and explains the missing selection", async () => {
    const view = setup();
    await view.addGallery();
    view.widget("source").value = "作者画廊";
    view.node.inputNodes = [];
    view.node.onConnectionsChange();
    assert.match(view.status.textContent, /连接/);
    const gallery = await view.addGallery();
    gallery.widgets[0].value = "invalid json";
    gallery.widgets[0].callback();
    assert.match(view.status.textContent, /选择数据/);
    assert.equal(view.image.hidden, true);
});

test("selecting a different card prevents an older pending response from replacing its preview", async () => {
    const view = setup();
    view.widget("source").value = "DFlow 素材";
    view.widget("card_key").value = "favorite:old";
    const oldRequest = view.node.sodaRefreshMaterialPreview();
    view.widget("card_key").value = "favorite:new";
    await view.node.sodaRefreshMaterialPreview({key: "favorite:new", name: "New picture", image_path: "/new.png", prompt: "New prompt"});
    view.reply(view.pending[0], [{key: "favorite:old", name: "Old picture", image_path: "/old.png", prompt: ""}]);
    await oldRequest;
    assert.match(view.image.src, /key=favorite%3Anew/);
    assert.match(view.status.textContent, /^New picture/);
    view.widget("source").value = "文字输入";
    view.widget("source").callback();
    assert.equal(view.container.hidden, true);
    assert.equal(view.image.src, undefined);
});

test("uncached and unavailable materials explain why there is no image", async () => {
    const view = setup();
    view.widget("source").value = "DFlow 素材";
    view.widget("card_key").value = "favorite:uncached";
    await view.node.sodaRefreshMaterialPreview({key: "favorite:uncached", name: "Waiting", image_path: "", prompt: ""});
    assert.equal(view.image.hidden, true);
    assert.match(view.status.textContent, /缓存未就绪/);
    const missing = view.node.sodaRefreshMaterialPreview();
    view.reply(view.pending[0], []);
    await missing;
    assert.match(view.status.textContent, /已不存在/);
});
