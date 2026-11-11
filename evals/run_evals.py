"""Run graded evals with shared API pacing and resumable per-case checkpoints.

Only the runner is changed; grading logic and evaluation fixtures are unchanged.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import run_agent
from providers import Provider
from scenarios import ScenarioTools
from grading import grade_case, summarize, bonus_points
from bonus.tools import BonusTools


class RateLimitError(RuntimeError):
    """All 429 retries are exhausted."""


class RequestPacer:
    """Reserve a time slot before EACH API request, including judge requests."""
    def __init__(self, interval_seconds):
        self.interval = interval_seconds
        self.lock = threading.Lock()
        self.next_allowed = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next_allowed)
            self.next_allowed = start + self.interval
        delay = start - now
        if delay > 0:
            print(f'API pacing: sleeping {delay:.1f}s', flush=True)
            time.sleep(delay)


class PacedProvider:
    """Applies shared pacing to agent AND judge, with bounded HTTP-429 retry."""
    def __init__(self, provider, pacer, retries=1, retry_wait=65):
        self.provider = provider
        self.pacer = pacer
        self.retries = retries
        self.retry_wait = retry_wait

    def complete(self, messages, schemas):
        for attempt in range(self.retries + 1):
            self.pacer.wait()
            try:
                return self.provider.complete(messages, schemas)
            except RuntimeError as exc:
                if 'HTTP 429' not in str(exc):
                    raise
                if attempt == self.retries:
                    raise RateLimitError('HTTP 429: retry budget exhausted') from exc
                delay = self.retry_wait * (attempt + 1)
                print(f'HTTP 429: backing off {delay:.0f}s before retry', flush=True)
                time.sleep(delay)
        raise AssertionError('Unreachable retry loop')


def build_provider(args, name, model):
    provider = Provider(name, model)
    pacer = getattr(args, '_pacer', None)
    if pacer is None:
        return provider
    return PacedProvider(provider, pacer, args.retry_429, args.retry_wait)


def run_case(case, args):
    # Reuse the completed agent trajectory if only the judge failed last time.
    previous = getattr(args, '_previous_results', {}).get(case['id'])
    cached = previous.get('result') if isinstance(previous, dict) else None
    if isinstance(cached, dict) and cached.get('status') == 'completed':
        result = cached
        print(f"{case['id']}: reusing completed agent trace; judge only", flush=True)
    else:
        try:
            result = run_agent(
                case['question'], build_provider(args, args.provider, args.model),
                (BonusTools('snapshot', scenario=case.get('scenario', 'normal'))
                 if getattr(args, 'bonus', False) else
                 ScenarioTools(case.get('scenario', 'normal'), case.get('fail', False))),
                max_steps=args.max_steps,
            )
        except Exception as exc:
            # Do not leak provider credentials or request bodies in error strings.
            result = {'status': 'error', 'answer': '', 'trace': [], 'usage': [],
                      'error': f'{type(exc).__name__}: agent run failed'}

    if not isinstance(result, dict) or not isinstance(result.get('trace', []), list):
        result = {'status': 'error', 'answer': '', 'trace': [], 'usage': [],
                  'error': 'Invalid agent result'}
    try:
        judge = build_provider(args, args.judge_provider, args.judge_model)
        grade = grade_case(case, result, judge)
    except Exception as exc:
        grade = {'score': None, 'status': 'unscored',
                 'reason': f'Judge failed: {type(exc).__name__}', 'judge_usage': {}}
    return {
        'id': case['id'], 'question': case['question'], 'expected': case['expected'],
        'scenario': case.get('scenario', 'normal'), 'provider': args.provider,
        'model': args.model, 'judge_provider': args.judge_provider,
        'judge_model': args.judge_model, 'result': result, 'grade': grade,
    }


def write_json_atomic(path, data):
    """A crash cannot leave a partially-written JSON checkpoint."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def per_case_folder(output_path):
    return output_path.with_name(output_path.stem + '_cases')


def load_resume(output_path, folder, config, selected):
    """Reject mixes of different model/judge/dataset/step configurations."""
    if not output_path.exists():
        raise ValueError('No existing aggregate file to resume; omit --resume for a new run.')
    saved = read_json(output_path)
    if saved.get('config') != config:
        raise ValueError('Eval config mismatch. Use a NEW --output for the changed model or settings.')
    run_id = saved.get('run_id')
    saved_results = {x['id']: x for x in saved.get('cases', [])}
    for case in selected:
        path = folder / f"{case['id']}.json"
        if not path.exists():
            continue
        record = read_json(path)
        if record.get('config') == config and record.get('run_id') == run_id:
            saved_results[case['id']] = record['case']
    selected_map = {c['id']: c for c in selected}
    # Do not reuse results if case content (including expected) has changed.
    for key, item in list(saved_results.items()):
        if key not in selected_map:
            del saved_results[key]
            continue
        original = selected_map[key]
        if item.get('question') != original.get('question') or item.get('expected') != original.get('expected'):
            raise ValueError(f'Evaluation dataset changed for {key}; resume refused.')
    return run_id or uuid.uuid4().hex, saved_results


