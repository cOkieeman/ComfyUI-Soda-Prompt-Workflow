import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

function setup(probe = async path => ({ok:true, json:async()=>path.endsWith("/models") ? {models:["model-a","model-v"]} :
    {ok:true, elapsed_ms:125, response_model:"model-a",warning:""}})) {
    const config = {active:"deepseek", profiles:{
        deepseek:{name:"DeepSeek",base_url:"https://api.deepseek.com",text_model:"deepseek-flash",vision_model:"deepseek-flash",strict_model:true,key_configured:true},
        glm:{name:"GLM / 智谱",base_url:"https://open.bigmodel.cn/api/paas/v4",text_model:"",vision_model:"",strict_model:false,key_configured:false},
    }};
    class Element {
        constructor(tag) {this.tag=tag;this.style={};this.children=[];this.listeners={};}
        append(child) {this.children.push(child);}
        replaceChildren(...children) {this.children=children;}
        setAttribute(name,value) {this[name]=value;}
        addEventListener(name,callback) {this.listeners[name]=callback;}
        showModal() {this.open=true;}
        close() {this.listeners.close?.();}
        remove() {this.removed=true;}
    }
    const body=new Element("body"), calls=[], listeners={};
    let extension;
    const app={registerExtension:e=>{extension=e;},graph:{change(){}},ui:{dialog:{show(message){throw new Error(message);}}}};
    const api={async fetchApi(path,options){
        calls.push({path,options});
        if (path !== "/soda/ai-services") return probe(path, options);
        if(options) {
            const saved=JSON.parse(options.body);
            config.active=saved.active;
            for(const [id,profile] of Object.entries(saved.profiles)) {
                config.profiles[id]={...profile,key_configured:!!profile.api_key || profile.key_configured};
                delete config.profiles[id].api_key;
            }
        }
        return {ok:true,json:async()=>JSON.parse(JSON.stringify(config))};
    }};
    const window={addEventListener:(name,fn)=>{listeners[name]=fn;},removeEventListener:(name)=>{delete listeners[name];},
        dispatchEvent:event=>listeners[event.type]?.(event)};
    const code=fs.readFileSync(new URL("../web/ai_services.js",import.meta.url),"utf8").replace(/^import .*;\r?\n/gm,"");
    vm.runInNewContext(code,{app,api,window,CustomEvent:class{constructor(type,args){this.type=type;Object.assign(this,args);}},document:{body,createElement:tag=>new Element(tag)}});
    class Node {
        constructor(){this.widgets=[{name:"target",value:"Anima"}];this.size=[500,500];}
        addDOMWidget(name,type,element,options){const widget={name,type,element,options};this.widgets.push(widget);return widget;}
        setSize(size){this.size=size;}
        computeSize(){return [500,500];}
    }
    extension.beforeRegisterNodeDef(Node,{name:"SodaPromptOutput"});
    const node=new Node();node.onNodeCreated();
    return {node,calls,body,config};
}

test("service button is nonserialized, preserves old widget values, and only fetches public settings",async()=>{
    const view=setup();
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(view.node.widgets[0].value,"Anima");
    const widget=view.node.widgets[1];
    assert.equal(widget.options.serialize,false);
    assert.match(widget.element.children[1].textContent,/DeepSeek.*deepseek-flash/);
    assert.equal(view.calls[0].path,"/soda/ai-services");
    assert.equal(view.calls[0].options,undefined);
});

test("dialog switches profiles, submits keys only to local save endpoint, and clears the password field",async()=>{
    const view=setup();
    await view.node.widgets[1].element.children[0].onclick();
    const dialog=view.body.children[0];
    const field=name=>dialog.children.find(el=>el["aria-label"]===name);
    const select=field("AI 服务");
    select.value="glm";select.onchange();
    assert.equal(field("Base URL / 服务地址").value,"https://open.bigmodel.cn/api/paas/v4");
    field("文字模型（改写、扩写、最终适配）").value="test-glm-text";
    field("看图模型（图片反推；需要支持图片输入）").value="test-glm-vision";
    const key=dialog.children.find(el=>el.type==="password");
    assert.equal(key.value,"");
    key.value="test-not-a-real-key";
    await dialog.children.find(el=>el.textContent==="保存并启用当前服务").onclick();
    const save=view.calls.find(call=>call.options?.method==="POST");
    assert.equal(save.path,"/soda/ai-services");
    assert.equal(JSON.parse(save.options.body).active,"glm");
    assert.equal(key.value,"");
    assert.match(view.node.widgets[1].element.children[1].textContent,/test-glm-text.*test-glm-vision/);
    assert.equal(view.calls.filter(call=>call.path.includes("chat/completions")).length,0);
});

