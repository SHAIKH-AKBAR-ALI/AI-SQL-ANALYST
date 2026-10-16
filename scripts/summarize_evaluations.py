"""Keep actual failures and unequal coverage visible; never pool references with AI."""
import argparse
import json
import math
import statistics
from pathlib import Path


def summarize(records):
    groups = {}
    for record in records:
        groups.setdefault(record['mode']+'@'+record['prompt_version'],[]).append(record)
    result = {}
    for mode, rows in groups.items():
        latency = sorted(row['latency_ms'] for row in rows)
        usage = [stage for row in rows for stage in (row.get('telemetry') or {}).get('stages',[]) if stage['stage'].startswith('model_')]
        result[mode] = {'passed':sum(row['pass'] for row in rows),'total':len(rows),
            'p50_ms':statistics.median(latency),'p95_ms':latency[max(0,math.ceil(len(latency)*.95)-1)],
            'provider_errors':sum(row.get('error_status') in (429,502,503,504) for row in rows),
            'model_calls':len(usage),'usage_unavailable_calls':sum(stage['input_tokens'] is None for stage in usage),
            'input_tokens':sum(stage['input_tokens'] or 0 for stage in usage),
            'output_tokens':sum(stage['output_tokens'] or 0 for stage in usage),
            'cost_usd':None,'failures':[{'case':row['case'],'fixture':row['fixture'],'error':row.get('error')} for row in rows if not row['pass']]}
    indexed = {(row['case'],row['fixture'],row['mode'],row['prompt_version']):row for row in records}
    pairs = [(row,indexed[(case,fixture,'reviewed',version)]) for (case,fixture,mode,version),row in indexed.items()
             if mode=='generator' and (case,fixture,'reviewed',version) in indexed]
    result['paired_comparison'] = {'pairs':len(pairs),'generator_passed':sum(a['pass'] for a,b in pairs),
                                   'reviewed_passed':sum(b['pass'] for a,b in pairs)}
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports',nargs='+',type=Path)
    args=parser.parse_args()
    records=[json.loads(line) for report in args.reports for line in report.read_text(encoding='utf-8').splitlines() if line.strip()]
    print(json.dumps(summarize(records),indent=2))


if __name__=='__main__':
    main()
