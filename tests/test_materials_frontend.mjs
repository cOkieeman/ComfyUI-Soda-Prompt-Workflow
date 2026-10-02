import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

function setup() {
    class Element {
        constructor() { this.style = {}; this.dataset = {}; this.children = []; this.events = {}; }
        append(child) { this.children.push(child); }
        removeAttribute(name) { delete this[name]; }
        setAttribute(name, value) { this[name] = value; }
        addEventListener(name, action) { this.events[name] = action; }
        click() { this.clicked = true; }
        remove() { this.removed = true; }
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
    const api = {apiURL: path => path, fetchApi: (path, options) => new Promise(resolve => pending.push({path, options, resolve}))};
    const app = {extensions: [{name: "Comfy.DanbooruGallery", async beforeRegisterNodeDef() {}}],
        registerExtension: value => { extension = value; }};
    const source = fs.readFileSync(new URL("../web/materials.js", import.meta.url), "utf8")
        .replace(/^import .*;\r?\n/gm, "");
    const body = new Element();
    vm.runInNewContext(source, {app, api, FormData: class {append() {}}, document: {body, createElement: () => new Element()}, window: {}});
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
    return {node, widget, container, image, status, pending, reply, addGallery, body};
}

test("restored local upload displays an encoded image preview without executing the graph", async () => {
    const view = setup();
    view.node.onConfigure({widgets_values: ["本地图片 / PNG元数据", "素材目录/图 & 1.png", "keep prompt", 4173, "favorite:old"]});
    assert.equal(view.container.hidden, false);
    assert.equal(view.image.hidden, false);
    assert.match(view.image.src, /\/view\?/);
    const url = new URL(view.image.src, "http://localhost");
    assert.equal(url.searchParams.get("filename"), "图 & 1.png");
    assert.equal(url.searchParams.get("subfolder"), "素材目录");
    assert.equal(url.searchParams.get("type"), "input");
    assert.equal(view.widget("text").value, "keep prompt");
    assert.equal(view.pending.length, 0);
});

test("changing or clearing a local path refreshes preview and stale image errors are ignored", async () => {
    const view = setup();
    view.node.onConfigure({widgets_values: ["本地图片 / PNG元数据", "old.png", "", 4173, ""]});
    const oldError = view.image.onerror;
    view.widget("image_path").value = "new.png";
    view.widget("image_path").callback();
    assert.equal(new URL(view.image.src, "http://localhost").searchParams.get("filename"), "new.png");
    oldError();
    assert.equal(view.image.hidden, false);
    view.widget("image_path").value = "";
    view.widget("image_path").callback();
    assert.equal(view.image.src, undefined);
    assert.equal(view.image.hidden, true);
    assert.match(view.status.textContent, /上传/);
});

test("successful upload selects local source and immediately updates preview", async () => {
    const view = setup();
    view.widget("soda_upload").element.onclick();
    const input = view.body.children[0];
    input.files = [{name: "fixture.png"}];
    const upload = input.onchange();
    assert.equal(view.pending[0].path, "/upload/image");
    view.pending[0].resolve({ok:true, json:async()=>({name:"fixture.png", subfolder:"uploaded"})});
    await upload;
    assert.equal(view.widget("source").value, "本地图片 / PNG元数据");
    assert.equal(view.widget("image_path").value, "uploaded/fixture.png");
    assert.equal(view.container.hidden, false);
    assert.equal(view.image.hidden, false);
    assert.equal(new URL(view.image.src, "http://localhost").searchParams.get("filename"), "fixture.png");
    assert.equal(input.removed, true);
});

test("absolute local paths use the thumbnail bridge and missing files show a helpful message", async () => {
    const view = setup();
    view.node.onConfigure({widgets_values: ["本地图片 / PNG元数据", '"C:\\图片\\picture.png"', "", 4173, ""]});
    assert.match(view.image.src, /\/soda\/materials\/local\/image\?/);
    assert.equal(new URL(view.image.src, "http://localhost").searchParams.get("image_path"), "C:\\图片\\picture.png");
    view.image.onerror();
    assert.equal(view.image.hidden, true);
    assert.match(view.status.textContent, /路径|上传/);
    view.widget("source").value = "文字输入";
    view.widget("source").callback();
    assert.equal(view.container.hidden, true);
    assert.equal(view.image.src, undefined);
});

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