async function open(view) {
    await view.node.widgets[1].element.children[0].onclick();
    const dialog=view.body.children[0];
    return {dialog, field:name=>dialog.children.find(el=>el["aria-label"]===name),
        button:name=>dialog.children.find(el=>el.textContent===name),
        status:dialog.children.find(el=>el.role==="status")};
}

test("pull uses unsaved current profile and fills both model fields only on explicit selection",async()=>{
    const view=setup(), ui=await open(view);
    const select=ui.field("AI 服务");select.value="glm";select.onchange();
    ui.field("Base URL / 服务地址").value="https://gateway.example/v1";
    ui.dialog.children.find(el=>el.type==="password").value="fake-draft-key";
    await ui.button("拉取模型").onclick();
    const call=view.calls.find(call=>call.path.endsWith("/models"));
    assert.equal(JSON.parse(call.options.body).service,"glm");
    assert.equal(JSON.parse(call.options.body).profile.base_url,"https://gateway.example/v1");
    assert.equal(view.config.active,"deepseek");
    assert.equal(ui.field("文字模型（改写、扩写、最终适配）").value,"");
    const choices=ui.field("已拉取模型 → 文字模型");
    assert.equal(choices.hidden,false);
    choices.value="model-a";choices.onchange();
    assert.equal(ui.field("文字模型（改写、扩写、最终适配）").value,"model-a");
    const vision=ui.field("已拉取模型 → 看图模型");vision.value="model-v";vision.onchange();
    assert.equal(ui.field("看图模型（图片反推；需要支持图片输入）").value,"model-v");
    ui.field("Base URL / 服务地址").oninput();
    assert.equal(choices.hidden,true);
    assert.equal(view.calls.filter(call=>call.path==="/soda/ai-services" && call.options).length,0);
});

test("text and vision tests send separate choices and show timing without saving",async()=>{
    const view=setup(),ui=await open(view);
    await ui.button("测试文字模型").onclick();
    assert.match(ui.status.textContent,/文字模型测试通过.*125 ms/);
    await ui.button("测试看图模型").onclick();
    assert.match(ui.status.textContent,/看图模型测试通过/);
    const calls=view.calls.filter(call=>call.path.endsWith("/test"));
    assert.deepEqual(calls.map(call=>JSON.parse(call.options.body).model_type),["text","vision"]);
    assert.equal(view.calls.filter(call=>call.path==="/soda/ai-services" && call.options).length,0);
    ui.field("文字模型（改写、扩写、最终适配）").oninput();
    assert.match(ui.status.textContent,/重新测试/);
});

test("errors restore controls and preserve manually entered models",async()=>{
    const view=setup(async()=>({ok:false,json:async()=>({error:"模型列表不可用，请手动填写"})})),ui=await open(view);
    await ui.button("拉取模型").onclick();
    assert.match(ui.status.textContent,/手动填写/);
    assert.equal(ui.field("文字模型（改写、扩写、最终适配）").value,"deepseek-flash");
    assert.equal(ui.button("测试文字模型").disabled,false);
    assert.equal(ui.button("保存并启用当前服务").disabled,false);
});

test("pending probes lock profile edits and ignore late results after dialog close",async()=>{
    let finish;
    const view=setup(()=>new Promise(resolve=>{finish=resolve;})),ui=await open(view);
    const pending=ui.button("拉取模型").onclick();
    assert.equal(ui.field("AI 服务").disabled,true);
    assert.equal(ui.button("测试看图模型").disabled,true);
    const key=ui.dialog.children.find(el=>el.type==="password");key.value="fake-draft-key";
    ui.button("关闭").onclick();
    finish({ok:true,json:async()=>({models:["model-a"]})});
    await pending;
    assert.equal(key.value,"");
    assert.equal(ui.dialog.removed,true);
    assert.equal(ui.field("已拉取模型 → 文字模型").hidden,true);
});

test("switching profiles clears model candidates and does not reuse a different profile's draft",async()=>{
    const view=setup(),ui=await open(view);
    await ui.button("拉取模型").onclick();
    const select=ui.field("AI 服务");select.value="glm";select.onchange();
    assert.equal(ui.field("已拉取模型 → 文字模型").hidden,true);
    await ui.button("测试文字模型").onclick();
    const call=view.calls.find(call=>call.path.endsWith("/test"));
    assert.equal(JSON.parse(call.options.body).service,"glm");
    assert.equal(JSON.parse(call.options.body).profile.text_model,"");
});

test("malformed successful HTTP responses never report a successful model test",async()=>{
    const view=setup(async()=>({ok:true,json:async()=>null})),ui=await open(view);
    await ui.button("测试文字模型").onclick();
    assert.match(ui.status.textContent,/未返回有效的测试结果/);
    assert.equal(ui.button("测试文字模型").disabled,false);
});
