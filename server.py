from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from google import genai

import asyncio
import json
import os
from datetime import datetime, timezone


# ============================================================
# APP
# ============================================================

app = FastAPI(title="Remote Control AI")

connected_laptops = {}

# Laptop is considered offline if no heartbeat is received
# within this many seconds.
HEARTBEAT_TIMEOUT = 15


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

REMOTE_TOKEN = os.getenv("REMOTE_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()


if not REMOTE_TOKEN:
    print("WARNING: REMOTE_TOKEN is not configured")
else:
    print("REMOTE_TOKEN is configured")


if not GEMINI_API_KEY:
    print("WARNING: GEMINI_API_KEY is not configured")


# ============================================================
# GEMINI AI
# ============================================================

ai_client = None

if GEMINI_API_KEY:
    try:
        ai_client = genai.Client(api_key=GEMINI_API_KEY)
        print("Gemini AI initialized")
    except Exception as e:
        print(f"Gemini initialization error: {e}")


# ============================================================
# SECURITY
# ============================================================

def check_token(authorization: str | None):
    """
    Validate:

        Authorization: Bearer YOUR_REMOTE_TOKEN
    """

    if not REMOTE_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="Remote token is not configured on server"
        )

    if authorization is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing token"
        )

    authorization = authorization.strip()

    expected = f"Bearer {REMOTE_TOKEN}"

    if authorization != expected:
        print("Authentication failed")
        print("Authorization header was received")
        print("But the supplied token did not match the server token")

        raise HTTPException(
            status_code=401,
            detail="Invalid or missing token"
        )


# ============================================================
# HOME
# ============================================================

@app.get("/")
async def home():

    current_time = datetime.now(timezone.utc)

    online_count = 0

    for laptop in connected_laptops.values():

        last_seen = laptop.get("last_seen")

        if last_seen:

            seconds_since_heartbeat = (
                current_time - last_seen
            ).total_seconds()

            if seconds_since_heartbeat <= HEARTBEAT_TIMEOUT:
                online_count += 1

    return {
        "message": "Remote Control AI server is running",
        "laptops_online": online_count,
        "ai_enabled": ai_client is not None
    }


# ============================================================
# LAPTOP WEBSOCKET
# ============================================================

@app.websocket("/ws/laptop/{laptop_id}")
async def laptop_websocket(
    websocket: WebSocket,
    laptop_id: str
):

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

    # --------------------------------------------------------
    # Replace old connection if one already exists
    # --------------------------------------------------------

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

            # Ignore messages from an old connection.
            if connected_laptops.get(laptop_id) is not laptop_data:

                print(
                    f"Ignoring old connection: {laptop_id}"
                )

                break

            # =================================================
            # HEARTBEAT
            # =================================================

            if data.get("type") == "heartbeat":

                laptop_data["last_seen"] = (
                    datetime.now(timezone.utc)
                )

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

            # =================================================
            # COMMAND RESULT
            # =================================================

            elif data.get("type") == "command_result":

                print(
                    f"Command result from {laptop_id}: "
                    f"{data.get('command')} - "
                    f"{data.get('result')}"
                )

    except WebSocketDisconnect:

        print(
            f"Laptop disconnected: {laptop_id}"
        )

    except Exception as e:

        print(
            f"WebSocket error for {laptop_id}: {e}"
        )

    finally:

        # Only mark this connection offline if it is still
        # the current connection for this laptop.

        if connected_laptops.get(laptop_id) is laptop_data:

            connected_laptops[laptop_id]["status"] = "offline"

            connected_laptops[laptop_id]["websocket"] = None

            print(
                f"Marked laptop offline: {laptop_id}"
            )


# ============================================================
# GET LAPTOPS
# ============================================================

@app.get("/laptops")
async def get_laptops():

    result = {}

    current_time = datetime.now(timezone.utc)

    for laptop_id, laptop in connected_laptops.items():

        last_seen = laptop.get("last_seen")

        # ----------------------------------------------------
        # Calculate online status from latest heartbeat
        # ----------------------------------------------------

        if last_seen is None:

            is_online = False

        else:

            seconds_since_heartbeat = (
                current_time - last_seen
            ).total_seconds()

            is_online = (
                seconds_since_heartbeat <= HEARTBEAT_TIMEOUT
            )

        # Update stored status.

        laptop["status"] = (
            "online" if is_online else "offline"
        )

        result[laptop_id] = {

            "status": laptop["status"],

            "last_seen": (
                last_seen.isoformat()
                if last_seen
                else None
            ),

            "battery": laptop.get("battery"),

            "charging": laptop.get("charging"),

            "cpu_usage": laptop.get("cpu_usage"),

            "ram_usage": laptop.get("ram_usage"),

            "hostname": laptop.get("hostname"),

            "windows_version": laptop.get(
                "windows_version"
            )
        }

    return JSONResponse(content=result)


