"""Offline checkpoint inference and full held-out simulator evaluation."""
from pathlib import Path
import argparse
import os
from typing import Literal

from pydantic import BaseModel,ConfigDict,Field
from power_grid_env.llm_training import _read_lines,evaluate_predictions,verify_corpus
from power_grid_env.llm_fit import checkpoint_identity
from power_grid_env.study_artifacts import encoded,read_json,seal
from power_grid_env.evidence import package_fingerprint


class InferenceRecipe(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    corpus:str
    checkpoint:str
    output:str
    split:Literal['dev','test']='test'
    max_new_tokens:int=Field(default=512,ge=1,le=8192)
    max_context_tokens:int=Field(default=16384,ge=128,le=131072)
    seed:int=Field(default=31,ge=0,le=2**32-1)
    use_cpu:bool=False


def evaluate_local(document):
    recipe=InferenceRecipe.model_validate(document);out=Path(recipe.output);checkpoint=Path(recipe.checkpoint).resolve()
    if out.exists():raise ValueError('new evaluation output required')
    if not checkpoint.is_dir():raise ValueError('existing local checkpoint required')
    if out.resolve().is_relative_to(checkpoint):raise ValueError('output must be outside input checkpoint')
    checked=verify_corpus(recipe.corpus);identity=checkpoint_identity(checkpoint);code=package_fingerprint()
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1';os.environ['TOKENIZERS_PARALLELISM']='false'
    import torch
    from transformers import AutoTokenizer,AutoModelForCausalLM,set_seed
    tokenizer=AutoTokenizer.from_pretrained(str(checkpoint),local_files_only=True,trust_remote_code=False)
    if not tokenizer.chat_template:raise ValueError('explicit checkpoint chat template required')
    rows=_read_lines(Path(recipe.corpus)/'public'/recipe.split/'prompts.jsonl')
    model=AutoModelForCausalLM.from_pretrained(str(checkpoint),local_files_only=True,trust_remote_code=False)
    device='cpu' if recipe.use_cpu else 'cuda'
    if device=='cuda' and not torch.cuda.is_available():raise ValueError('CUDA unavailable; select use_cpu explicitly')
    model.to(device);model.eval();set_seed(recipe.seed)
    capacity=getattr(model.config,'max_position_embeddings',recipe.max_context_tokens)
    budget=min(recipe.max_context_tokens,capacity) if isinstance(capacity,int) else recipe.max_context_tokens
    predictions=[]
    for row in rows:
        tokens=tokenizer.apply_chat_template(row['prompt'],add_generation_prompt=True,tokenize=True,return_tensors='pt').to(device)
        if tokens.shape[-1]+recipe.max_new_tokens>budget:raise ValueError('held-out prompt exceeds context; truncation forbidden')
        with torch.inference_mode():
            generated=model.generate(tokens,attention_mask=torch.ones_like(tokens),max_new_tokens=recipe.max_new_tokens,
                                     do_sample=False,pad_token_id=tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id)
        text=tokenizer.decode(generated[0,tokens.shape[-1]:],skip_special_tokens=True)
        predictions.append(dict(task_id=row['task_id'],completion=text))
    result=evaluate_predictions(recipe.corpus,predictions,split=recipe.split)
    if package_fingerprint()!=code or checkpoint_identity(checkpoint)!=identity or verify_corpus(recipe.corpus)['manifest_sha256']!=checked['manifest_sha256']:
        raise ValueError('evaluation inputs changed')
    out.mkdir(parents=True)
    (out/'predictions.jsonl').write_bytes(b''.join(encoded(r)+b'\n' for r in predictions))
    (out/'evaluation.json').write_bytes(encoded(result))
    (out/'recipe.json').write_bytes(encoded(dict(recipe=recipe.model_dump(),checkpoint=identity)))
    seal(out);return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input',required=True)
    r=evaluate_local(read_json(parser.parse_args().input))
    print(encoded({k:r[k] for k in ['status','tasks','mean_reward','feasible_fraction','restored_fraction']}).decode())
