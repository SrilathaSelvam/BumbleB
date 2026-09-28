"""WebSocket /ws: streams bus messages (metric/alert/alert_update) as JSON."""
import asyncio, logging
from fastapi import APIRouter, WebSocket

log = logging.getLogger(__name__)
router = APIRouter()

@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    bus = ws.app.state.runtime.bus
    await ws.accept()
    q = bus.subscribe()
    log.info("ws client connected (%d)", bus.count)
    async def sender():
        while True:
            await ws.send_json(await q.get())
    async def receiver():                      # detect disconnect; ignore client text
        while (await ws.receive())["type"] != "websocket.disconnect":
            pass
    tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
            t.add_done_callback(lambda t: t.cancelled() or t.exception())  # mark retrieved
        bus.unsubscribe(q)
        log.info("ws client gone (%d)", bus.count)
