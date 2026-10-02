import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

function setup() {
    const config = {active:"deepseek", profiles:{
        deepseek:{name:"DeepSeek",base_url:"https://api.deepseek.com",text_model:"deepseek-flash",vision_model:"deepseek-flash",strict_model:true,key_configured:true},
        glm:{name:"GLM / 智谱",base_url:"https://open.bigmodel.cn/api/paas/v4",text_model:"",vision_model:"",strict_model:false,key_configured:false},
    }};
    class Element {
        constructor(tag) {this.tag=tag;this.style={};this.children=[];this.listeners={};}
        append(child) {this.children.push(child);}
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
    return {node,calls,body};
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
