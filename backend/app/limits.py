"""Single-worker admission limits. Peer address comes only from the ASGI server."""
from collections import OrderedDict
from threading import Lock
from time import monotonic

from fastapi.responses import JSONResponse


class UsageLimits:
    def __init__(self, app):
        self.app = app
        self.lock = Lock()
        self.clients = OrderedDict()
        self.active = {'ask': 0, 'query': 0, 'import': 0}
        self.daily = (monotonic(), 0)

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if scope['type'] != 'http' or scope.get('method') != 'POST':
            return await self.app(scope, receive, send)
        kind = 'import' if path.startswith('/imports/') else 'ask' if path.endswith('/ask') else 'query'
        now = monotonic()
        peer = (scope.get('client') or ('unknown',))[0]
        with self.lock:
            for key in list(self.clients):
                if now - self.clients[key][0] >= 60:
                    del self.clients[key]
            start, count = self.clients.get(peer, (now, 0))
            day_start, daily_count = self.daily
            if now - day_start >= 86400:
                day_start, daily_count = now, 0
            limited = count >= 12 or (peer not in self.clients and len(self.clients) >= 4096)
            limited |= kind == 'ask' and daily_count >= 120
            busy = self.active[kind] >= (1 if kind == 'import' else 2) or sum(self.active.values()) >= 4
            if not limited and not busy:
                self.clients[peer] = (start, count + 1)
                self.active[kind] += 1
                self.daily = (day_start, daily_count + int(kind == 'ask'))
        if limited or busy:
            return await JSONResponse({'detail': 'Usage limit reached. Please try later.' if limited else 'Server busy. Please try shortly.'},
                                      status_code=429 if limited else 503, headers={'Retry-After': '60' if limited else '5'})(scope, receive, send)
        try:
            await self.app(scope, receive, send)
        finally:
            with self.lock:
                self.active[kind] -= 1
