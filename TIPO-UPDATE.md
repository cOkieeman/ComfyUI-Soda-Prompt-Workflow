# TIPO 本地扩写

第④步选择 TIPO。安装官方 [z-tipo-extension](https://github.com/KohakuBlueleaf/z-tipo-extension) 及与硬件匹配的推理依赖。验证版本：tipo-kgen 0.3.1、llama-cpp-python 0.3.36 CUDA 13、diskcache 5.6.3；其他显卡需按官方说明选择包。[CUDA 13 官方索引](https://abetlen.github.io/llama-cpp-python/whl/cu130/llama-cpp-python/)。

[TIPO-v2.1-1B-A200M](https://huggingface.co/KBlueLeaf/TIPO-v2.1-1B-A200M) GGUF 放入 ComfyUI 自身的 models/kgen，文件名需一致：

```text
TIPO-v2.1-1B-A200M_TIPO-v2.1-1B-A200M-f16.gguf
TIPO-v2.1-1B-A200M-Q8_0.gguf
```

FP16 沿用官方插件的下载命名，Q8_0 为已验证的本地文件。仓库不附模型。当前接口使用本机 models/kgen，单独设置共享模型目录不足以发现其他位置的文件。

- 自动：识别匹配记录中的标签，含前后缀节点传递的结构；其他按自然语言处理。
- 标签：整段 booru 标签。
- 标签＋描述：首段标签，后续段落描述；也支持首行标签、后续行描述。
- 自然语言：整段描述。

粘贴纯标签请显式选择标签。默认保留原标签＋扩写描述，也可选生成标签＋描述或仅描述。requirements 只用于 Flash；TIPO 的场景要求直接写入上游文本。

宽高与实际出图一致。默认种子1234、short长度、温度0.5。改种子生成另一稿；相同输入、模型文件和参数命中缓存。CUDA 不保证跨硬件逐字一致。

独立进程调用官方节点，timeout_seconds 包含等待、启动和推理。超时、取消或 ComfyUI 中断时停止进程，不保存未完成结果；结束后释放模型资源。子进程禁止自动安装依赖。关闭或 Flash 不启动 TIPO。

实测输入含 1girl, solo，两个模型仍可能扩写出第二个人。默认关闭，启用后在第⑤步核对并修订。保留标签不保证描述严格遵守标签。
