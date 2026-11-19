"""Exact inference-only tail replay for frozen-prefix text LoRA witnesses.

No model methods or parameters are replaced. Hooks observe native inputs to the
last decoder blocks; replay calls those same blocks, final norm and LM head.
Only valid while the earlier decoder, embeddings, norm and head remain frozen.
"""
import copy


def detached(value):
    import torch
    if isinstance(value,torch.Tensor):return value.detach().clone()
    if isinstance(value,tuple):return tuple(detached(v) for v in value)
    if isinstance(value,list):return [detached(v) for v in value]
    if isinstance(value,dict):return {k:detached(v) for k,v in value.items()}
    if value is None or isinstance(value,(str,int,float,bool)):return value
    raise ValueError('mutable/non-data decoder cache argument forbidden')


class FrozenPrefixCache:
    def __init__(self,model,last_blocks=2):
        import re
        if type(model).__name__!='Qwen3_5ForConditionalGeneration' or type(last_blocks) is not int or last_blocks<1:
            raise ValueError('qualified native Qwen3.5 text tail required')
        self.model=model
        text=model.model.language_model
        self.layers=list(text.layers)
        self.first=len(self.layers)-last_blocks
        if self.first<1:raise ValueError('nonempty frozen decoder prefix required')
        for name,p in model.named_parameters():
            if not p.requires_grad:continue
            match=re.match(r'^model\.language_model\.layers\.(\d+)\.',name)
            if match is None or int(match[1])<self.first:raise ValueError('trainable parameter affects frozen prefix/norm/head')
        self.entries={}

    def prepare(self,key,batch):
        import torch
        from power_grid_env.qwen35_training import supervised_forward
        if key in self.entries or self.model.training or 'pixel_values' in batch:
            raise ValueError('fresh eval-mode text cache entry required')
        captured={}
        hooks=[]
        def capture(index):
            def hook(module,args,kwargs):
                if len(args)!=1 or kwargs.get('past_key_values') is not None or kwargs.get('use_cache') is not False:
                    raise ValueError('stateless native decoder call required')
                captured[index]=dict(hidden=detached(args[0]) if index==self.first else None,kwargs=detached(kwargs))
            return hook
        try:
            for index in range(self.first,len(self.layers)):
                hooks.append(self.layers[index].register_forward_pre_hook(capture(index),with_kwargs=True))
            with torch.inference_mode():_,native=supervised_forward(self.model,batch)
        finally:
            for hook in hooks:hook.remove()
        if len(captured)!=len(self.layers)-self.first:raise ValueError('complete native tail inputs required')
        self.entries[key]=dict(captured=captured,input_ids=batch['input_ids'].clone(),
                               attention_mask=batch['attention_mask'].clone(),labels=batch['labels'].clone())
        with torch.inference_mode():fast=self.forward(key,batch)
        difference=float((native-fast).abs().max())
        if not torch.equal(native,fast):
            self.entries.pop(key)
            raise ValueError('frozen-prefix replay is not bit-exact: '+str(difference))
        return dict(bit_exact=True,maximum_logit_difference=difference,frozen_prefix_blocks=self.first)

    def forward(self,key,batch):
        import torch
        if self.model.training or torch.is_grad_enabled():raise ValueError('prefix cache is inference-only eval runtime')
        entry=self.entries[key]
        if any(not torch.equal(batch[k],entry[k]) for k in ('input_ids','attention_mask','labels')):
            raise ValueError('cached witness input changed')
        captured=entry['captured']
        hidden=captured[self.first]['hidden'].clone()
        for index in range(self.first,len(self.layers)):
            hidden=self.layers[index](hidden,**detached(captured[index]['kwargs']))
        hidden=self.model.model.language_model.norm(hidden)
        positions=torch.nonzero((batch['labels']!=-100)[0,1:],as_tuple=False).flatten()
        return self.model.lm_head(hidden[:,positions,:])

    def verify(self,key,batch):
        import torch
        from power_grid_env.qwen35_training import supervised_forward
        with torch.inference_mode():
            _,native=supervised_forward(self.model,batch)
            cached=self.forward(key,batch)
        if not torch.equal(native,cached):raise ValueError('updated adapter tail replay no longer exact')
        return dict(bit_exact=True,maximum_logit_difference=float((native-cached).abs().max()))
