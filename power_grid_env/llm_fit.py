"""Optional, offline-only TRL entry point. Core simulation needs no torch/TRL.

Run: python -m power_grid_env.llm_fit --input recipe.json
Only a local checkpoint is accepted; no remote code, tracking or model download.
"""
from pathlib import Path
from typing import Literal
import argparse
import os

from pydantic import BaseModel,ConfigDict,Field

from power_grid_env.llm_training import CorpusVerifier,_read_lines
from power_grid_env.study_artifacts import encoded,read_json,digest
from power_grid_env.evidence import package_fingerprint


class TrainingRecipe(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    mode:Literal['sft','grpo']
    corpus:str
    checkpoint:str
    output:str
    sft_kind:Literal['plans','steps','tools']='plans'
    max_steps:int=Field(default=100,ge=1,le=100000)
    seed:int=Field(default=31,ge=0,le=2**32-1)
    learning_rate:float=Field(default=1e-5,gt=0,le=.01)
    max_length:int=Field(default=16384,ge=128,le=131072)
    max_completion_length:int=Field(default=512,ge=1,le=8192)
    num_generations:int=Field(default=4,ge=2,le=16)
    use_cpu:bool=False


def checked_rows(root,kind,tokenizer,max_length,*,completion_budget=0):
    """Fail before training rather than silently truncate network or labels."""
    rows=_read_lines(Path(root)/'public/train'/(kind+'.jsonl'))
    if not rows:raise ValueError('nonempty training split required')
    result=[]
    for row in rows:
        prompt=row['prompt']
        length=len(tokenizer.apply_chat_template(prompt,tokenize=True,add_generation_prompt=True))
        if completion_budget:
            if length+completion_budget>max_length:raise ValueError('prompt plus completion exceeds context budget; increase context or reduce topology')
            result.append(dict(prompt=prompt,task_id=row['task_id']))
        else:
            full=len(tokenizer.apply_chat_template(prompt+row['completion'],tokenize=True,add_generation_prompt=False))
            if full>max_length:raise ValueError('SFT example exceeds context budget; truncation is forbidden')
            result.append(dict(prompt=prompt,completion=row['completion']))
    return result


def checkpoint_identity(root):
    """Streaming identity includes actual weights, tokenizer and config."""
    import hashlib
    files={}
    for path in sorted(Path(root).rglob('*')):
        if not path.is_file():continue
        value=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(8*1024*1024),b''):value.update(block)
        files[path.relative_to(root).as_posix()]=value.hexdigest()
    if not files:raise ValueError('empty checkpoint')
    return dict(files=files,sha256=digest(encoded(files)))


def train_local(document):
    recipe=TrainingRecipe.model_validate(document)
    checkpoint=Path(recipe.checkpoint).resolve();out=Path(recipe.output)
    if not checkpoint.is_dir():raise ValueError('existing local checkpoint directory required')
    if out.exists():raise ValueError('new training output directory required')
    if out.resolve().is_relative_to(checkpoint):raise ValueError('output must be outside input checkpoint')
    input_identity=checkpoint_identity(checkpoint)
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    os.environ['TOKENIZERS_PARALLELISM']='false'
    verifier=CorpusVerifier(recipe.corpus)
    from transformers import AutoTokenizer,AutoModelForCausalLM
    from datasets import Dataset
    from trl import SFTConfig,SFTTrainer,GRPOConfig,GRPOTrainer
    tokenizer=AutoTokenizer.from_pretrained(str(checkpoint),local_files_only=True,trust_remote_code=False)
    if not tokenizer.chat_template:raise ValueError('checkpoint needs an explicit chat template')
    if tokenizer.pad_token_id is None:
        if tokenizer.eos_token_id is None:raise ValueError('tokenizer needs EOS or PAD')
        tokenizer.pad_token=tokenizer.eos_token
    rows=checked_rows(recipe.corpus,recipe.sft_kind if recipe.mode=='sft' else 'prompts',tokenizer,
                      recipe.max_length,completion_budget=recipe.max_completion_length if recipe.mode=='grpo' else 0)
    model=AutoModelForCausalLM.from_pretrained(str(checkpoint),local_files_only=True,trust_remote_code=False)
    capacity=getattr(model.config,'max_position_embeddings',None)
    if isinstance(capacity,int) and recipe.max_length>capacity:raise ValueError('requested context exceeds checkpoint position capacity')
    common=dict(output_dir=str(out),max_steps=recipe.max_steps,learning_rate=recipe.learning_rate,
                seed=recipe.seed,data_seed=recipe.seed,use_cpu=recipe.use_cpu,bf16=False,fp16=False,
                report_to=[],save_strategy='no',logging_steps=1,gradient_checkpointing=False)
    if recipe.mode=='sft':
        config=SFTConfig(**common,per_device_train_batch_size=1,max_length=recipe.max_length,
                         completion_only_loss=True,packing=False)
        trainer=SFTTrainer(model=model,args=config,train_dataset=Dataset.from_list(rows),processing_class=tokenizer)
    else:
        config=GRPOConfig(**common,per_device_train_batch_size=recipe.num_generations,
                          num_generations=recipe.num_generations,max_prompt_length=recipe.max_length-recipe.max_completion_length,
                          max_completion_length=recipe.max_completion_length,use_vllm=False,beta=0.,
                          remove_unused_columns=False)
        trainer=GRPOTrainer(model=model,args=config,train_dataset=Dataset.from_list(rows),
                            processing_class=tokenizer,reward_funcs=verifier.trl_reward)
    trainer.train();trainer.save_model(str(out/'checkpoint'));tokenizer.save_pretrained(str(out/'checkpoint'))
    if package_fingerprint()!=verifier.code:raise ValueError('producer changed during training; output unqualified')
    if checkpoint_identity(checkpoint)!=input_identity:raise ValueError('input checkpoint changed during training')
    # No task objects or private evaluator traces in the model directory.
    import importlib.metadata
    receipt=dict(schema='power-grid-env.llm-training/v1',mode=recipe.mode,source_manifest_sha256=verifier.identity,
                 producer_sha256=verifier.code,recipe=recipe.model_dump(),steps=trainer.state.global_step,
                 versions={p:importlib.metadata.version(p) for p in ['torch','transformers','trl','datasets','accelerate']},
                 log_history=trainer.state.log_history,quality_or_generalization_claimed=False,
                 external_tracking=False,input_checkpoint=input_identity,output_checkpoint=checkpoint_identity(out/'checkpoint'))
    (out/'training.json').write_bytes(encoded(receipt));return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input',required=True)
    args=parser.parse_args();result=train_local(read_json(args.input))
    print(encoded(dict(mode=result['mode'],steps=result['steps'],quality_or_generalization_claimed=False)).decode())


if __name__=='__main__':main()
