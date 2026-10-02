import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

function setup(shiftedRestore = false) {
    class Element {
        constructor() { this.style = {}; this.dataset = {}; this.children = []; this.attributes = {}; }
        append(child) { this.children.push(child); }
        setAttribute(name, value) { this.attributes[name] = value; }
    }
    class OutputNode {
        constructor() { this.widgets = []; this.size = [380, 350]; this.changes = 0; this.graph = {change: () => this.changes++}; }
        onNodeCreated() {
            for (const [name, value] of [["use_edited", false], ["edited_prompt", ""], ["target", "Anima"],
                ["adapt_to_target", true], ["refresh", 0], ["timeout_seconds", 180], ["anima_token_budget", 512]]) {
                this.widgets.push({name, value});
            }
        }
        onConfigure(info) { info.widgets_values.slice(shiftedRestore ? 1 : 0).forEach((value, index) => { this.widgets[index].value = value; }); }
        addDOMWidget(name, type, element, options) { const widget = {name, type, element, options}; this.widgets.push(widget); return widget; }
        computeSize() { return [380, 500]; }
        setSize(size) { this.size = size; }
        setDirtyCanvas() {}
    }
    let extension;
    const source = fs.readFileSync(new URL("../web/target_prompt.js", import.meta.url), "utf8").replace(/^import .*;\r?\n/gm, "");
    vm.runInNewContext(source, {app: {registerExtension: value => { extension = value; }},
        document: {createElement: () => new Element()}});
    extension.beforeRegisterNodeDef(OutputNode, {name: "SodaPromptOutput"});
    const node = new OutputNode();
    node.onNodeCreated();
    const widget = name => node.widgets.find(widget => widget.name === name);
    const container = widget("soda_target_quick_pick").element;
    const [row, status] = container.children;
    return {node, widget, buttons: row.children, status};
}

test("three buttons select exactly one target and turn adaptation on", () => {
    const {node, widget, buttons, status} = setup();
    assert.deepEqual(buttons.map(button => button.textContent), ["Anima", "Krea2", "Qwen 2.1"]);
    for (const [index, target] of ["Anima", "Krea2", "Qwen2.1"].entries()) {
        widget("adapt_to_target").value = false;
        buttons[index].onclick();
        assert.equal(widget("target").value, target);
        assert.equal(widget("adapt_to_target").value, true);
        assert.equal(buttons.filter(button => button.attributes["aria-pressed"] === "true").length, 1);
        assert.ok(status.textContent.includes(target));
    }
    assert.equal(node.changes, 3);
});

test("saved native values restore by name even if the host shifts converted input widgets", () => {
    const {node, widget, buttons} = setup(true);
    node.onConfigure({widgets_values: [false, "", "Qwen2.1", true, 3, 180, 512, ""]});
    assert.equal(widget("use_edited").value, false);
    assert.equal(widget("edited_prompt").value, "");
    assert.equal(widget("target").value, "Qwen2.1");
    assert.equal(widget("refresh").value, 3);
    assert.equal(buttons[2].attributes["aria-pressed"], "true");
});

test("named saved values take priority over host positional remapping", () => {
    const {node, widget, buttons} = setup();
    node.onConfigure({widgets_values: ["", "Qwen2.1", true], widgets_values_named: {
        use_edited:false, edited_prompt:"", target:"Qwen2.1", adapt_to_target:true}});
    assert.equal(widget("edited_prompt").value, "");
    assert.equal(widget("target").value, "Qwen2.1");
    assert.equal(buttons[2].attributes["aria-pressed"], "true");
    assert.equal(widget("soda_target_quick_pick").serialize, false);
});

test("legacy workflow values keep their positions and restore selected button", () => {
    const {node, widget, buttons} = setup();
    node.onConfigure({widgets_values: [true, "manual text", "Krea2"]});
    assert.equal(widget("use_edited").value, true);
    assert.equal(widget("edited_prompt").value, "manual text");
    assert.equal(widget("target").value, "Krea2");
    assert.equal(widget("adapt_to_target").value, true);
    assert.equal(buttons[1].attributes["aria-pressed"], "true");
    assert.equal(widget("soda_target_quick_pick").options.serialize, false);
    assert.equal(node.widgets.at(-1).name, "soda_target_quick_pick");
});

test("dropdown and disabled adapter synchronize button state", () => {
    const {widget, buttons, status} = setup();
    widget("target").value = "Qwen2.1";
    widget("target").callback("Qwen2.1");
    assert.equal(buttons[2].attributes["aria-pressed"], "true");
    widget("adapt_to_target").value = false;
    widget("adapt_to_target").callback(false);
    assert.ok(buttons.every(button => button.attributes["aria-pressed"] === "false"));
    assert.ok(status.textContent.includes("原样输出"));
});
