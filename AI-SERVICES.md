# 统一 AI 服务配置

在①素材入口或⑤最终输出节点底部点击“AI 服务配置 / 切换模型”。

1. 选择 DeepSeek、GLM/智谱、Gemini 或自定义兼容服务。
2. 填写 Base URL、文字模型、看图模型和该服务的 API Key。
3. 点击“保存并启用当前服务”，下次运行使用此服务。保存本身不请求模型。
4. 以后选择已配置的另一项，再保存启用，无需逐个节点修改。

| 服务 | 默认 Base URL | 模型设置 |
|---|---|---|
| DeepSeek | `https://api.deepseek.com` | 默认保留 `deepseek-flash`；可修改 |
| GLM / 智谱 | `https://open.bigmodel.cn/api/paas/v4` | 按账户实际可用型号填写文字与视觉模型 |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | 使用 Google 的 OpenAI 兼容接口，填写可用 Gemini 型号 |
| 自定义 | 自行填写 | 服务必须支持 Chat Completions；反推还需支持 `image_url` 内联图片 |

也可直接填写完整 `/chat/completions` 地址，不会重复追加路径。远程地址使用 HTTPS，本机兼容服务支持 HTTP。

地址依据：[DeepSeek 官方示例](https://api-docs.deepseek.com/api_samples/chat_curl/)、[智谱官方 SDK](https://github.com/zai-org/z-ai-sdk-python/blob/main/README_CN.md)、[Gemini 官方兼容接口](https://ai.google.dev/gemini-api/docs/openai)。此入口处理文字生成和图片理解，不调用图像生成接口；不支持原生 Anthropic Messages 或 Gemini generateContent 地址。

文字模型用于创作、OC、远程扩写和最终适配；看图模型用于阿丹、DFlow、WD 标签复核及旧看图节点。两栏可填同一个多模态模型，也可分开。未配置看图模型时，图片反推明确提示，不自动用文字模型替代。

新配置存入 `ComfyUI/user/soda_prompt_workflow/ai_services.json`，不进入工作流、缓存、记录或公开仓库。读取配置只返回“是否已配置”，不返回密钥。保留默认 DeepSeek 时继续读取原 secrets.toml 或 DEEPSEEK_API_KEY；其他服务不会借用 DeepSeek 的密钥。

API Key 留空在地址不变时保留；换地址需重新填写。开启“严格核对返回模型名称”时，返回名称不一致会停止；关闭时允许版本别名并在 API 记录中提示差异。DeepSeek 默认严格，GLM/Gemini 默认允许别名。模型返回格式仍需符合各阶段要求，不因换服务放宽结构检查。

服务地址、型号和当前配置变化会更新相关节点的执行缓存，并区分持久响应缓存。原默认 Flash 缓存仍可用。失败或输出被截断时没有自动重试；确认后改变 refresh 才重新调用。TIPO、原词读取、本地前后缀和关闭的分支不请求 AI。

本次验证覆盖本机兼容 API 的实际 HTTP 请求和完整 ComfyUI 运行；未使用真实 GLM/Gemini 密钥测试，无法保证任意型号、网关或账户权限均兼容。
