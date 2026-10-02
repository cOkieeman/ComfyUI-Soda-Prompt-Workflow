"""Own the TIPO worker lifetime, including timeouts and ComfyUI interruption."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def check_interrupted():
    # Optional during offline tests; installed ComfyUI supplies the interruption API.
    import comfy.model_management
    comfy.model_management.throw_exception_if_processing_interrupted()


async def execute_worker(request, timeout_seconds, check=check_interrupted, worker_path=None, task_name="TIPO"):
    check()
    worker_path = worker_path or Path(__file__).with_name('tipo_worker.py')
    with tempfile.TemporaryDirectory(prefix='soda-tipo-') as temporary:
        request_path = Path(temporary) / 'request.json'
        result_path = Path(temporary) / 'result.json'
        request_path.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
        process = subprocess.Popen([sys.executable, '-X', 'utf8', '-u', str(worker_path), str(request_path), str(result_path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
        deadline = time.monotonic() + timeout_seconds
        try:
            while process.poll() is None:
                check()
                if time.monotonic() >= deadline:
                    action = "本地扩写" if task_name == "TIPO" else "本地任务"
                    raise TimeoutError(f'{task_name} {action}超时，推理进程已停止；未保存缓存。')
                await asyncio.sleep(min(0.1, max(0.001, deadline - time.monotonic())))
            check()
            if process.returncode != 0 or not result_path.is_file():
                raise RuntimeError(f'{task_name} 推理进程异常退出；请检查依赖和模型。')
            result = json.loads(result_path.read_text(encoding='utf-8'))
            if 'error' in result:
                action = "扩写失败" if task_name == "TIPO" else "失败"
                raise RuntimeError(f'{task_name} {action}：' + result['error'])
            return result['result']
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
