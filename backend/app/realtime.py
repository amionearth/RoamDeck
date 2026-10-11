import asyncio
import json
import logging
from typing import Dict, Set
from fastapi import WebSocket

logger = logging.getLogger("ffh.realtime")

class ConnectionManager:
    def __init__(self):
        # channel_name -> set of WebSocket connections
        self.channels: Dict[str, Set[WebSocket]] = {}
        # websocket -> set of subscribed channels
        self.subscriptions: Dict[WebSocket, Set[str]] = {}
        self.lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self.lock:
            self.subscriptions[websocket] = set()

    async def disconnect(self, websocket: WebSocket):
        async with self.lock:
            subs = self.subscriptions.pop(websocket, set())
            for ch in subs:
                if ch in self.channels:
                    self.channels[ch].discard(websocket)
                    if not self.channels[ch]:
                        del self.channels[ch]

    async def subscribe(self, websocket: WebSocket, channel: str):
        async with self.lock:
            if websocket not in self.subscriptions:
                self.subscriptions[websocket] = set()
            self.subscriptions[websocket].add(channel)
            if channel not in self.channels:
                self.channels[channel] = set()
            self.channels[channel].add(websocket)

    async def unsubscribe(self, websocket: WebSocket, channel: str):
        async with self.lock:
            if websocket in self.subscriptions:
                self.subscriptions[websocket].discard(channel)
            if channel in self.channels:
                self.channels[channel].discard(websocket)
                if not self.channels[channel]:
                    del self.channels[channel]

    async def broadcast(self, channel: str, event: str, data: dict):
        message = json.dumps({"event": event, "data": data})
        targets = []
        async with self.lock:
            if channel in self.channels:
                targets = list(self.channels[channel])

        dead = []
        for ws in targets:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)

        for ws in dead:
            await self.disconnect(ws)

    async def broadcast_bytes(self, channel: str, data: bytes):
        targets = []
        async with self.lock:
            if channel in self.channels:
                targets = list(self.channels[channel])

        dead = []
        for ws in targets:
            try:
                await ws.send_bytes(data)
            except Exception:
                dead.append(ws)

        for ws in dead:
            await self.disconnect(ws)

manager = ConnectionManager()
