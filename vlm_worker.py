"""Disposable local vision worker. Only inline image bytes are accepted; no network."""
import json
import ctypes
import os
from pathlib import Path
import sys
import time

SCHEMA = {"type": "object", "properties": {
    "tags": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 64}, "minItems": 1, "maxItems": 32},
    "nl": {"type": "string", "minLength": 1, "maxLength": 700},
    "uncertainties": {"type": "array", "items": {"type": "string", "maxLength": 100}, "maxItems": 4},
}, "required": ["tags", "nl", "uncertainties"], "additionalProperties": False}


def execute_request(request):
    if not request["image"].startswith("data:image/"):
        raise ValueError("本地反推只接收工作流内的图片数据。")
    if request.get("runtime_directory"):
        sys.path.insert(0, request["runtime_directory"])
    # Torch supplies the portable installation's CUDA DLL search paths on Windows.
    import torch
    import llama_cpp
    try:
        from llama_cpp.llama_chat_format import Qwen35ChatHandler
    except ImportError:
        raise RuntimeError("当前 llama-cpp 不支持 Qwen3.5；请配置新版独立运行库。") from None
    # 0.4.x wheels register CUDA plugins when a Llama instance starts. Register
    # first so a pre-load capability check does not incorrectly report CPU-only.
    from llama_cpp._ggml import ggml_backend_load_all_from_path
    llama_cpp.llama_backend_init()
    library = Path(llama_cpp.__file__).resolve().parent / "lib"
    ggml_backend_load_all_from_path(ctypes.c_char_p(str(library).encode("utf-8")))
    device = request["device"]
    warnings = []
    available = torch.cuda.is_available() and llama_cpp.llama_supports_gpu_offload()
    if device == "cuda" and not available:
        raise ValueError("CUDA 不可用，请检查运行库或选择 CPU。")
    if device == "auto":
        needed = Path(request["model_path"]).stat().st_size * 1.05 + Path(request["mmproj_path"]).stat().st_size * 1.15 + 1024**3
        device = "cuda" if available and torch.cuda.mem_get_info()[0] >= needed else "cpu"
        if device == "cpu":
            warnings.append("自动选择 CPU：CUDA 不可用或空闲显存不足；没有卸载其他程序的模型。")
    start = time.monotonic()
    handler = Qwen35ChatHandler(mmproj_path=request["mmproj_path"], enable_thinking=False,
        use_gpu=device == "cuda", image_min_tokens=1024, image_max_tokens=1024, verbose=False)
    model = llama_cpp.Llama(model_path=request["model_path"], chat_handler=handler,
        n_gpu_layers="all" if device == "cuda" else 0, n_ctx=request["context_size"], n_batch=512,
        n_ubatch=256, n_threads=max(1, (os.cpu_count() or 4) // 2), seed=request["seed"], use_mmap=True, verbose=False)
    try:
        messages = [{"role": "system", "content": request["rule"]}, {"role": "user", "content": [
            {"type": "text", "text": "Describe the visible image." + ("\nDeliberate user requirements:\n" + request["instruction"] if request["instruction"] else "")},
            {"type": "image_url", "image_url": {"url": request["image"]}}]}]
        reply = model.create_chat_completion(messages=messages, max_tokens=request["max_tokens"],
            temperature=0.2, top_p=0.9, seed=request["seed"], response_format={"type": "json_object", "schema": SCHEMA})
        choice = reply["choices"][0]
        if choice["finish_reason"] == "length":
            raise ValueError("本地反推达到输出长度上限；请提高最大输出 token 后重试，未保存残缺结果。")
        text = choice["message"].get("content")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("本地反推返回空文本。")
        return {"text": text, "model": Path(request["model_path"]).name, "mmproj": Path(request["mmproj_path"]).name,
            "device": device, "runtime_version": llama_cpp.__version__, "usage": reply.get("usage", {}),
            "elapsed_seconds": round(time.monotonic() - start, 3), "warnings": warnings}
    finally:
        model.close()


def main():
    request_path, result_path = map(Path, sys.argv[1:])
    try:
        result = {"result": execute_request(json.loads(request_path.read_text(encoding="utf-8")))}
    except Exception as error:
        result = {"error": str(error)}
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
