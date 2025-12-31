import os
import time
import json
import asyncio
import tempfile
import requests
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse, StreamingResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload  # Changed to this for file-based upload
from google.oauth2.credentials import Credentials

app = FastAPI()

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
REDIRECT_URI = "https://ys-duda.onrender.com/oauth2callback"
TOKEN = {}

# ... (keep the get_home_html function exactly as before)

@app.get("/", response_class=HTMLResponse)
def home():
    show_form = "creds" in TOKEN
    return get_home_html(show_form=show_form)

@app.get("/login")
def login():
    flow = Flow.from_client_secrets_file(
        "client_secret.json",
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent"
    )
    return RedirectResponse(auth_url)

@app.get("/oauth2callback")
def oauth2callback(code: str):
    flow = Flow.from_client_secrets_file(
        "client_secret.json",
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    TOKEN["creds"] = creds.to_json()
    return RedirectResponse("/")

@app.get("/logout")
def logout():
    TOKEN.clear()
    return RedirectResponse("/")

# Progress state
progress_state = {
    "phase": "Idle",
    "dl_bytes": 0, "ul_bytes": 0, "total_size": 0,
    "dl_start": 0, "ul_start": 0,
}

async def progress_generator(request: Request):
    while True:
        if await request.is_disconnected():
            break
        total = progress_state["total_size"] or 1
        dl_percent = round(progress_state["dl_bytes"] / total * 100, 1)
        ul_percent = round(progress_state["ul_bytes"] / total * 100, 1)
        overall = round((progress_state["dl_bytes"] + progress_state["ul_bytes"]) / (total * 2) * 100, 1)
        dl_speed = f"{(progress_state['dl_bytes'] / (time.time() - progress_state['dl_start']) / 1e6):.2f}" if progress_state["dl_start"] else "0.00"
        ul_speed = f"{(progress_state['ul_bytes'] / (time.time() - progress_state['ul_start']) / 1e6):.2f}" if progress_state["ul_start"] else "0.00"
        eta = "Calculating..." if total <= 1 else f"{((total * 2 - progress_state['dl_bytes'] - progress_state['ul_bytes']) / 1e6 / max(float(dl_speed) + float(ul_speed), 1)):.1f} min"
        data = {
            "phase": progress_state["phase"],
            "dl_percent": dl_percent,
            "ul_percent": ul_percent,
            "overall": overall,
            "dl_speed": dl_speed,
            "ul_speed": ul_speed,
            "eta": eta,
            "size": f"{total / 1e6:.1f} MB",
        }
        yield f"data: {json.dumps(data)}\n\n"
        await asyncio.sleep(1)
        if progress_state["phase"] in ["Done", "Error"]:
            yield "data: DONE\n\n"
            break

@app.get("/progress-stream")
async def progress_stream(request: Request):
    return StreamingResponse(progress_generator(request), media_type="text/event-stream")

@app.post("/upload", response_class=HTMLResponse)
async def upload(seedr_url: str = Form(...), title: str = Form(...)):
    if "creds" not in TOKEN:
        return RedirectResponse("/login")

    progress_state.update({
        "phase": "Preparing download...",
        "dl_bytes": 0, "ul_bytes": 0, "total_size": 0,
        "dl_start": time.time(), "ul_start": 0,
    })

    temp_file = None
    try:
        info = json.loads(TOKEN["creds"])
        if "refresh_token" not in info:
            return HTMLResponse("<div class='alert alert-danger'>Missing refresh token. <a href='/logout'>Log out and re-login</a></div>")

        creds = Credentials.from_authorized_user_info(info, SCOPES)
        youtube = build("youtube", "v3", credentials=creds)

        # Get content length first
        head = requests.head(seedr_url, timeout=30)
        total_size = int(head.headers.get('content-length', 0))
        if total_size > 10 * 1024**3:  # Optional: warn/reject >10GB
            raise Exception("Video too large (>10GB) for free hosting limits")

        progress_state["total_size"] = total_size or 1
        progress_state["phase"] = "Downloading from Seedr..."

        # Create temp file on disk
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
        temp_path = temp_file.name
        temp_file.close()

        # Stream download to disk
        with requests.get(seedr_url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(temp_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f.write(chunk)
                        progress_state["dl_bytes"] += len(chunk)

        progress_state["phase"] = "Uploading to YouTube..."
        progress_state["ul_start"] = time.time()

        # Use MediaFileUpload for low-memory resumable upload from disk
        media = MediaFileUpload(temp_path, mimetype="video/mp4", resumable=True, chunksize=1024*1024)

        request = youtube.videos().insert(
            part="snippet,status",
            body={
                "snippet": {"title": title or "Private Video", "categoryId": "22"},
                "status": {"privacyStatus": "private"}
            },
            media_body=media
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                progress_state["ul_bytes"] = int(status.resumable_progress)

        link = f"https://www.youtube.com/watch?v={response['id']}"
        progress_state["phase"] = "Done"
        return f"""
        <div class="alert alert-success">
            <h3>Upload Successful!</h3>
            <a href="{link}" target="_blank">{link}</a><br><br>
            <a href="/">Upload another</a>
        </div>
        <script>document.getElementById('progress-container').style.display = 'none';</script>
        """

    except Exception as e:
        progress_state["phase"] = "Error"
        return f"""
        <div class="alert alert-danger">
            <h3>Error: {str(e)}</h3>
            <a href="/">Go back</a>
        </div>
        """
    finally:
        # Clean up temp file
        if temp_file and os.path.exists(temp_path):
            os.unlink(temp_path)
