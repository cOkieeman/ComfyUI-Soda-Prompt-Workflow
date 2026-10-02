"""Run the official TIPO node in a disposable process, outside ComfyUI's GPU state."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys


def execute_request(request):
    sys.path.insert(0, request['comfy_root'])
    # API schema imports must not initialize ComfyUI's own GPU models here.
    sys.argv = ['soda-tipo-worker', '--cpu']
    import comfy.options
    comfy.options.enable_args_parsing()
    import folder_paths
    from comfy.cli_args import args

    args.cuda_device = request['cuda_device']
    folder_paths.models_dir = str(Path(request['model_path']).parent.parent)
    os.environ['TIPO_NO_AUTO_INSTALL'] = '1'
    plugin = Path(request['plugin_root'])
    spec = importlib.util.spec_from_file_location('soda_tipo_backend', plugin / '__init__.py',
        submodule_search_locations=[str(plugin)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
    backend = importlib.import_module('soda_tipo_backend.nodes.tipo')
    parameters = request['parameters']
    output = backend.TIPO.execute(tags=request['tags'], nl_prompt=request['natural'],
        ban_tags=parameters['ban_tags'], tipo_model=Path(request['model_path']).name,
        format='<|extended|>', width=parameters['width'], height=parameters['height'],
        temperature=parameters['temperature'], top_p=0.95, min_p=0.05, top_k=80,
        tag_length=parameters['length'], nl_length=parameters['length'],
        seed=parameters['seed'], device=parameters['device'])
    formatted, _, unformatted, _, tags, natural = output.result
    return {'description': formatted.strip(), 'generated_tags': tags,
            'generated_nl': natural, 'unformatted': unformatted}


def main():
    request_path, result_path = map(Path, sys.argv[1:])
    try:
        result = {'result': execute_request(json.loads(request_path.read_text(encoding='utf-8')))}
    except Exception as error:
        result = {'error': f'{type(error).__name__}: {error}'}
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
