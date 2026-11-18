"""Owner-pinned experimental action adapter loading; integrity is not quality."""
from pathlib import Path
from power_grid_env.study_artifacts import verify, read_json


def adapter_receipt(root, manifest_sha256, target_sha256):
    if verify(root) != manifest_sha256:
        raise ValueError('owner adapter manifest differs')
    receipt = read_json(Path(root)/'summary.json')
    schema=receipt.get('schema')
    steps=receipt.get('optimizer_steps')
    complete_schedule=(schema=='power-grid-env.engineering-action-training/v1' and
        receipt.get('schedule_complete') is True and isinstance(receipt.get('schedule'),list) and
        type(steps) is int and steps>0 and len(receipt['schedule'])==steps)
    qualification=(schema=='power-grid-env.engineering-sft-qualification/v1' and type(steps) is int and steps==1)
    if (not (qualification or complete_schedule) or
            receipt.get('target_sha256') != target_sha256 or
            receipt.get('frozen_unchanged') is not True or
            receipt.get('adapter_roundtrip_exact') is not True or
            receipt.get('eligible_for_gold_sft') is not False or
            not isinstance(receipt.get('training_plan'),dict) or
            not isinstance(receipt.get('adapter_names'),list)):
        raise ValueError('incompatible experimental adapter receipt')
    if not (Path(root)/'adapter.safetensors').is_file():
        raise ValueError('adapter weights missing')
    return receipt


def restore(model, root, receipt):
    from safetensors.torch import load_file
    from power_grid_env.qwen35_training import AdapterPlan, attach_adapters, restore_adapters
    plan = AdapterPlan.model_validate(receipt['training_plan'])
    names = attach_adapters(model,plan)
    if names != receipt['adapter_names']:
        raise ValueError('adapter module identities differ')
    restore_adapters(model,load_file(str(Path(root)/'adapter.safetensors')))
    model.eval()
    model.requires_grad_(False)
