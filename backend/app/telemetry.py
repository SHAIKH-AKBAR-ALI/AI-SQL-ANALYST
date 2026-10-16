"""Allowlisted metadata only. Never capture model IO, SQL, IDs, or exceptions."""
import os
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache, wraps
from time import monotonic
from uuid import uuid4

CURRENT = ContextVar('telemetry', default=None)


@lru_cache(maxsize=1)
def trace_client():
    if os.getenv('LANGFUSE_ENABLED') != 'true':
        return None
    if not all(os.getenv(key) for key in ('LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY', 'LANGFUSE_BASE_URL')):
        return None
    from langfuse import Langfuse
    return Langfuse(timeout=3, sample_rate=1, release='phase9-v1')


@contextmanager
def stage(name):
    started = monotonic()
    info = {'stage': name, 'outcome': 'ok', 'input_tokens': None, 'output_tokens': None}
    try:
        yield info
    except BaseException:
        info['outcome'] = 'error'
        raise
    finally:
        info['latency_ms'] = round((monotonic() - started) * 1000, 2)
        record = CURRENT.get()
        if record is not None:
            record['stages'].append(info)


def new_record():
    return {'trace_id': uuid4().hex, 'stages': [], 'cost_usd': None}


def measured(name):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with stage(name):
                return function(*args, **kwargs)
        return wrapped
    return decorate


def export_record(record, model, prompt_version, outcome):
    try:
        client = trace_client()
        if client is None:
            return
        root = client.start_observation(name='question', trace_context={'trace_id': record['trace_id']},
            metadata={'model': model, 'prompt_version': prompt_version, 'outcome': outcome})
        for item in record['stages']:
            is_model = item['stage'].startswith('model_')
            usage = {key: item[key] for key in ('input_tokens','output_tokens') if item[key] is not None}
            span = root.start_observation(name=item['stage'], as_type='generation' if is_model else 'span',
                model=model if is_model else None, usage_details=usage or None, metadata=dict(item),
                level='ERROR' if item['outcome']=='error' else 'DEFAULT')
            span.end()
        root.end()
    except Exception:
        # Telemetry outages cannot change analytical answers or expose SDK errors.
        pass
