"""Upload benchmark metadata and scores as a Langfuse experiment; no raw IO."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from dotenv import load_dotenv
load_dotenv(ROOT/'backend/.env')
from app.telemetry import trace_client


def publish(records, run_name):
    client = trace_client()
    if client is None:
        raise RuntimeError('Configure Langfuse credentials and LANGFUSE_ENABLED=true in backend/.env first.')
    data = []
    for record in records:
        metrics = {'pass':bool(record['pass']), 'latency_ms':record['latency_ms'],
                   'source_trace_id':(record.get('telemetry') or {}).get('trace_id')}
        for dimension in ('groundedness','relevance','clarity'):
            value = record.get('judge',{}).get('quality',{}).get(dimension)
            if value is not None:
                metrics[dimension] = value
        value = record.get('judge',{}).get('sql_equivalence',{}).get('value')
        if value is not None:
            metrics['sql_equivalence'] = value
        data.append({'input':{key:record.get(key) for key in ('case','fixture','mode','prompt_version','model')},
                     'expected_output':{'pass':True}, 'metadata':metrics})
    def task(*, item, **kwargs):
        return item['metadata']
    def evaluate(*, output, **kwargs):
        return [{'name':key,'value':int(value) if isinstance(value,bool) else value}
                for key,value in output.items() if isinstance(value,(int,float,bool))]
    result = client.run_experiment(name='HisaabAI fixed sales benchmark',run_name=run_name,
        data=data,task=task,evaluators=[evaluate],max_concurrency=1,
        metadata={'corpus_version':'phase9-v1','io_policy':'case identifiers and numeric scores only'})
    client.flush()
    return result.experiment_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',type=Path)
    args = parser.parse_args()
    report = args.report.resolve()
    if not report.is_relative_to(ROOT/'evals/results'):
        parser.error('Use a fixed benchmark report under evals/results.')
    records = [json.loads(line) for line in report.read_text(encoding='utf-8').splitlines() if line.strip()]
    try:
        print(publish(records,report.stem))
    except RuntimeError as exc:
        parser.exit(1,str(exc)+'\n')


if __name__=='__main__':
    main()
