import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

test("local settings preserve widget slots; route pick changes only route, action and timeout", async () => {
    const config = {model_path:"C:/model.gguf", mmproj_path:"C:/mmproj.gguf", runtime_directory:"", device:"auto", context_size:4096, max_tokens:1024, seed:42};
    class Element {
        constructor() {this.style={};this.children=[];this.events={};}
        append(child) {this.children.push(child);}
        setAttribute(name,value) {this[name]=value;}
        addEventListener(name,callback) {this.events[name]=callback;}
        showModal() {}
        close() {this.events.close?.();}
        remove() {}
    }
    const calls=[], body=new Element(); let extension;
    const app={registerExtension:e=>{extension=e;},graph:{change(){},setDirtyCanvas(){}},ui:{dialog:{show(message){throw new Error(message);}}}};
    const api={async fetchApi(path, options){
        calls.push({path,options});
        if (options) Object.assign(config, JSON.parse(options.body));
        return {ok:true,json:async()=>({config:{...config},paths_ready:true})};
    }};
    const listeners={};
    const window={addEventListener:(name,fn)=>{listeners[name]=fn;},removeEventListener:name=>{delete listeners[name];},dispatchEvent:event=>listeners[event.type]?.(event)};
    const code=fs.readFileSync(new URL("../web/local_vlm.js",import.meta.url),"utf8").replace(/^import .*;\r?\n/gm,"");
    vm.runInNewContext(code,{app,api,window,CustomEvent:class{constructor(type,args){this.type=type;Object.assign(this,args);}},document:{body,createElement:()=>new Element()}});
    class Node {
        constructor(){this.widgets=[{name:"action",value:"文字创作"},{name:"route",value:"old"},{name:"user_prompt",value:"keep"},{name:"timeout_seconds",value:180}];this.size=[500,500];}
        addDOMWidget(name,type,element,options){const widget={name,type,element,options};this.widgets.push(widget);return widget;}
        setSize() {}
        computeSize(){return [500,500];}
    }
    extension.beforeRegisterNodeDef(Node,{name:"SodaUnifiedProcess"});
    const node=new Node();node.onNodeCreated();
    await new Promise(resolve=>setImmediate(resolve));
    const widget=node.widgets[4];
    assert.equal(widget.serialize,false); assert.equal(widget.options.serialize,false);
    await widget.element.children[0].onclick();
    const dialog=body.children[0];
    const field=name=>dialog.children.find(el=>el["aria-label"]===name);
    field("最大输出 token 数").value="1536";
    await dialog.children.find(el=>el.textContent==="保存配置").onclick();
    assert.equal(config.max_tokens,1536);
    dialog.children.find(el=>el.textContent==="使用本地 Qwen 反推").onclick();
    assert.equal(node.widgets[0].value,"重新反推");
    assert.equal(node.widgets[1].value,"Qwen3.5 · 本地看图反推");
    assert.equal(node.widgets[2].value,"keep"); assert.equal(node.widgets[3].value,600);
    assert.ok(dialog.children.some(el=>el.textContent.includes("⑤最终适配开启时仍使用 AI 服务")));
    assert.ok(calls.every(call=>call.path==="/soda/local-vlm"));
    node.onRemoved(); assert.equal(Object.keys(listeners).length,0);
});
