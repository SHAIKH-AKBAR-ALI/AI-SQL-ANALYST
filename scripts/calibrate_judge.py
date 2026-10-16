"""Assistant-labeled fictional rubric anchors. Human review remains explicit."""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT,ROOT/'backend'):
    sys.path.insert(0,str(path))
from dotenv import load_dotenv
load_dotenv(ROOT/'backend/.env')
from evals.judge import rubric_score
from app.sql_executor import QueryError

ANCHORS = [
 ('correct','Total October 2026 revenue?','Total revenue for the requested period is 1440.00 INR.',[['1440.00']],{'groundedness':[4,5],'relevance':[4,5],'clarity':[4,5]}),
 ('wrong_value','Total October 2026 revenue?','Revenue is 9999 INR.',[['1440.00']],{'groundedness':[1,1]}),
 ('causal_claim','Total October 2026 revenue?','Revenue is 1440 INR because customers love our marketing.',[['1440.00']],{'groundedness':[1,1]}),
 ('null_is_zero','Total revenue in February 2025?','No sales happened. Revenue is zero INR.',[[None]],{'groundedness':[1,1]}),
 ('unrelated','Which product earned most revenue?','There are many products available in the market.',[['Milk','1650.00']],{'relevance':[1,1]}),
 ('empty_honest','Total revenue in February 2025?','No matching values returned; this does not imply zero sales.',[[None]],{'groundedness':[4,5],'relevance':[4,5],'clarity':[4,5]}),
]


def main():
    records = []
    for i,(name,question,answer,rows,expected) in enumerate(ANCHORS):
        if i:
            time.sleep(20)
        try:
            scores = rubric_score(question,answer,{'rows':rows,'currency':'INR','truncated':False})
            match = all(low<=scores[key]<=high for key,(low,high) in expected.items())
            record = dict(anchor=name,expected=expected,scores=scores,pass_=match)
        except QueryError as exc:
            record = dict(anchor=name,error=exc.message,error_status=exc.status,pass_=False)
        records.append(record)
        print(json.dumps(record),flush=True)
        if record.get('error_status') == 429:
            break
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    (ROOT/f'evals/results/{stamp}-judge-calibration.json').write_text(json.dumps(
        {'human_review':'pending; assistant-labeled anchors', 'planned':len(ANCHORS),
         'complete':len(records)==len(ANCHORS), 'records':records},indent=2)+'\n',encoding='utf-8')
    return 0 if len(records)==len(ANCHORS) and all(record['pass_'] for record in records) else 1


if __name__=='__main__':
    raise SystemExit(main())
