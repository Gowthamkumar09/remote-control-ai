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
        "laptops_online": sum(
            1
            for laptop in connected_laptops.values()
            if laptop["status"] == "online"
        )
    }


@app.websocket("/ws/laptop/{laptop_id}")
async def laptop_websocket(websocket: WebSocket, laptop_id: str):
    await websocket.accept()

    # Create a unique record for this connection
    laptop_data = {
        "websocket": websocket,
        "status": "online",
        "last_seen": datetime.now(timezone.utc),
        "battery": None,
        "charging": None,
        "cpu_usage": None,
        "ram_usage": None,
        "hostname": laptop_id,
        "windows_version": None
    }

    # Check whether this laptop already has another connection
    old_laptop = connected_laptops.get(laptop_id)

    if old_laptop:
        old_websocket = old_laptop.get("websocket")

        if old_websocket:
            try:
                await old_websocket.close()
            except Exception:
                pass

    # Store the new active connection
    connected_laptops[laptop_id] = laptop_data

    print(f"Laptop connected: {laptop_id}")

    try:
        while True:
            data = await websocket.receive_json()

            # Ignore messages from an old connection
            if connected_laptops.get(laptop_id) is not laptop_data:
                print(f"Ignoring old connection: {laptop_id}")
                break

            if data.get("type") == "heartbeat":
                laptop_data["last_seen"] = datetime.now(timezone.utc)
                laptop_data["status"] = "online"

                # Update laptop information
                for key in [
                    "battery",
                    "charging",
                    "cpu_usage",
                    "ram_usage",
                    "hostname",
                    "windows_version"
                ]:
                    if key in data:
                        laptop_data[key] = data[key]

                print(
                    f"Heartbeat: {laptop_id} | "
                    f"Battery: {laptop_data['battery']}% | "
                    f"CPU: {laptop_data['cpu_usage']}% | "
                    f"RAM: {laptop_data['ram_usage']}%"
                )

            elif data.get("type") == "command_result":
                print(
                    f"Command result from {laptop_id}: "
                    f"{data.get('command')} - {data.get('result')}"
                )

    except WebSocketDisconnect:
        print(f"Laptop disconnected: {laptop_id}")

    except Exception as e:
        print(f"WebSocket error for {laptop_id}: {e}")

    finally:
        # Only clean up if this is still the active connection
        if connected_laptops.get(laptop_id) is laptop_data:
            connected_laptops[laptop_id]["status"] = "offline"
            connected_laptops[laptop_id]["websocket"] = None

            print(f"Marked laptop offline: {laptop_id}")


@app.get("/laptops")
async def get_laptops():
    result = {}

    for laptop_id, laptop in connected_laptops.items():
        result[laptop_id] = {
            "status": laptop.get("status", "offline"),
            "last_seen": (
                laptop["last_seen"].isoformat()
                if laptop.get("last_seen")
                else None
            ),
            "battery": laptop.get("battery"),
            "charging": laptop.get("charging"),
            "cpu_usage": laptop.get("cpu_usage"),
            "ram_usage": laptop.get("ram_usage"),
            "hostname": laptop.get("hostname"),
            "windows_version": laptop.get("windows_version")
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

    laptop = connected_laptops[laptop_id]

    if laptop["status"] != "online" or laptop["websocket"] is None:
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

    await laptop["websocket"].send_json({
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

        for laptop_id, laptop in list(connected_laptops.items()):
            last_seen = laptop.get("last_seen")

            if last_seen is None:
                continue

            if (now - last_seen).total_seconds() > 30:
                print(f"Laptop timed out: {laptop_id}")

                # Only mark the current connection offline
                laptop["status"] = "offline"
                laptop["websocket"] = None

        await asyncio.sleep(10)


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(monitor_laptops())
