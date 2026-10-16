"""Run optional judges on already-saved fictional benchmark answers."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
for path in (ROOT,ROOT/'backend'):
    sys.path.insert(0,str(path))
os.environ['RAGAS_DO_NOT_TRACK']='true'
from dotenv import load_dotenv
load_dotenv(ROOT/'backend/.env')
from evals.judge import judge_answer


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',type=Path)
    parser.add_argument('--limit',type=int,default=3)
    args=parser.parse_args()
    report=args.report.resolve()
    if not report.is_relative_to(ROOT/'evals/results'):
        parser.error('Use a fictional fixed benchmark report under evals/results.')
    cases={case['id']:case for case in json.loads((ROOT/'evals/cases.json').read_text(encoding='utf-8'))}
    records=[json.loads(line) for line in report.read_text(encoding='utf-8').splitlines() if line.strip()]
    scores=[]
    for record in records:
        if record.get('status')!='answered' or not record.get('pass') or record.get('case') not in cases:
            continue
        if scores:
            time.sleep(20)
        try:
            score=judge_answer(cases[record['case']],record)
            output={'case':record['case'],'fixture':record['fixture'],'mode':record['mode'],'judge':score}
        except Exception as exc:
            # Never expose provider bodies; failed metric stays unavailable.
            output={'case':record['case'],'error_type':type(exc).__name__,'judge':None}
        scores.append(output)
        print(json.dumps(output,ensure_ascii=True),flush=True)
        if len(scores)>=max(1,args.limit):
            break
    report.with_suffix('.judge.json').write_text(json.dumps(scores,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if scores and all(row.get('judge') for row in scores) else 1


if __name__=='__main__':
    raise SystemExit(main())