def build_report(selected, results_by_id, config, run_id, full_bonus):
    completed = [results_by_id[c['id']] for c in selected if c['id'] in results_by_id]
    # Pending cases count as unscored, not as zeros or completed cases.
    summary_rows = [results_by_id.get(c['id'], {'grade': {'score': None}}) for c in selected]
    summary = summarize(summary_rows)
    if config['dataset'] == 'bonus':
        summary['bonus_points_0_15'] = bonus_points(summary['score_0_100'], full_bonus)
    return {
        'summary': summary, 'config': config, 'run_id': run_id,
        'progress': {'saved_cases': len(completed), 'selected_cases': len(selected),
                     'pending_cases': len(selected) - len(completed),
                     'all_cases_saved': len(completed) == len(selected)},
        'cases': completed,
    }


def is_rate_limited(item):
    result = item.get('result') or {}
    grade = item.get('grade') or {}
    return ('RateLimitError' in str(result.get('error', '')) or
            'HTTP 429' in str(grade.get('reason', '')) or
            'RateLimitError' in str(grade.get('reason', '')))


def main():
    parser = argparse.ArgumentParser(description='Resumable, API-paced status agent evals')
    choices = ['openai', 'gemini', 'anthropic', 'openrouter']
    parser.add_argument('--provider', choices=choices, default=os.getenv('PROVIDER', 'openai'))
    parser.add_argument('--model', default=os.getenv('MODEL'))
    parser.add_argument('--judge-provider', choices=choices, default=os.getenv('JUDGE_PROVIDER'))
    parser.add_argument('--judge-model', default=os.getenv('JUDGE_MODEL'))
    parser.add_argument('--bonus', action='store_true')
    parser.add_argument('--case', help='Run a single case, e.g. E2 or H1')
    parser.add_argument('--max-steps', type=int, default=None)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--output', default='eval_results.json')
    parser.add_argument('--resume', action='store_true', help='Reuse saved scores and retry unscored cases')
    parser.add_argument('--min-interval', type=float, default=30.0,
                        help='Minimum seconds between ALL API requests (default: 30 = 2 RPM)')
    parser.add_argument('--retry-429', type=int, default=1,
                        help='Number of retry attempts after HTTP 429 (default: 1)')
    parser.add_argument('--retry-wait', type=float, default=65.0,
                        help='Wait after first HTTP 429 in seconds (default: 65)')
    args = parser.parse_args()
    args.judge_provider = args.judge_provider or args.provider
    args.judge_model = args.judge_model or args.model
    if not args.model or not args.judge_model:
        parser.error('Missing --model or MODEL')
    args.max_steps = args.max_steps if args.max_steps is not None else (16 if args.bonus else 8)
    if (args.max_steps < 1 or not 1 <= args.workers <= 8 or args.min_interval < 0 or
            args.retry_429 < 0 or args.retry_wait < 0):
        parser.error('Invalid max-steps/workers/min-interval/retry-429/retry-wait')
    if args.workers > 1:
        parser.error('Use --workers 1 for safe pacing and resumable runs')

    cases_path = ((Path(__file__).resolve().parents[1] / 'bonus' / 'cases.json')
                  if args.bonus else (Path(__file__).parent / 'cases.json'))
    cases = read_json(cases_path)
    selected = [c for c in cases if not args.case or c['id'] == args.case.upper()]
    if not selected:
        parser.error('Unknown case ID')

    config = {'provider': args.provider, 'model': args.model,
              'judge_provider': args.judge_provider, 'judge_model': args.judge_model,
              'max_steps': args.max_steps, 'dataset': 'bonus' if args.bonus else 'core'}
    output_path = Path(args.output)
    folder = per_case_folder(output_path)
    if args.resume:
        try:
            run_id, results_by_id = load_resume(output_path, folder, config, selected)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            parser.error(f'Cannot resume: {exc}')
    else:
        run_id, results_by_id = uuid.uuid4().hex, {}
    args._previous_results = results_by_id
    args._pacer = RequestPacer(args.min_interval)
    full_bonus = bool(args.bonus and not args.case and len(selected) == len(cases))

    def checkpoint():
        report = build_report(selected, results_by_id, config, run_id, full_bonus)
        write_json_atomic(output_path, report)
        return report

    # Save initial progress; then update both case and aggregate after each case.
    report = checkpoint()
    try:
        for case in selected:
            old = results_by_id.get(case['id'])
            if old and old.get('grade', {}).get('status') == 'scored':
                print(f"{case['id']}: already scored; skipping", flush=True)
                continue
            print(f"Running {case['id']}", flush=True)
            item = run_case(case, args)
            results_by_id[case['id']] = item
            write_json_atomic(folder / f"{case['id']}.json",
                              {'run_id': run_id, 'config': config, 'case': item})
            report = checkpoint()
            print(f"{case['id']}: {item['grade'].get('score')} ({item['grade']['status']}); "
                  f"saved {report['progress']['saved_cases']}/{len(selected)}", flush=True)
            if is_rate_limited(item):
                print('HTTP 429 still present. Saved progress; resume after quota resets.',
                      file=sys.stderr, flush=True)
                break
    except KeyboardInterrupt:
        print('Interrupted; previously completed cases are saved.', file=sys.stderr)
        return 130

    report = checkpoint()
    summary = report['summary']
    if args.bonus:
        print(f"Bonus points: {summary['bonus_points_0_15']}/15")
    print(f"Score: {summary['score_0_100']}/100; scored "
          f"{summary['scored_cases']}/{summary['total_cases']}. File: {output_path}")
    if summary['score_0_100'] is None:
        print('Incomplete or unscored cases remain. Use --resume.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
