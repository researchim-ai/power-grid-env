"""Owner-run tool episodes. Candidate receives public messages only, never state.

Subprocess separation is a data-flow boundary, not an OS security sandbox.
"""
import argparse
import copy
import hashlib
import json
import os
import selectors
import subprocess
import time
from pathlib import Path
from benchmarks import engineering_agent as env
from power_grid_env.study_artifacts import seal

class CandidateError(RuntimeError):
    def __init__(self, error, detail):
        super().__init__(str(detail)[:500])
        self.code = 'context_budget' if str(detail).startswith('context_budget_exceeded:') else 'candidate_error'
        self.candidate_error = error


SYSTEM = ('You are a power-grid engineering assistant. Choose exactly one allowed tool per turn. '
          'Return only JSON: {"name":"tool_name","arguments":{...}}. '
          'Use public observations, preserve owner requirements, and obtain fresh simulation before submit. '
          'No markdown or extra keys. A failed calculation is not success.')


def strict_json(text):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError('duplicate JSON key')
            result[k] = v
        return result
    return json.loads(text, object_pairs_hook=pairs,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def parse_action(text, allowed):
    if not isinstance(text, str) or len(text.encode()) > 8192:
        raise ValueError('completion exceeds action byte limit')
    value = strict_json(text)
    if not isinstance(value, dict) or set(value) != {'name', 'arguments'}:
        raise ValueError('one name/arguments action required')
    if not isinstance(value['name'], str) or value['name'] not in allowed or value['name'] == 'export':
        raise ValueError('action not allowed')
    if not isinstance(value['arguments'], dict):
        raise ValueError('arguments must be an object')
    return value


def public_reply(response):
    """Avoid repeating the task schemas every turn; never discard measurements."""
    reply = copy.deepcopy(response)
    if isinstance(reply.get('observation'), dict):
        reply['observation'].pop('task', None)
    return reply


def rollout(identifier, policy, max_turns=32, repeat_limit=0):
    if not 1 <= max_turns <= 32:
        raise ValueError('turn budget must be 1..32')
    if type(repeat_limit) is not int or repeat_limit not in (0,2,3,4):
        raise ValueError('repeat guard must be disabled or 2..4')
    last_public_key=None;repeated=0
    state = env.reset(identifier, allow_private=True)
    task = env.task(identifier, allow_private=True)
    public = env.observation(state, task)
    messages = [dict(role='system', content=SYSTEM),
                dict(role='user', content=json.dumps(public, ensure_ascii=False))]
    turns = []
    reason = 'turn_budget'
    for index in range(max_turns):
        tick = time.monotonic()
        try:
            generated = policy(copy.deepcopy(messages))
        except Exception as exc:
            turns.append(dict(turn=index, error=type(exc).__name__,
                error_code=getattr(exc,'code','transport_or_policy_failure'),detail=str(exc)[:500],
                elapsed_s=time.monotonic()-tick))
            reason = 'policy_failure'
            break
        text = generated.get('text') if isinstance(generated, dict) else generated
        meta = {k: v for k, v in generated.items() if k != 'text'} if isinstance(generated, dict) else {}
        row = dict(turn=index, completion=text, generation=meta, elapsed_s=time.monotonic()-tick)
        try:
            action = parse_action(text, public['task']['tools'])
        except (ValueError, TypeError) as exc:
            row['protocol_error'] = str(exc)
            # Invalid protocol attempts consume the environment call budget too.
            state, response = env.apply(state, '__invalid_action__', {}, allow_private=True)
        else:
            state, response = env.apply(state, action['name'], action['arguments'], allow_private=True)
            row['action'] = action
        row['response'] = public_reply(response)
        turns.append(row)
        messages.append(dict(role='assistant', content=text if isinstance(text, str) else ''))
        messages.append(dict(role='user', content=json.dumps(row['response'], ensure_ascii=False)))
        if state['finished']:
            reason = 'submitted' if state['terminal'] and 'call_budget' not in state['terminal'].get('violations', []) else 'call_budget'
            break
        if repeat_limit and row.get('action'):
            payload={k:v for k,v in response.items() if k!='observation'}
            key=env.sha(dict(action=row['action'],payload=payload,revision=state['revision']))
            repeated=repeated+1 if key==last_public_key else 1
            last_public_key=key
            if repeated>=repeat_limit:
                reason='repeated_public_observation'
                break
    terminal = state['terminal'] or {}
    return dict(task_id=identifier, split=task['split'], family_sha256=task['family_sha256'],
                kind=task['kind'], success=bool(terminal.get('success', False)),
                reward=float(terminal.get('reward', 0)), stop_reason=reason,
                calls=state['calls'], simulations=state['simulations'], turns=turns,
                repeat_limit=repeat_limit,eligible_for_gold_sft=False)


class ProcessPolicy:
    """Bounded line protocol; only public messages cross into candidate process."""
    def __init__(self, command, timeout=180):
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=None, bufsize=0)
        self.timeout = timeout
        self.command = list(command)
        self.identity = dict(command=command, response_timeout_s=timeout,
            transport='public-json-lines-v1', os_sandbox=False,
            protocol_source_sha256=hashlib.sha256(Path(__file__).with_name('engineering_protocol.py').read_bytes()).hexdigest(),
            protocol_policy_sha256=hashlib.sha256(Path(__file__).with_name('engineering_protocol_policy.py').read_bytes()).hexdigest(),
            observation_view_sha256=hashlib.sha256(Path(__file__).with_name('engineering_observations.py').read_bytes()).hexdigest(),
            adapter_loader_sha256=hashlib.sha256(Path(__file__).with_name('engineering_adapter.py').read_bytes()).hexdigest(),
            policy_source_sha256=hashlib.sha256(Path(__file__).with_name('engineering_qwen_policy.py').read_bytes()).hexdigest())
        self.pending = b''
        os.set_blocking(self.process.stdin.fileno(), False)

    def begin_episode(self):
        if self.process.poll() is not None:
            self.close()
            self.process = subprocess.Popen(self.command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=None,bufsize=0)
            self.pending = b''
            os.set_blocking(self.process.stdin.fileno(),False)

    def __call__(self, messages):
        if self.process.poll() is not None:
            raise RuntimeError('candidate process unavailable')
        try:
            return self._request(messages)
        except Exception:
            # Never attribute a late timed-out response to a different episode.
            self.close()
            raise

    def _request(self, messages):
        request = (json.dumps(dict(messages=messages))+'\n').encode()
        if len(request) > 2_000_000:
            raise ValueError('candidate request exceeds limit')
        deadline = time.monotonic()+self.timeout
        sent = 0
        with selectors.DefaultSelector() as writer:
            writer.register(self.process.stdin, selectors.EVENT_WRITE)
            while sent < len(request):
                if not writer.select(max(0, deadline-time.monotonic())):
                    raise TimeoutError('candidate input timeout')
                try:
                    sent += os.write(self.process.stdin.fileno(), request[sent:sent+65536])
                except BlockingIOError:
                    continue
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while b'\n' not in self.pending:
                if not selector.select(max(0, deadline-time.monotonic())):
                    raise TimeoutError('candidate response timeout')
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError('candidate process closed')
                self.pending += chunk
                if len(self.pending) > 65536:
                    raise ValueError('candidate response exceeds limit')
        line, self.pending = self.pending.split(b'\n', 1)
        value = strict_json(line.decode())
        if isinstance(value,dict) and set(value)=={'error','detail'} and isinstance(value['error'],str) and isinstance(value['detail'],str):
            raise CandidateError(value['error'],value['detail'])
        if not isinstance(value, dict) or set(value) - {'text', 'input_tokens', 'output_tokens', 'token_budget_exhausted','protocol_completion'}:
            raise ValueError('invalid candidate envelope')
        return value

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()


