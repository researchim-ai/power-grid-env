"""Real TRL CPU SFT/GRPO plumbing check; NOT a useful language model.

Whole-message tokens deliberately make a tiny offline bandit. This verifies
optimizer/reward wiring without downloading any model. It tests no engineering
reasoning, natural-language ability, generalization or training convergence.
Run in an optional torch/TRL environment with the repository on PYTHONPATH.
"""
from pathlib import Path
import argparse
import os


def run(corpus,out):
    os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
    os.environ['TOKENIZERS_PARALLELISM']='false'
    import torch
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from transformers import PreTrainedTokenizerFast,GPT2Config,GPT2LMHeadModel
    from power_grid_env.llm_training import _read_lines
    from power_grid_env.llm_fit import train_local
    from power_grid_env.llm_inference import evaluate_local
    from power_grid_env.study_artifacts import encoded,seal
    out=Path(out)
    if out.exists():raise ValueError('new smoke output required')
    out.mkdir(parents=True);checkpoint=out/'tiny-input';checkpoint.mkdir()
    torch.manual_seed(31);torch.set_num_threads(1)
    good=[r['completion'][0]['content'] for r in _read_lines(Path(corpus)/'public/train/plans.jsonl')]
    vocabulary={text:i for i,text in enumerate(dict.fromkeys(['<UNK>','<PAD>','<EOS>','<SEP>',*good,'{"actions":[]}','invalid']))}
    raw=Tokenizer(WordLevel(vocabulary,unk_token='<UNK>'))
    tokenizer=PreTrainedTokenizerFast(tokenizer_object=raw,unk_token='<UNK>',pad_token='<PAD>',eos_token='<EOS>',additional_special_tokens=['<SEP>'])
    tokenizer.chat_template="{% for message in messages %}{{ message['content'] }}{% if message['role'] == 'assistant' %}{{ eos_token }}{% else %}{{ '<SEP>' }}{% endif %}{% endfor %}"
    tokenizer.save_pretrained(checkpoint)
    model=GPT2LMHeadModel(GPT2Config(vocab_size=len(tokenizer),n_positions=128,n_ctx=128,n_embd=16,n_layer=1,n_head=1,
                                   eos_token_id=tokenizer.eos_token_id,pad_token_id=tokenizer.pad_token_id,bos_token_id=tokenizer.eos_token_id))
    model.save_pretrained(checkpoint)
    base=dict(corpus=str(corpus),max_steps=2,max_length=128,max_completion_length=8,num_generations=4,use_cpu=True,learning_rate=.001)
    sft=train_local(dict(**base,mode='sft',checkpoint=str(checkpoint),output=str(out/'sft')))
    grpo_recipe=dict(base,max_steps=8,max_completion_length=1,num_generations=16)
    grpo=train_local(dict(**grpo_recipe,mode='grpo',checkpoint=str(out/'sft/checkpoint'),output=str(out/'grpo')))
    informative=sum(r.get('frac_reward_zero_std',1)<1 for r in grpo['log_history'])
    nonzero=sum(r.get('grad_norm',0)>0 for r in grpo['log_history'])
    evaluation=evaluate_local(dict(corpus=str(corpus),checkpoint=str(out/'grpo/checkpoint'),output=str(out/'heldout'),
                                   max_new_tokens=1,max_context_tokens=128,use_cpu=True))
    result=dict(schema='power-grid-env.llm-cpu-smoke/v1',sft_steps=sft['steps'],grpo_steps=grpo['steps'],
                actual_optimizer_steps=True,whole_message_token_bandit=True,informative_grpo_batches=informative,
                nonzero_grpo_gradient_steps=nonzero,
                useful_trained_llm=False,engineering_reasoning_or_generalization_tested=False,
                heldout_tasks=evaluation['tasks'],heldout_mean_reward=evaluation['mean_reward'],
                grpo_logs=grpo['log_history'])
    (out/'smoke.json').write_bytes(encoded(result))
    # Checkpoints are tiny here, so the standard bounded artifact seal applies.
    seal(out);return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--corpus',required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args();print(run(args.corpus,args.out))