# ============================================================
# ALLOWED COMMANDS
# ============================================================

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


# ============================================================
# SEND COMMAND TO LAPTOP
# ============================================================

async def send_command_to_laptop(
    laptop_id: str,
    requested_command: str
):

    # --------------------------------------------------------
    # Check laptop exists
    # --------------------------------------------------------

    if laptop_id not in connected_laptops:

        raise HTTPException(
            status_code=404,
            detail="Laptop is offline"
        )

    laptop = connected_laptops[laptop_id]

    # --------------------------------------------------------
    # Check latest heartbeat
    # --------------------------------------------------------

    last_seen = laptop.get("last_seen")

    if last_seen is None:

        raise HTTPException(
            status_code=404,
            detail="Laptop is offline"
        )

    seconds_since_heartbeat = (
        datetime.now(timezone.utc) - last_seen
    ).total_seconds()

    if (
        seconds_since_heartbeat > HEARTBEAT_TIMEOUT
        or laptop.get("websocket") is None
    ):

        laptop["status"] = "offline"

        laptop["websocket"] = None

        raise HTTPException(
            status_code=404,
            detail="Laptop is offline"
        )

    # --------------------------------------------------------
    # Validate command
    # --------------------------------------------------------

    if requested_command not in ALLOWED_COMMANDS:

        raise HTTPException(
            status_code=400,
            detail="Invalid command"
        )

    # --------------------------------------------------------
    # Send command through WebSocket
    # --------------------------------------------------------

    await laptop["websocket"].send_json({

        "type": "command",

        "command": requested_command
    })

    print(
        f"Command sent: {laptop_id} -> "
        f"{requested_command}"
    )

    return {

        "success": True,

        "message": "Command sent to laptop",

        "command": requested_command
    }


# ============================================================
# MANUAL COMMAND
# ============================================================

@app.post("/command/{laptop_id}")
async def send_command(

    laptop_id: str,

    command: dict,

    authorization: str | None = Header(
        default=None
    )
):

    # --------------------------------------------------------
    # SECURITY CHECK
    # --------------------------------------------------------

    check_token(authorization)

    # --------------------------------------------------------
    # Get command
    # --------------------------------------------------------

    requested_command = command.get("command")

    if not requested_command:

        raise HTTPException(
            status_code=400,
            detail="Command is required"
        )

    print(
        f"Command request received: "
        f"{laptop_id} -> {requested_command}"
    )

    # --------------------------------------------------------
    # Send command
    # --------------------------------------------------------

    return await send_command_to_laptop(
        laptop_id,
        requested_command
    )


# ============================================================
# AI CHAT REQUEST
# ============================================================

class AIChatRequest(BaseModel):

    message: str


# ============================================================
# AI SYSTEM PROMPT
# ============================================================

AI_SYSTEM_PROMPT = """

You are Remote Control AI, an assistant for the user's own
Windows laptop.

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
- If the user clearly confirms with "yes", "confirm",
  "do it", or similar, return the actual command.

Do not invent commands.

Do not claim a command was executed unless the server sends
it successfully.

Answer laptop information questions using the supplied
laptop information.

"""


# ============================================================
# AI CHAT
# ============================================================