def evaluate(policy, out, split='dev', max_turns=32, repeat_limit=0):
    if split not in ('train', 'dev', 'test'):
        raise ValueError('invalid split')
    root = Path(out)
    root.mkdir(parents=True, exist_ok=False)
    rows = []
    for task in env.registry():
        if task['split'] != split:
            continue
        if hasattr(policy, 'begin_episode'):
            policy.begin_episode()
        row = rollout(task['id'], policy, max_turns, repeat_limit)
        (root / (task['id']+'.json')).write_text(json.dumps(row, ensure_ascii=False, indent=2)+'\n')
        rows.append(row)
    summary = dict(schema='power-grid-env.engineering-rollout/v1', split=split,
                   tasks=len(rows), successes=sum(r['success'] for r in rows),
                   accuracy=sum(r['success'] for r in rows)/len(rows),
                   protocol_errors=sum('protocol_error' in t for r in rows for t in r['turns']),
                   failures=sum(r['stop_reason']=='policy_failure' for r in rows),
                   context_failures=sum(any(t.get('error_code')=='context_budget' for t in r['turns']) for r in rows),
                   max_turns=max_turns,repeat_limit=repeat_limit,eligible_for_gold_sft=False,
                   candidate=getattr(policy, 'identity', {'type': 'in_process_test_callback'}),
                   core_sha256=env.package_fingerprint(),
                   environment_sha256=hashlib.sha256(Path(env.__file__).read_bytes()).hexdigest(),
                   rollout_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   scope='Authored bounded text/tool profile; all tasks retained in denominator. No OS sandbox, VLM qualification or general engineering claim.')
    (root/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    seal(root)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True)
    parser.add_argument('--split', choices=['train', 'dev', 'test'], default='dev')
    parser.add_argument('--max-turns', type=int, default=32)
    parser.add_argument('--timeout', type=float, default=180)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('candidate command required after --')
    policy = ProcessPolicy(command, args.timeout)
    try:
        print(json.dumps(evaluate(policy, args.out, args.split, args.max_turns)))
    finally:
        policy.close()


if __name__ == '__main__':
    main()
