"""Local read-only Qwen candidate. No evaluator import and no labels in input.

This text-only tool protocol does not qualify visual reasoning or SFT/GRPO.
Run in the already installed Qwen environment, never install remote model code.
"""
import argparse
import json
import sys


def pack(processor, messages, context_tokens, new_tokens):
    if not isinstance(messages, list) or not messages:
        raise ValueError('messages required')
    if any(not isinstance(m, dict) or set(m) != {'role', 'content'} or
           m['role'] not in ('system', 'user', 'assistant') or not isinstance(m['content'], str)
           for m in messages):
        raise ValueError('text-only public messages required')
    batch = processor.apply_chat_template(messages, tokenize=True,
        add_generation_prompt=True, return_dict=True, return_tensors='pt', enable_thinking=False)
    length = int(batch['input_ids'].shape[1])
    if length + new_tokens > context_tokens:
        raise ValueError('context_budget_exceeded: history is never silently truncated')
    return batch, length


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--target', required=True)
    p.add_argument('--target-sha256', required=True)
    p.add_argument('--context-tokens', type=int, default=16384)
    p.add_argument('--new-tokens', type=int, default=192)
    p.add_argument('--threads', type=int, default=4)
    p.add_argument('--public-view',choices=['raw','compact-pf-v1'],default='raw')
    p.add_argument('--processor-only', action='store_true')
    p.add_argument('--adapter')
    p.add_argument('--adapter-manifest-sha256')
    args = p.parse_args()
    if not 1 <= args.threads <= 16 or not 16 <= args.new_tokens <= 512 or not 1024 <= args.context_tokens <= 32768:
        p.error('unsupported resource budget')
    if bool(args.adapter) != bool(args.adapter_manifest_sha256) or (args.adapter and args.processor_only):
        p.error('adapter and owner manifest must be provided together for inference')
    from power_grid_env.qwen35_training import local_target, load_native
    target = local_target(args.target, args.target_sha256)
    receipt = None
    if args.adapter:
        from benchmarks.engineering_adapter import adapter_receipt
        receipt = adapter_receipt(args.adapter,args.adapter_manifest_sha256,args.target_sha256)
    if args.processor_only:
        from power_grid_env.vlm_batches import local_qwen35_processor
        processor = local_qwen35_processor(target['local_checkpoint'], target['config_sha256'])
        model = None
    else:
        processor, model = load_native(target, args.threads)
        if receipt is not None:
            from benchmarks.engineering_adapter import restore
            restore(model,args.adapter,receipt)
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if set(request) != {'messages'}:
                raise ValueError('only public messages accepted')
            messages=request['messages']
            if args.public_view=='compact-pf-v1':
                from benchmarks.engineering_observations import referenced_messages
                messages=referenced_messages(messages)
            batch, length = pack(processor, messages, args.context_tokens, args.new_tokens)
            if model is None:
                reply = dict(text='', input_tokens=length, output_tokens=0, token_budget_exhausted=False)
            else:
                import torch
                with torch.inference_mode():
                    result = model.generate(**batch, do_sample=False, num_beams=1,
                        max_new_tokens=args.new_tokens, use_cache=True, logits_to_keep=1,
                        pad_token_id=processor.tokenizer.pad_token_id,
                        eos_token_id=processor.tokenizer.eos_token_id)
                completion = result[0, length:]
                reply = dict(text=processor.tokenizer.decode(completion, skip_special_tokens=True),
                    input_tokens=length, output_tokens=len(completion),
                    token_budget_exhausted=len(completion) >= args.new_tokens)
            print(json.dumps(reply), flush=True)
        except Exception as exc:
            print(json.dumps(dict(error=type(exc).__name__, detail=str(exc)[:500])), flush=True)


if __name__ == '__main__':
    main()
