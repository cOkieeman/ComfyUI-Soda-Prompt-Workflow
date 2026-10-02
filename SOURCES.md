# 来源与适配

- [anima-tagger](https://github.com/Vincent54122/anima-tagger)：提交 c8adfc1919765976e57820341be29a7c440ea929。MIT 代码、四份规则与 T5 tokenizer 保存在 vendor/anima_tagger，原文件未改动。保留标签分档、低分复核、八槽顺序、折叠与冲突检查、创作方案和 K2 扩写。
- [DFlow](https://github.com/oldiron-666/Dflow)：核对提交 c5d4c367dbcd097289ceb43635e67c3657c95127。本包自行实现本机桥接、忠实观察和中文扩写适配，不声称包含作者私有预设或完整复制应用。
- [z-tipo-extension](https://github.com/KohakuBlueleaf/z-tipo-extension)：验证提交 6132862978021727284215bc2d5fe5257a872709。独立进程直接调用官方 TIPO V3，标签与自然语言分别传入，没有复制其推理实现。
- [TIPO 模型](https://huggingface.co/KBlueLeaf/TIPO-v2.1-1B-A200M)：使用者自行提供，验证 FP16 / Q8_0 GGUF，仓库不附模型。
- 作者画廊：复用已安装的 DanbooruGalleryNode 与 Comfy.DanbooruGallery 原生界面，按需执行，没有复制作者插件代码。
- 阿丹与作者 OC 原文：本地配置。缺少明确再分发许可的正文不进入公开仓库；使用者自行提供有权使用的原文，原样发送。测试使用测试专用文本。

元数据读取、开关编排、记录、输入拆分、缓存和进程管理由本包实现。复杂 PNG 图不推断未知节点运行结果。512 token 是本工作流的兼容预算，实际下游行为取决于编码节点。格式校验不保证视觉准确性。
