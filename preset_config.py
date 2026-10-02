"""Author presets are local configuration, supplied by the user."""
from pathlib import Path


def read_preset(name):
    path = Path(__file__).parent / 'presets' / name
    if not path.is_file():
        raise RuntimeError(f'缺少本地预设 {name}；请按 README 将有权使用的预设放入 presets 目录。')
    return path.read_text(encoding='utf-8')
