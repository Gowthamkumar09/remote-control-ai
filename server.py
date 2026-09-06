from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header
from fastapi.responses import JSONResponse
import asyncio
import os
from datetime import datetime, timezone

app = FastAPI()

connected_laptops = {}

# Read the private token from Render Environment Variables
REMOTE_TOKEN = os.getenv("REMOTE_TOKEN")

if not REMOTE_TOKEN:
    print("WARNING: REMOTE_TOKEN is not configured")


def check_token(authorization: str | None):
    expected = f"Bearer {REMOTE_TOKEN}"

    if not REMOTE_TOKEN or authorization != expected:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing token"
        )


@app.get("/")
async def home():
    return {
        "message": "Remote Control AI server is running",
        "laptops_online": len(connected_laptops)
    }


@app.websocket("/ws/laptop/{laptop_id}")
async def laptop_websocket(websocket: WebSocket, laptop_id: str):
    await websocket.accept()

    connected_laptops[laptop_id] = {
        "websocket": websocket,
        "status": "online",
        "last_seen": datetime.now(timezone.utc)
    }

    print(f"Laptop connected: {laptop_id}")

    try:
        while True:
            data = await websocket.receive_json()

            if data.get("type") == "heartbeat":
                connected_laptops[laptop_id]["last_seen"] = (
                    datetime.now(timezone.utc)
                )
                connected_laptops[laptop_id]["status"] = "online"

            elif data.get("type") == "command_result":
                print(
                    f"Command result from {laptop_id}: "
                    f"{data.get('command')} - {data.get('result')}"
                )

    except WebSocketDisconnect:
        print(f"Laptop disconnected: {laptop_id}")

    finally:
        connected_laptops.pop(laptop_id, None)


@app.get("/laptops")
async def get_laptops():
    result = {}

    for laptop_id, laptop in connected_laptops.items():
        result[laptop_id] = {
            "status": laptop["status"],
            "last_seen": laptop["last_seen"].isoformat()
        }

    return JSONResponse(content=result)


@app.post("/command/{laptop_id}")
async def send_command(
    laptop_id: str,
    command: dict,
    authorization: str | None = Header(default=None)
):
    check_token(authorization)

    if laptop_id not in connected_laptops:
        raise HTTPException(
            status_code=404,
            detail="Laptop is offline"
        )

    allowed_commands = {
        "lock",
        "sleep",
        "restart",
        "shutdown"
    }

    requested_command = command.get("command")

    if requested_command not in allowed_commands:
        raise HTTPException(
            status_code=400,
            detail="Invalid command"
        )

    websocket = connected_laptops[laptop_id]["websocket"]

    await websocket.send_json({
        "type": "command",
        "command": requested_command
    })

    return {
        "success": True,
        "message": "Command sent to laptop",
        "command": requested_command
    }


async def monitor_laptops():
    while True:
        now = datetime.now(timezone.utc)

        for laptop_id in list(connected_laptops.keys()):
            last_seen = connected_laptops[laptop_id]["last_seen"]

            if (now - last_seen).total_seconds() > 30:
                print(f"Laptop timed out: {laptop_id}")
                connected_laptops.pop(laptop_id, None)

        await asyncio.sleep(10)


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(monitor_laptops())
