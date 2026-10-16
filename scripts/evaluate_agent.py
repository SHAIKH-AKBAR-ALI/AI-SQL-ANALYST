"""Fixed fictional corpus. Reference checks are distinct from live model evaluation."""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT/'backend', ROOT/'backend/tests'):
    sys.path.insert(0, str(path))
os.environ.setdefault('RAGAS_DO_NOT_TRACK', 'true')
from app.agent import PROMPT_VERSION, ask_demo
from app.agent_config import groq_settings
from app.sql_executor import QueryError, execute_query
from evals.compare import equal_rows
from evals.fixtures import fixtures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--fixture', choices=('all','demo','edge','empty'), default='all')
    parser.add_argument('--mode', choices=('reviewed','generator','both'), default='reviewed')
    parser.add_argument('--case', action='append')
    parser.add_argument('--pause', type=float, default=0)
    parser.add_argument('--judge', action='store_true', help='Offline fictional-only judge/Ragas calls consume additional quota.')
    args = parser.parse_args()
    cases = json.loads((ROOT/'evals/cases.json').read_text(encoding='utf-8'))
    if args.case:
        unknown = set(args.case) - {case['id'] for case in cases}
        if unknown:
            parser.error('Unknown case IDs: ' + ', '.join(sorted(unknown)))
        cases = [case for case in cases if case['id'] in args.case]
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = ROOT/'evals/results'/f'{stamp}-{"live" if args.live else "reference"}.jsonl'
    output.parent.mkdir(exist_ok=True)
    records = []
    quota_exhausted = False
    planned = 0
    with fixtures() as datasets, output.open('w',encoding='utf-8') as file:
        planned = sum(args.fixture in ('all', name) for name in datasets) * len(
            [case for case in cases if args.live or case['status'] == 'answered']) * (
            2 if args.live and args.mode == 'both' else 1)
        for fixture, (dataset_id,user) in datasets.items():
            if quota_exhausted:
                break
            if args.fixture != 'all' and args.fixture != fixture:
                continue
            modes = ('generator','reviewed') if args.live and args.mode == 'both' else (args.mode,)
            for mode in modes:
                if quota_exhausted:
                    break
                for case in cases:
                    if not args.live and case['status'] != 'answered':
                        continue
                    if records and args.pause:
                        time.sleep(min(60, max(0,args.pause)))
                    record = {'case':case['id'],'fixture':fixture,'mode':mode if args.live else 'reference',
                              'prompt_version':PROMPT_VERSION, 'model':groq_settings()[1] if args.live else None,
                              'corpus_version':'phase9-v1','pass':False}
                    started = time.monotonic()
                    try:
                        if args.live:
                            response = ask_demo(case['question'],dataset_id,user=user,review_enabled=mode=='reviewed')
                            record.update(status=response['status'],telemetry=response['telemetry'],repair_count=response['repair_count'])
                            record.update(sql=response['sql'],answer=response['answer'],result=response['result'])
                            assert response['status'] == case['status'], 'Unexpected answer/refusal/clarification status'
                            if case['status'] == 'answered':
                                assert not response['result']['truncated'], 'Benchmark result unexpectedly truncated'
                                assert equal_rows(response['result']['rows'],case['expected'][fixture],ordered=case['ordered']), 'Actual rows differ from hand-checked expected rows'
                            else:
                                assert response['result'] is None and response['sql'] is None, 'Non-answer executed or disclosed SQL'
                            if args.judge and response['status'] == 'answered':
                                from evals.judge import judge_answer
                                record['judge'] = judge_answer(case,response)
                            # Fictional corpus only. Preserve actual SQL/answer for failure inspection.
                            record.update(sql=response['sql'],answer=response['answer'],result=response['result'])
                        else:
                            result = execute_query(case['sql'],dataset_id,user)
                            assert equal_rows(result['rows'],case['expected'][fixture],ordered=case['ordered']), 'Reference SQL differs from manually checked values'
                        record['pass'] = True
                    except (QueryError, AssertionError) as exc:
                        record['error'] = exc.message if isinstance(exc,QueryError) else str(exc)
                        record['error_status'] = exc.status if isinstance(exc,QueryError) else None
                        if isinstance(exc, QueryError):
                            record['telemetry'] = getattr(exc, 'telemetry', None)
                    record['latency_ms'] = round((time.monotonic()-started)*1000,2)
                    records.append(record)
                    file.write(json.dumps(record,ensure_ascii=False)+'\n'); file.flush()
                    print(json.dumps({key:record[key] for key in ('case','fixture','mode','pass','latency_ms')},ensure_ascii=True),flush=True)
                    if record.get('error_status') == 429:
                        quota_exhausted = True
                        break
    passed = sum(record['pass'] for record in records)
    summary = {'run':output.name,'kind':'live' if args.live else 'reference','passed':passed,'total':len(records),
               'planned':planned, 'complete':len(records)==planned,
               'stopped_reason':'provider_rate_limit' if quota_exhausted else None,
               'status':'pass' if passed==len(records)==planned else 'fail', 'prompt_version':PROMPT_VERSION,
               'notice':'Reference checks verify fixtures/SQL/comparator, not LLM accuracy. Live failures, including quota, count as failures.'}
    output.with_suffix('.summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary),flush=True)
    return 0 if passed==len(records)==planned else 1


if __name__ == '__main__':
    raise SystemExit(main())
