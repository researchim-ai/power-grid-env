"""Native local Qwen3.5 processor checks; no weights, forward pass or training."""
import argparse
import importlib.metadata
from pathlib import Path
import sys


def run(exports,expected_manifest,target,out,runtime_dir=None,threads=4):
    if runtime_dir:sys.path.insert(0,str(Path(runtime_dir).resolve()))
    from power_grid_env.study_artifacts import read_json,encoded,verify,seal,digest
    from power_grid_env.vlm_batches import local_qwen35_processor,VLMExportDataset
    from power_grid_env.evidence import package_fingerprint
    import torch
    if not 1<=threads<=16:raise ValueError('bounded CPU thread count required')
    torch.set_num_threads(threads)
    root=Path(exports).resolve()
    if verify(root)!=expected_manifest:raise ValueError('trusted export collection differs')
    collection=read_json(root/'receipt.json');definition=read_json(target)
    checkpoint=Path(definition['local_checkpoint']).resolve()
    files={str(p.name):digest(p.read_bytes()) for p in checkpoint.iterdir() if p.name in ('config.json','tokenizer.json','tokenizer_config.json','preprocessor_config.json','video_preprocessor_config.json','vocab.json','merges.txt','chat_template.jinja')}
    processor=local_qwen35_processor(checkpoint,definition['config_sha256']);rows=[]
    for entry in collection['exports']:
        data=VLMExportDataset(root/entry['path'],entry['manifest_sha256'])
        # Every image-only observation; one record of each extra modality/split.
        indices=range(len(data)) if entry['mode']=='image' else range(1)
        for i in indices:
            batch,meta=data.pack(processor,i)
            meta['export']=entry['path'];rows.append(meta)
            if data.kind=='sft':
                if meta['assistant_tokens']<=0 or meta['prompt_tokens']<=0:raise ValueError('missing assistant-only labels')
            elif 'labels' in batch:raise ValueError('held-out prompt carried labels')
            del batch
            if len(rows)%8==0:print('native processor checks',len(rows),flush=True)
    if len(rows)!=69 or sum(r['mode']=='image' for r in rows)!=60 or sum(r['kind']=='sft' for r in rows)!=39:raise ValueError('native processor inventory differs')
    if verify(root)!=expected_manifest or files!={str(p.name):digest(p.read_bytes()) for p in checkpoint.iterdir() if p.name in files}:raise ValueError('export or local processor files changed')
    import power_grid_env.vlm_batches as runtime
    if runtime_dir and not Path(runtime.__file__).resolve().is_relative_to(Path(runtime_dir).resolve()):raise ValueError('wrong runtime import')
    result=dict(passed=True,exporter_code_sha256=package_fingerprint(),export_collection_manifest_sha256=expected_manifest,rows=rows,
        checks=69,image_observations=60,assistant_mask_checks=39,heldout_prompt_checks=30,
        processor=type(processor).__name__,processor_files_sha256=files,config_sha256=definition['config_sha256'],
        versions={p:importlib.metadata.version(p) for p in ('transformers','torch','Pillow','tokenizers')},
        maximum_tokens=max(r['tokens'] for r in rows),maximum_images=max(r['images'] for r in rows),
        weights_loaded=False,forward_pass=False,model_trained=False,remote_origin_authenticated=False,
        scope='CPU native processor, visual tensors, prompt-prefix equality and assistant-only loss masks; not training or model accuracy.')
    output=Path(out);output.mkdir(parents=True,exist_ok=False);(output/'receipt.json').write_bytes(encoded(result));seal(output)
    return {k:v for k,v in result.items() if k not in ('rows','processor_files_sha256')}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--exports',required=True);p.add_argument('--expected-manifest',required=True);p.add_argument('--target',required=True);p.add_argument('--out',required=True);p.add_argument('--runtime-dir');p.add_argument('--threads',type=int,default=4)
    print(run(**vars(p.parse_args())))
