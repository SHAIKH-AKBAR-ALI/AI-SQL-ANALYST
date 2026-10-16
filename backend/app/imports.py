"""Confirmed all-or-nothing imports using only the owner-scoped import role."""
import json
from uuid import UUID, uuid4

from pydantic import Field, model_validator
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database import user_transaction
from app.import_files import ValidationRequest
from app.file_jobs import validate_file
from app.sql_executor import QueryError


class CommitRequest(ValidationRequest):
    name: str = Field(min_length=1, max_length=200)
    confirmed: bool = False
    replace_dataset_id: UUID | None = None
    expected_version: int | None = Field(default=None, ge=1)
    replace_confirmed: bool = False

    @model_validator(mode='after')
    def confirmation(self):
        if not self.name.strip():
            raise ValueError('Dataset name cannot be blank')
        if self.replace_dataset_id and (not self.expected_version or not self.replace_confirmed):
            raise ValueError('Replacement requires explicit confirmation and expected version')
        return self


def commit_import(body: CommitRequest, user):
    if not body.confirmed:
        raise QueryError(422, 'Confirm the import after reviewing validation.')
    report, rows = validate_file(body)
    if not report['valid']:
        raise QueryError(422, 'Import blocked. Fix every rejected row and complete amount confirmations; validate again.')
    mapping = body.model_dump(mode='json', exclude={'content', 'confirmed', 'replace_confirmed'})
    try:
        with user_transaction(user, 'import') as connection:
            # Serialize this owner's commits, including simultaneous first submissions.
            connection.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:owner, 0))'), {'owner': str(user.id)})
            target = None
            if body.replace_dataset_id:
                target = connection.execute(text('SELECT id, version FROM app.datasets WHERE id=:id FOR UPDATE'),
                                            {'id': body.replace_dataset_id}).mappings().one_or_none()
                if target is None:
                    raise QueryError(404, 'Dataset not found')
            duplicate = connection.execute(text('''SELECT i.dataset_id, i.dataset_version, i.accepted_rows,
                i.confirmed_mapping, d.version FROM app.imports i JOIN app.datasets d ON d.id=i.dataset_id
                WHERE i.owner_id=:owner AND i.file_sha256=:hash AND i.status='committed'
                ORDER BY i.created_at DESC LIMIT 1'''), {'owner': user.id, 'hash': report['sha256']}).mappings().one_or_none()
            if duplicate:
                same_target = not body.replace_dataset_id or body.replace_dataset_id == duplicate['dataset_id']
                # A repeat request must confirm identical column meanings, too.
                old = duplicate['confirmed_mapping']
                meaning_keys = ('mapping', 'sheet', 'date_format', 'default_unit', 'currency', 'amount_confirmed', 'adjustment_note')
                same_meaning = all(old.get(key) == mapping.get(key) for key in meaning_keys)
                if same_target and same_meaning and duplicate['version'] == duplicate['dataset_version']:
                    return {'dataset_id': str(duplicate['dataset_id']), 'dataset_version': duplicate['dataset_version'],
                            'accepted_rows': duplicate['accepted_rows'], 'rejected_rows': 0,
                            'status': 'committed', 'duplicate': True}
                raise QueryError(409, 'File already imported with other meanings or an older version. No data changed.')
            dataset_id = body.replace_dataset_id or uuid4()
            version = 1
            if target:
                if target['version'] != body.expected_version:
                    raise QueryError(409, 'Dataset changed. Refresh and confirm the replacement again.')
                version = target['version'] + 1
                connection.execute(text('DELETE FROM app.sales WHERE dataset_id=:id'), {'id': dataset_id})
                connection.execute(text('UPDATE app.datasets SET version=:version, name=:name WHERE id=:id'),
                                   {'id': dataset_id, 'version': version, 'name': body.name.strip()})
            else:
                connection.execute(text('INSERT INTO app.datasets (id,owner_id,name,currency) VALUES (:id,:owner,:name,:currency)'),
                                   {'id': dataset_id, 'owner': user.id, 'name': body.name.strip(), 'currency': body.currency})
            connection.execute(text('''INSERT INTO app.sales
                (dataset_id,source_row,sale_date,product_name,quantity,unit,unit_price,amount)
                VALUES (:dataset_id,:source_row,:sale_date,:product_name,:quantity,:unit,:unit_price,:amount)'''),
                               [{'dataset_id': dataset_id, **row} for row in rows])
            connection.execute(text('''INSERT INTO app.imports
                (dataset_id,owner_id,dataset_version,status,file_sha256,confirmed_mapping,accepted_rows,rejected_rows)
                VALUES (:id,:owner,:version,'committed',:hash,CAST(:mapping AS jsonb),:accepted,0)'''),
                               {'id': dataset_id, 'owner': user.id, 'version': version, 'hash': report['sha256'],
                                'mapping': json.dumps(mapping), 'accepted': len(rows)})
            return {'dataset_id': str(dataset_id), 'dataset_version': version, 'accepted_rows': len(rows),
                    'rejected_rows': 0, 'status': 'committed', 'duplicate': False,
                    'date_start': min(row['sale_date'] for row in rows).isoformat(),
                    'date_end': max(row['sale_date'] for row in rows).isoformat()}
    except (RuntimeError, SQLAlchemyError) as exc:
        raise QueryError(503, 'Import could not be committed. Existing data was preserved; retry after refreshing.') from exc
