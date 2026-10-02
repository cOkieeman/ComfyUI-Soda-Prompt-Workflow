# ComfyUI Soda Prompt Workflow

独立的 ComfyUI 提示词工作台：从本地图片、PNG 原词、作者画廊、DFlow 或文字需求开始，完成原词读取、反推、OC 处理、可选扩写、人工修订与归档。

导入 `workflows/Soda-Prompt-Workbench.json`，14个节点、19条连接。最终正负面文本可复制或连接到自己的生图工作流。

## 安装

需要 Python 3.11+ 和已有的 ComfyUI/PyTorch。使用运行 ComfyUI 的同一份 Python 安装依赖。

```sh
cd ComfyUI/custom_nodes
git clone https://github.com/cOkieeman/ComfyUI-Soda-Prompt-Workflow.git
python -m pip install -r ComfyUI-Soda-Prompt-Workflow/requirements.txt
```

Windows portable 用户把 python 换成 python_embeded/python.exe 的实际路径。重启 ComfyUI、刷新网页，再导入工作流。

工作流预览依赖 [comfyui-custom-scripts](https://github.com/pythongosssss/ComfyUI-Custom-Scripts)。TIPO 路线依赖 [z-tipo-extension](https://github.com/KohakuBlueleaf/z-tipo-extension) 及其推理依赖。画廊路线需要提供 DanbooruGalleryNode 和 Comfy.DanbooruGallery 接口的作者插件；后继 Aaalice-Nodes 的兼容性尚未验证。未选中的画廊分支不执行。Easy-Use 和 rgthree 不是这张工作台的必需依赖。

## 使用

1. **① 素材入口**：本地图片、作者画廊、DFlow 或文字。选择 DFlow 卡片后，入口立即显示所选图片预览和原词状态，重新打开工作流也会恢复预览；无需运行或调用反推接口。普通 JPG/PNG 可反推；没有元数据时返回空原词。
2. **② 处理方式**：原提示词、重新反推或文字创作。没有原词需手动选反推，不会自动调用付费接口。创作用 variant_index 选稿。
3. **③ 前后缀 / OC**：本地拼接前后缀；OC 替换需开关和设定。前后缀保留已识别标签的结构记录，OC 改写后不复用可能过时的标签。
4. **④ 扩写选择**：关闭、TIPO、K2 七层英文或 DFlow 中文。默认关闭，一次只执行选中的路线。
5. **⑤ 目标适配**：点击 **Anima / Krea2 / Qwen 2.1** 快捷按钮，再运行工作流，最终只输出所选模型的提示词。Anima 是标签＋英文短描述，Krea2 是英文自然语言，Qwen2.1 是中文自然语言。首次适配用 Flash，相同输入和目标复用缓存；不改上游反推与扩写规则。手动输入也可适配；关闭 adapt_to_target 后原样输出。Anima 的512 token是可调整的长度偏好，0表示不限制，超预算不截断。
6. **输出与归档**：正负面分别输出。归档默认开启，DFlow 回写默认关闭。不同页签不能跨页连线，需复制模块进入生图流或复制最终文本。

PNG 的 A1111 parameters 优先。ComfyUI 图仅在已支持采样器直接连接字面文本编码节点、且各采样器结果一致时提取正负面词。复杂连接或多套不同词保留清单和警告，返回空原词，不把负面词拼进正面词。

## 模型、密钥和预设

反推、文字创作、OC 和 Flash 扩写使用官方 deepseek-flash。从 secrets.example.toml 复制空模板到 ComfyUI/user/soda_prompt_workflow/secrets.toml，在本机填写；也可设置 DEEPSEEK_API_KEY。密钥不进入工作流或记录。TIPO 不需要 Flash 密钥，见 [TIPO-UPDATE.md](TIPO-UPDATE.md)。

WD + Flash 需要固定资源，放入 ComfyUI/user/soda_prompt_workflow/assets/wd-eva02-tagger-2026-canary-onnx-v2：

- [model.onnx](https://huggingface.co/Misaka41Z/wd-eva02-tagger-2026-canary-onnx-v2/resolve/0a86acfa093b33b8818667820e52fc5eccf27ff8/model.onnx)
- [selected_tags.csv](https://huggingface.co/Misaka41Z/wd-eva02-tagger-2026-canary-onnx-v2/resolve/0a86acfa093b33b8818667820e52fc5eccf27ff8/selected_tags.csv)

节点校验固定哈希。其他反推路线和 TIPO 不读取 WD 模型。模型不随仓库分发。

阿丹原文和作者 OC 预设由使用者提供有权使用的 UTF-8 原文，放入插件 presets/adan_pixel.txt 和 presets/author_oc.txt。缺失时明确提示，不替换规则。公开仓库不分发这些本地原文，测试使用测试文本。DFlow 忠实观察无需这些预设。

[DFlow 原应用](https://github.com/oldiron-666/Dflow) 桥接默认连接本机4173，选择已有卡片并按开关处理、回写，不自动领取整队列。

## 缓存与边界

Flash 缓存位于 ComfyUI/user/soda_prompt_workflow/cache-v2。正在请求或结果未确认时不自动重发；确认失败后改变 refresh 才重新调用。TIPO 缓存位于 tipo-cache-v1，输入、模型、种子、参数或 refresh 改变时重做。

Anima WD + Flash 的逐标签核验决定是标签保留依据。槽位漏词、重复或包含已剔除的词时，由本地程序整理并记录补回/移除清单，不再为机械标签整理调用 Flash；未知标签的补回槽位会标记待人工确认。已有反推缓存可直接复用，修复这类错误无需增加 refresh。视觉决定缺失或格式损坏仍明确报错。

TIPO 使用一次性独立进程，超时或取消时终止，结束后释放进程持有的显存。每个 ComfyUI 事件循环一次只启动一个 TIPO 进程，等待计入超时，模型启动有额外开销。

归档在 ComfyUI/output/soda_prompt_workflow，保留来源、原稿、扩写、手动稿、最终适配稿与省略说明。不同模型各自缓存，切换目标不必重做反推；改变⑤的 refresh 才重做该目标适配。格式校验不保证视觉准确性，请核对人物、服装、动作和场景。规则依据与范围见 [MODEL-PROMPTS.md](MODEL-PROMPTS.md)。

## 开发

```sh
python tests/run_tests.py
```

前端预览与快捷按钮回归测试另用 Node.js 运行：`node --test tests/test_materials_frontend.mjs tests/test_target_frontend.mjs`，不需要安装 npm 依赖。

离线测试不读取真实密钥、不调用付费API、不下载模型。自动测试配置在 `.github/workflow-templates/offline-tests.yml`，使用 CPU PyTorch；当前发布凭据缺少 workflow 权限，模板尚未启用。具有相应权限后将它复制到 `.github/workflows/tests.yml` 可启用。验证边界见 [VERIFICATION.md](VERIFICATION.md)。自建代码采用 MIT，第三方资产见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 与 [SOURCES.md](SOURCES.md)。
