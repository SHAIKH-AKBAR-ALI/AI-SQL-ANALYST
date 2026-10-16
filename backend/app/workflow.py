"""Bounded ephemeral demo conversations and cooperative request cancellation."""
import asyncio
from collections import OrderedDict
from threading import Event, Lock
from time import monotonic
from uuid import uuid4

from starlette.concurrency import run_in_threadpool

from app.agent import REQUEST_CONTROL, ask_demo, dataset_context
from app.sql_executor import QueryError

_sessions = OrderedDict()
_lock = Lock()


def ask_demo_turn(body, dataset_id):
    context = dataset_context(dataset_id)
    now = monotonic()
    with _lock:
        for key in list(_sessions):
            if _sessions[key]['expires'] <= now and not _sessions[key]['busy']:
                del _sessions[key]
        cid = body.conversation_id or uuid4()
        if body.conversation_id:
            state = _sessions.get(cid)
            if state is None or state['dataset'] != dataset_id:
                raise QueryError(404, 'Demo conversation expired. Start a new conversation.')
            if state['version'] != context['version']:
                raise QueryError(409, 'Dataset changed. Start a new conversation.')
            if state['busy'] or state['turns'] >= 20:
                raise QueryError(409, 'Conversation busy or full. Wait or start a new conversation.')
        else:
            if len(_sessions) >= 500:
                raise QueryError(503, 'Demo conversation capacity reached. Please try later.')
            state = {'dataset': dataset_id, 'version': context['version'],
                     'history': [], 'turns': 0, 'expires': now + 1800, 'busy': False}
            _sessions[cid] = state
        state['busy'] = True
        history = list(state['history'])
    try:
        # Keep the original no-context call compatible with the standalone API.
        response = ask_demo(body.question, dataset_id, history=history) if history else ask_demo(body.question, dataset_id)
        with _lock:
            state['turns'] += 1
            state['expires'] = monotonic() + 1800
            if response['status'] in ('answered', 'clarify'):
                state['history'] = (history + [{'question': body.question, 'sql': response.get('sql'),
                    'clarification': response['answer'] if response['status'] == 'clarify' else None}])[-8:]
        return {**response, 'conversation_id': str(cid)}
    finally:
        with _lock:
            state['busy'] = False


async def run_question(request, function, *args):
    cancelled = Event()
    deadline = monotonic() + 70
    token = REQUEST_CONTROL.set((deadline, cancelled))
    task = asyncio.create_task(run_in_threadpool(function, *args))
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=0.2)
            if await request.is_disconnected():
                cancelled.set()
                # Drain the current bounded provider/DB call; no later stage may run.
                break
            if monotonic() >= deadline:
                cancelled.set()
                break
        return await task
    finally:
        cancelled.set()
        REQUEST_CONTROL.reset(token)
