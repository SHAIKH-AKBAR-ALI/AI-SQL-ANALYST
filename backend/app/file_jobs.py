"""Discardable parsing process: timeout terminates work before importing rows."""
import multiprocessing
import sys

from app.sql_executor import QueryError


def _worker(pipe, operation, body):
    try:
        if sys.platform != 'win32':
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024 * 1024,) * 2)
            resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
        from app import import_files
        result = getattr(import_files, operation)(body)
        pipe.send(('ok', result))
    except QueryError as exc:
        pipe.send(('error', (exc.status, exc.message)))
    except Exception:
        pipe.send(('error', (422, 'File could not be processed. Check the format and limits.')))
    finally:
        pipe.close()


def run_file_job(operation, body):
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(child, operation, body), daemon=True)
    try:
        process.start()
        child.close()
        if not parent.poll(15):
            raise QueryError(408, 'File processing time limit exceeded. Choose a smaller file.')
        try:
            status, value = parent.recv()
        except EOFError as exc:
            raise QueryError(422, 'File worker stopped. Choose a smaller valid file.') from exc
        if status == 'error':
            raise QueryError(*value)
        return value
    finally:
        parent.close()
        child.close()
        if process.pid:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)


def preview_file(body):
    return run_file_job('preview_file', body)


def validate_file(body):
    return run_file_job('validate_file', body)
