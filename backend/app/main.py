import asyncio

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from .pipeline.company_search import search_companies
from .pipeline.runner import run_pipeline

app = FastAPI(title="asb-finstat")


@app.get("/")
async def health():
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    pipeline_task: asyncio.Task | None = None

    async def emit(step: str, status: str, message: str, data: dict | None = None):
        await ws.send_json({"type": "step_update", "step": step, "status": status,
                            "message": message, "data": data})

    try:
        while True:
            msg = await ws.receive_json()
            msg_type = msg.get("type")
            if msg_type == "search_companies":
                # AI call #1 - company typeahead
                try:
                    companies = await search_companies(msg["country"], msg["query"])
                    await ws.send_json({"type": "companies", "requestId": msg.get("requestId"),
                                        "companies": companies})
                except Exception as e:
                    await ws.send_json({"type": "companies", "requestId": msg.get("requestId"),
                                        "companies": [], "error": str(e)})
            elif msg_type == "run_pipeline":
                if pipeline_task and not pipeline_task.done():
                    await emit("pipeline", "error", "A pipeline is already running on this connection")
                    continue
                pipeline_task = asyncio.create_task(run_pipeline(msg["company"], msg["country"], emit))
            else:
                await emit("pipeline", "error", f"Unknown message type: {msg_type}")
    except WebSocketDisconnect:
        pass
    finally:
        if pipeline_task and not pipeline_task.done():
            pipeline_task.cancel()
