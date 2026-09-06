from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from google import genai

import asyncio
import os
from datetime import datetime, timezone


# ====================== APP ======================

app = FastAPI(title="Remote Control AI")

connected_laptops = {}


# ====================== ENVIRONMENT VARIABLES ======================

REMOTE_TOKEN = os.getenv("REMOTE_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not REMOTE_TOKEN:
    print("WARNING: REMOTE_TOKEN is not configured")
else:
    print("REMOTE_TOKEN is configured")

if not GEMINI_API_KEY:
    print("WARNING: GEMINI_API_KEY is not configured")


# ====================== GEMINI AI ======================

ai_client = None

if GEMINI_API_KEY:
    try:
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
        print("Gemini AI initialized")
    except Exception as e:
        print(f"Gemini initialization error: {e}")


# ====================== SECURITY ======================

def check_token(authorization: str | None):
    expected = f"Bearer {REMOTE_TOKEN}"

    if not REMOTE_TOKEN or authorization != expected:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing token"
        )


# ====================== HOME ======================

@app.get("/")
async def home():
    return {
        "message": "Remote Control AI server is running",
        "laptops_online": sum(
            1
            for laptop in connected_laptops.values()
            if laptop["status"] == "online"
        ),
        "ai_enabled": ai_client is not None
    }


# ====================== LAPTOP WEBSOCKET ======================

@app.websocket("/ws/laptop/{laptop_id}")
async def laptop_websocket(websocket: WebSocket, laptop_id: str):
    await websocket.accept()

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

    old_laptop = connected_laptops.get(laptop_id)

    if old_laptop:
        old_websocket = old_laptop.get("websocket")

        if old_websocket:
            try:
                await old_websocket.close()
            except Exception:
                pass

    connected_laptops[laptop_id] = laptop_data

    print(f"Laptop connected: {laptop_id}")

    try:
        while True:
            data = await websocket.receive_json()

            if connected_laptops.get(laptop_id) is not laptop_data:
                print(f"Ignoring old connection: {laptop_id}")
                break

            if data.get("type") == "heartbeat":
                laptop_data["last_seen"] = datetime.now(timezone.utc)
                laptop_data["status"] = "online"

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
        if connected_laptops.get(laptop_id) is laptop_data:
            connected_laptops[laptop_id]["status"] = "offline"
            connected_laptops[laptop_id]["websocket"] = None

            print(f"Marked laptop offline: {laptop_id}")


# ====================== GET LAPTOPS ======================

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


# ====================== MANUAL COMMAND ======================

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


# ====================== AI CHAT MODEL ======================

class AIChatRequest(BaseModel):
    message: str


# ====================== AI CHAT ======================

@app.post("/ai/chat")
async def ai_chat(
    request: AIChatRequest,
    authorization: str | None = Header(default=None)
):
    check_token(authorization)

    if ai_client is None:
        raise HTTPException(
            status_code=503,
            detail="Gemini AI is not configured"
        )

    laptop = connected_laptops.get("my-laptop")

    if laptop and laptop["status"] == "online":
        laptop_context = f"""
Laptop status: Online
Battery: {laptop.get("battery")}%
Charging: {laptop.get("charging")}
CPU usage: {laptop.get("cpu_usage")}%
RAM usage: {laptop.get("ram_usage")}%
Hostname: {laptop.get("hostname")}
Windows version: {laptop.get("windows_version")}
"""
    else:
        laptop_context = "Laptop status: Offline"

    prompt = f"""
You are Remote Control AI, an assistant for the user's own Windows laptop.

Answer clearly and briefly.

You can explain laptop information and help with safe computer-management
tasks. Do not claim that a command was executed unless the server confirms it.

Current laptop information:
{laptop_context}

User message:
{request.message}
"""

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )

        return {
            "success": True,
            "reply": response.text,
            "laptop": laptop_context
        }

    except Exception as e:
        print(f"Gemini API error: {e}")

        raise HTTPException(
            status_code=500,
            detail="AI request failed"
        )


# ====================== LAPTOP MONITOR ======================

async def monitor_laptops():
    while True:
        now = datetime.now(timezone.utc)

        for laptop_id, laptop in list(connected_laptops.items()):
            last_seen = laptop.get("last_seen")

            if last_seen is None:
                continue

            if (now - last_seen).total_seconds() > 30:
                print(f"Laptop timed out: {laptop_id}")

                laptop["status"] = "offline"
                laptop["websocket"] = None

        await asyncio.sleep(10)


# ====================== STARTUP ======================

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(monitor_laptops())
    print("Remote Control AI server started")
