from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from google import genai

import asyncio
import json
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
                    f"{data.get('command')} - "
                    f"{data.get('result')}"
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


# ====================== COMMANDS ======================

ALLOWED_COMMANDS = {
    "lock",
    "sleep",
    "restart",
    "shutdown",

    "open_chrome",
    "open_notepad",
    "open_calculator",
    "open_youtube",
    "open_google",
    "open_downloads",
    "open_documents",

    "take_screenshot",

    "volume_up",
    "volume_down",
    "mute",
    "play_pause"
}


# ====================== SEND COMMAND TO LAPTOP ======================

async def send_command_to_laptop(
    laptop_id: str,
    requested_command: str
):

    if laptop_id not in connected_laptops:
        raise HTTPException(
            status_code=404,
            detail="Laptop is offline"
        )

    laptop = connected_laptops[laptop_id]

    if (
        laptop["status"] != "online"
        or laptop["websocket"] is None
    ):
        raise HTTPException(
            status_code=404,
            detail="Laptop is offline"
        )

    if requested_command not in ALLOWED_COMMANDS:
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


# ====================== MANUAL COMMAND ======================

@app.post("/command/{laptop_id}")
async def send_command(
    laptop_id: str,
    command: dict,
    authorization: str | None = Header(default=None)
):

    check_token(authorization)

    requested_command = command.get("command")

    return await send_command_to_laptop(
        laptop_id,
        requested_command
    )


# ====================== AI CHAT MODEL ======================

class AIChatRequest(BaseModel):
    message: str


# ====================== AI SYSTEM PROMPT ======================

AI_SYSTEM_PROMPT = """
You are Remote Control AI, an assistant for the user's own Windows laptop.

You must return ONLY valid JSON.
Do not use Markdown.
Do not include explanations outside the JSON.

For a normal question, return:

{
  "type": "chat",
  "command": null,
  "message": "your answer"
}

For a safe laptop command, return:

{
  "type": "command",
  "command": "command_name",
  "message": "short response"
}

For restart or shutdown, ask for confirmation first:

{
  "type": "confirmation",
  "command": "restart",
  "message": "Are you sure you want to restart your laptop?"
}

Allowed commands:

lock
sleep
restart
shutdown

open_chrome
open_notepad
open_calculator
open_youtube
open_google
open_downloads
open_documents

take_screenshot

volume_up
volume_down
mute
play_pause

Command examples:

"Open Chrome" = open_chrome
"Launch Notepad" = open_notepad
"Open Calculator" = open_calculator
"Go to YouTube" = open_youtube
"Open Google" = open_google
"Open my Downloads folder" = open_downloads
"Open Documents" = open_documents
"Lock my laptop" = lock
"Put my laptop to sleep" = sleep
"Take a screenshot" = take_screenshot
"Increase volume" = volume_up
"Decrease volume" = volume_down
"Mute the volume" = mute
"Play music" = play_pause
"Pause music" = play_pause

For restart and shutdown:

- Never execute immediately.
- First ask for confirmation.
- If the user clearly confirms with "yes", "confirm", "do it", or similar,
  return the actual command.

Do not invent commands.

Do not claim a command was executed unless the server sends it successfully.

Answer laptop information questions using the supplied laptop information.
"""


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

        laptop_context = {
            "status": "Online",
            "battery": laptop.get("battery"),
            "charging": laptop.get("charging"),
            "cpu_usage": laptop.get("cpu_usage"),
            "ram_usage": laptop.get("ram_usage"),
            "hostname": laptop.get("hostname"),
            "windows_version": laptop.get("windows_version")
        }

    else:

        laptop_context = {
            "status": "Offline"
        }

    prompt = f"""
{AI_SYSTEM_PROMPT}

Current laptop information:

{json.dumps(laptop_context, indent=2)}

User message:

{request.message}
"""

    try:

        response = ai_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt
        )

        raw_reply = response.text.strip()

        # Remove Markdown code fences if Gemini adds them.
        if raw_reply.startswith("```"):

            raw_reply = raw_reply.replace("```json", "")
            raw_reply = raw_reply.replace("```", "")
            raw_reply = raw_reply.strip()

        ai_result = json.loads(raw_reply)

        result_type = ai_result.get("type", "chat")
        command = ai_result.get("command")
        message = ai_result.get(
            "message",
            "I could not understand that request."
        )

        # ====================== NORMAL CHAT ======================

        if result_type == "chat":

            return {
                "success": True,
                "type": "chat",
                "command": None,
                "reply": message,
                "laptop": laptop_context
            }

        # ====================== CONFIRMATION ======================

        if result_type == "confirmation":

            return {
                "success": True,
                "type": "confirmation",
                "command": command,
                "reply": message,
                "laptop": laptop_context
            }

        # ====================== EXECUTE COMMAND ======================

        if result_type == "command" and command:

            if command not in ALLOWED_COMMANDS:

                return {
                    "success": True,
                    "type": "chat",
                    "command": None,
                    "reply": "That command is not available."
                }

            # Extra protection for dangerous commands.
            if command in {"restart", "shutdown"}:

                return {
                    "success": True,
                    "type": "confirmation",
                    "command": command,
                    "reply": (
                        "Please confirm before executing "
                        f"{command}."
                    )
                }

            command_result = await send_command_to_laptop(
                "my-laptop",
                command
            )

            return {
                "success": True,
                "type": "command",
                "command": command,
                "reply": message,
                "command_sent": command_result["success"],
                "laptop": laptop_context
            }

        return {
            "success": True,
            "type": "chat",
            "command": None,
            "reply": message,
            "laptop": laptop_context
        }

    except json.JSONDecodeError:

        print("Gemini returned invalid JSON:", raw_reply)

        return {
            "success": True,
            "type": "chat",
            "command": None,
            "reply": raw_reply,
            "laptop": laptop_context
        }

    except HTTPException:
        raise

    except Exception as e:

        print(f"Gemini API error: {e}")

        raise HTTPException(
            status_code=500,
            detail="AI request failed"
        )


# ====================== LAPTOP MONITOR ======================

async def monitor_laptops():

    while True:

        current_time = datetime.now(timezone.utc)

        for laptop_id, laptop in list(
            connected_laptops.items()
        ):

            last_seen = laptop.get("last_seen")

            if last_seen is None:
                continue

            if (
                current_time - last_seen
            ).total_seconds() > 30:

                print(f"Laptop timed out: {laptop_id}")

                laptop["status"] = "offline"
                laptop["websocket"] = None

        await asyncio.sleep(10)


# ====================== STARTUP ======================

@app.on_event("startup")
async def startup_event():

    asyncio.create_task(monitor_laptops())

    print("Remote Control AI server started")