@app.post("/ai/chat")
async def ai_chat(

    request: AIChatRequest,

    authorization: str | None = Header(
        default=None
    )
):

    # --------------------------------------------------------
    # SECURITY
    # --------------------------------------------------------

    check_token(authorization)

    # --------------------------------------------------------
    # Check Gemini
    # --------------------------------------------------------

    if ai_client is None:

        raise HTTPException(
            status_code=503,
            detail="Gemini AI is not configured"
        )

    # --------------------------------------------------------
    # Get laptop
    # --------------------------------------------------------

    laptop = connected_laptops.get(
        "my-laptop"
    )

    # --------------------------------------------------------
    # Current laptop status
    # --------------------------------------------------------

    laptop_context = {

        "status": "Offline"
    }

    if laptop:

        last_seen = laptop.get(
            "last_seen"
        )

        if last_seen:

            seconds_since_heartbeat = (
                datetime.now(timezone.utc)
                - last_seen
            ).total_seconds()

            if (
                seconds_since_heartbeat
                <= HEARTBEAT_TIMEOUT
            ):

                laptop_context = {

                    "status": "Online",

                    "battery": laptop.get(
                        "battery"
                    ),

                    "charging": laptop.get(
                        "charging"
                    ),

                    "cpu_usage": laptop.get(
                        "cpu_usage"
                    ),

                    "ram_usage": laptop.get(
                        "ram_usage"
                    ),

                    "hostname": laptop.get(
                        "hostname"
                    ),

                    "windows_version":
                        laptop.get(
                            "windows_version"
                        )
                }

            else:

                laptop["status"] = "offline"

                laptop["websocket"] = None

    # --------------------------------------------------------
    # Create AI prompt
    # --------------------------------------------------------

    prompt = f"""
{AI_SYSTEM_PROMPT}

Current laptop information:

{json.dumps(
    laptop_context,
    indent=2
)}

User message:

{request.message}
"""

    try:

        # ----------------------------------------------------
        # Gemini request
        # ----------------------------------------------------

        response = ai_client.models.generate_content(

            model="gemini-3.6-flash",

            contents=prompt
        )

        raw_reply = response.text.strip()

        # ----------------------------------------------------
        # Remove Markdown fences
        # ----------------------------------------------------

        if raw_reply.startswith("```"):

            raw_reply = raw_reply.replace(
                "```json",
                ""
            )

            raw_reply = raw_reply.replace(
                "```",
                ""
            )

            raw_reply = raw_reply.strip()

        # ----------------------------------------------------
        # Parse JSON
        # ----------------------------------------------------

        ai_result = json.loads(
            raw_reply
        )

        result_type = ai_result.get(
            "type",
            "chat"
        )

        command = ai_result.get(
            "command"
        )

        message = ai_result.get(
            "message",
            "I could not understand that request."
        )

        # ====================================================
        # NORMAL CHAT
        # ====================================================

        if result_type == "chat":

            return {

                "success": True,

                "type": "chat",

                "command": None,

                "reply": message,

                "laptop": laptop_context
            }

        # ====================================================
        # CONFIRMATION
        # ====================================================

        if result_type == "confirmation":

            return {

                "success": True,

                "type": "confirmation",

                "command": command,

                "reply": message,

                "laptop": laptop_context
            }

        # ====================================================
        # EXECUTE COMMAND
        # ====================================================

        if (
            result_type == "command"
            and command
        ):

            if command not in ALLOWED_COMMANDS:

                return {

                    "success": True,

                    "type": "chat",

                    "command": None,

                    "reply":
                        "That command is not available."
                }

            # ------------------------------------------------
            # Extra protection
            # ------------------------------------------------

            if command in {
                "restart",
                "shutdown"
            }:

                return {

                    "success": True,

                    "type": "confirmation",

                    "command": command,

                    "reply": (
                        "Please confirm before "
                        f"executing {command}."
                    )
                }

            # ------------------------------------------------
            # Send command
            # ------------------------------------------------

            command_result = (
                await send_command_to_laptop(
                    "my-laptop",
                    command
                )
            )

            return {

                "success": True,

                "type": "command",

                "command": command,

                "reply": message,

                "command_sent":
                    command_result["success"],

                "laptop": laptop_context
            }

        # ====================================================
        # FALLBACK
        # ====================================================

        return {

            "success": True,

            "type": "chat",

            "command": None,

            "reply": message,

            "laptop": laptop_context
        }

    # ========================================================
    # INVALID GEMINI JSON
    # ========================================================

    except json.JSONDecodeError:

        print(
            "Gemini returned invalid JSON:",
            raw_reply
        )

        return {

            "success": True,

            "type": "chat",

            "command": None,

            "reply": raw_reply,

            "laptop": laptop_context
        }

    # ========================================================
    # HTTP ERROR
    # ========================================================

    except HTTPException:

        raise

    # ========================================================
    # OTHER ERROR
    # ========================================================

    except Exception as e:

        print(
            f"Gemini API error: {e}"
        )

        raise HTTPException(

            status_code=500,

            detail="AI request failed"
        )


# ============================================================
# LAPTOP MONITOR
# ============================================================

async def monitor_laptops():

    while True:

        current_time = datetime.now(
            timezone.utc
        )

        for laptop_id, laptop in list(
            connected_laptops.items()
        ):

            last_seen = laptop.get(
                "last_seen"
            )

            if last_seen is None:

                continue

            seconds_since_heartbeat = (
                current_time - last_seen
            ).total_seconds()

            if (
                seconds_since_heartbeat
                > HEARTBEAT_TIMEOUT
            ):

                # Only print once when changing
                # from online to offline.

                if (
                    laptop.get("status")
                    == "online"
                ):

                    print(
                        f"Laptop timed out: "
                        f"{laptop_id}"
                    )

                laptop["status"] = "offline"

                laptop["websocket"] = None

        await asyncio.sleep(5)


# ============================================================
# STARTUP
# ============================================================

@app.on_event("startup")
async def startup_event():

    asyncio.create_task(
        monitor_laptops()
    )

    print(
        "Remote Control AI server started"
    )
