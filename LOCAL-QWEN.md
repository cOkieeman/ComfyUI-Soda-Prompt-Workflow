# 本地 Qwen3.5 看图反推

②新增 `Qwen3.5 · 本地看图反推`，输入①的本地图片、作者画廊或 DFlow 图片，返回候选标签和英文短描述。②a继续分类取舍，⑤继续选择 Anima / Krea2 / Qwen2.1。

②下方 **本地 Qwen3.5 配置 / 使用** 可保存主模型、对应投影模型的完整 GGUF 路径、运行库目录、设备、上下文、输出长度和种子。配置保存在 `ComfyUI/user/soda_prompt_workflow/local_vlm.json`，不写入工作流。保存只验证文件路径与 GGUF 文件头，不加载模型；主模型与投影的兼容性需运行确认。

点击“使用本地 Qwen 反推”会选择重新反推和本地路线，并将②超时设为600秒。①必须有图片。新模板 [本地Qwen反推版](workflows/Soda-Prompt-Workbench-本地Qwen反推版.json) 默认从本地图片开始，上传图片后运行即可；⑤默认适配Anima，也可点击其他目标按钮。

**仅②看图反推完全本地。** ③OC替换、④Flash扩写和⑤最终适配开启时仍调用当前AI服务。⑤关闭 `adapt_to_target` 可原样输出本地标签＋描述，但不会自动转换成 Krea2/Qwen2.1 的目标格式。TIPO依然是另一条可选的本地扩写路线。

## 运行库

需要支持 `Qwen35ChatHandler` 的 llama-cpp-python。本机实际验证版本为 [JamePeng 0.4.1 CUDA13 Windows轮子](https://github.com/JamePeng/llama-cpp-python/releases/tag/v0.4.1-cu130-win-20260926)。选择与自己的Python版本、系统和CUDA兼容的文件。该功能不依赖其他插件的节点接口，不会升级已有TIPO运行库。

可以将轮子单独安装到目录，再填入配置窗口。例如Windows portable Python3.13：

```powershell
& '你的ComfyUI_windows_portable\python_embeded\python.exe' -m pip install --no-deps --target '你的ComfyUI\user\soda_prompt_workflow\local-vlm-runtime' '已下载的cp313-win_amd64.whl'
```

目录中应有 `llama_cpp/__init__.py`。PyTorch使用已有ComfyUI环境；不添加全局llama-cpp依赖。模型、运行库均不随插件分发，节点不会联网下载。

## 缓存、显存与结果边界

模型在独立进程加载，成功、失败、超时或取消后退出，释放它占用的显存。每个宿主事件循环一次只加载一个本地看图模型；等待也计入超时。自动设备模式根据CUDA能力和空闲显存估算选择GPU或CPU，估算不能保证避免显存不足；显存不足时可选CPU或自行释放其他任务。不会自动卸载ComfyUI或其他程序的模型。

`cache-local-vlm` 按图片、指令、模型文件状态、配置、版本和refresh区分；命中后不重新加载模型。失败或残缺输出不缓存，不自动调用远程服务。强制结束整个宿主留下pending文件时，可增加refresh重做。输出限制与格式约束防止无限罗列标签，达到最大输出长度时明确报错。

标签来自视觉模型，可能包含非标准词、遗漏或幻觉；没有通过WD逐项核验。原素材标签、②a分类和最终文本可用于对照检查，不确定信息在记录中保存。公开模板不包含本机模型路径、API密钥或用户图片。
