"""In-process pub/sub. publish() never blocks; slow consumers lose oldest."""
import asyncio

class Bus:
    def __init__(self, maxsize=256):
        self.maxsize, self._subs = maxsize, set()

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue(self.maxsize)
        self._subs.add(q)
        return q

    def unsubscribe(self, q):
        self._subs.discard(q)

    def publish(self, msg: dict):
        for q in list(self._subs):
            if q.full():
                q.get_nowait()
            q.put_nowait(msg)

    @property
    def count(self):
        return len(self._subs)
