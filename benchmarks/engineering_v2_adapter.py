"""Owner-pinned experimental trainer-v2 checkpoint loading for evaluation only."""
import hashlib
from pathlib import Path
from power_grid_env.study_artifacts import verify,read_json


def receipt(root,manifest,target_sha,contract):
    if verify(root)!=manifest:raise ValueError('v2 adapter owner manifest differs')
    from power_grid_env.evidence import package_fingerprint
    r=read_json(Path(root)/'summary.json')
    if not (Path(root)/'adapter.safetensors').is_file() or not isinstance(r.get('adapter_names'),list) or not r['adapter_names']:raise ValueError('v2 adapter weights/module inventory missing')
    if (r.get('schema')!='power-grid-env.trainer-v2/v1' or r.get('identity',{}).get('target_sha256')!=target_sha or r.get('identity',{}).get('core_sha256')!=package_fingerprint() or
        r.get('identity',{}).get('trainer_sha256')!=hashlib.sha256(Path(__file__).with_name('engineering_trainer_v2.py').read_bytes()).hexdigest() or
        r.get('recipe',{}).get('contract')!=contract.model_dump() or
        type(r.get('completed_steps')) is not int or r['completed_steps']<=0 or
        r.get('completed_steps')!=r.get('recipe',{}).get('steps') or r.get('schedule_complete') is not True or
        r.get('frozen_base_unchanged') is not True or r.get('adapter_roundtrip_exact') is not True):
        raise ValueError('v2 checkpoint/contract identity or completeness differs')
    return r


def restore(model,root,metadata):
    from safetensors.torch import load_file
    from benchmarks.engineering_trainer_v2 import LoraSpec,attach_language_lora
    from power_grid_env.qwen35_training import restore_adapters
    names=attach_language_lora(model,LoraSpec.model_validate(metadata['recipe']['lora']))
    if names!=metadata['adapter_names']:raise ValueError('v2 adapter module selection differs')
    restore_adapters(model,load_file(str(Path(root)/'adapter.safetensors')))
    model.eval();model.requires_grad_(False)
