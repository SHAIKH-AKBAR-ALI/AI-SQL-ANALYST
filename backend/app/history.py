"""Owner-scoped, version-bound conversations. Results are never persisted."""
from uuid import uuid4

from pydantic import model_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.agent import QuestionRequest, ask_demo, dataset_context
from app.database import user_transaction
from app.sql_executor import QueryError


class PrivateQuestion(QuestionRequest):
    ai_consent: bool = False

    @model_validator(mode='after')
    def consent(self):
        if not self.ai_consent:
            raise ValueError('Confirm that your question, recent conversation context and query results may be sent to Groq.')
        return self


def ask_private(body, dataset_id, user):
    context = dataset_context(dataset_id, user)
    if context['is_demo']:
        raise QueryError(422, 'Use Try Demo for fictional shared data.')
    conversation_id, run_id = body.conversation_id or uuid4(), uuid4()
    history = []
    try:
        with user_transaction(user, 'import') as connection:
            if body.conversation_id:
                conversation = connection.execute(text('''SELECT dataset_version FROM app.conversations
                    WHERE id=:cid AND dataset_id=:id AND owner_id=:owner FOR UPDATE'''),
                    {'cid': conversation_id, 'id': dataset_id, 'owner': user.id}).first()
                if conversation is None:
                    raise QueryError(404, 'Conversation not found')
                if conversation.dataset_version != context['version']:
                    raise QueryError(409, 'Dataset changed. Start a new conversation and confirm the period and metric.')
                runs = list(connection.execute(text('''SELECT question,sql_text,status,clarification,
                    created_at > now() - interval '2 minutes' AS recent
                    FROM app.query_runs WHERE conversation_id=:cid
                    ORDER BY created_at,id LIMIT 21'''), {'cid': conversation_id}).mappings())
                if len(runs) >= 20:
                    raise QueryError(409, 'Conversation limit reached. Start a new conversation.')
                if any(run['status'] == 'pending' and run['recent'] for run in runs):
                    raise QueryError(409, 'Conversation has an active question. Please wait.')
                history = [{'question': run['question'], 'sql': run['sql_text'],
                            'clarification': run['clarification']} for run in runs[-8:]
                           if run['status'] in ('succeeded', 'clarification')]
            else:
                connection.execute(text('''INSERT INTO app.conversations (id,owner_id,dataset_id,dataset_version)
                VALUES (:cid,:owner,:id,:version)'''),
                               {'cid': conversation_id, 'owner': user.id, 'id': dataset_id, 'version': context['version']})
            connection.execute(text('''INSERT INTO app.query_runs
                (id,conversation_id,owner_id,dataset_id,dataset_version,question,status)
                VALUES (:rid,:cid,:owner,:id,:version,:question,'pending')'''),
                               {'rid': run_id, 'cid': conversation_id, 'owner': user.id, 'id': dataset_id,
                                'version': context['version'], 'question': body.question})
    except (RuntimeError, SQLAlchemyError) as exc:
        raise QueryError(503, 'Could not save question history. Please try again.') from exc
    try:
        response = ask_demo(body.question, dataset_id, user=user, history=history)
        if response.get('result') and response['result']['dataset_version'] != context['version']:
            raise QueryError(409, 'Dataset changed. Please ask again.')
    except QueryError:
        save_run(run_id, user, 'failed', None)
        raise
    status = 'succeeded' if response['status'] == 'answered' else 'clarification' if response['status'] == 'clarify' else 'failed'
    saved = save_run(run_id, user, status, response['sql'], response['answer'] if status == 'clarification' else None)
    if not saved:
        response['warning'] = 'Answer returned, but history status could not be updated. Refresh history to check.'
    return {**response, 'conversation_id': str(conversation_id), 'query_run_id': str(run_id)}


def save_run(run_id, user, status, sql, clarification=None):
    try:
        with user_transaction(user, 'import') as connection:
            connection.execute(text('UPDATE app.query_runs SET status=:status,sql_text=:sql,clarification=:clarification WHERE id=:id'),
                               {'id': run_id, 'status': status, 'sql': sql, 'clarification': clarification})
        return True
    except (RuntimeError, SQLAlchemyError):
        return False


def read_history(dataset_id, user):
    try:
        with user_transaction(user) as connection:
            if not connection.execute(text('SELECT id FROM app.datasets WHERE id=:id AND owner_id=:owner'),
                                      {'id': dataset_id, 'owner': user.id}).first():
                raise QueryError(404, 'Dataset not found')
            rows = connection.execute(text('''SELECT q.id, q.conversation_id, q.question, q.sql_text,
                q.status, q.clarification, q.dataset_version, q.created_at, d.version AS current_version
                FROM app.query_runs q JOIN app.datasets d ON d.id=q.dataset_id
                WHERE q.dataset_id=:id ORDER BY q.created_at DESC, q.id LIMIT 100'''), {'id': dataset_id}).mappings()
            return {'history': [dict(row) for row in rows],
                    'notice': 'Conversations retain up to eight recent question/SQL/clarification turns as context. Old result rows are not retained. Replaced versions cannot be resumed.'}
    except (RuntimeError, SQLAlchemyError) as exc:
        raise QueryError(503, 'History temporarily unavailable') from exc
