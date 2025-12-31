import os
import time
import json
import asyncio
import requests
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse, StreamingResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import HttpRequest  # For manual requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as AuthRequest

app = FastAPI()

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
REDIRECT_URI = "https://ys-duda.onrender.com/oauth2callback"
TOKEN = {}

# ------------------- HTML UI (Simplified for Streaming) -------------------
def get_home_html(show_form: bool = False):
    login_btn = '<a href="/login"><button class="btn btn-primary btn-lg">Login with Google</button></a>'
    form = """
    <div class="card p-4 mt-4">
        <h3>Direct Stream Upload from Seedr to YouTube (Private)</h3>
        <p><small>No full download on server - streams directly!</small></p>
        <form id="upload-form" method="post" action="/upload" onsubmit="startProgress()">
            <div class="mb-3">
                <input type="text" name="seedr_url" class="form-control" placeholder="Seedr Direct Download Link (must be valid & accessible)" required>
            </div>
            <div class="mb-3">
                <input type="text" name="title" class="form-control" placeholder="Video Title" required>
            </div>
            <button type="submit" class="btn btn-success btn-lg">Stream & Upload</button>
        </form>

        <div id="progress-container" class="mt-4" style="display:none;">
            <h4 id="phase">Preparing stream...</h4>
            
            <div class="mb-3">
                <strong>Stream Progress</strong>
                <div class="progress mb-2">
                    <div id="stream-bar" class="progress-bar bg-primary progress-bar-striped progress-bar-animated" style="width:0%">0%</div>
                </div>
                <small id="stream-details">0 MB / 0 MB • 0.00 MB/s</small>
            </div>

            <div class="row text-center mt-3">
                <div class="col"><strong>Time Elapsed:</strong> <span id="elapsed">0s</span></div>
                <div class="col"><strong>ETA:</strong> <span id="eta">--</span></div>
                <div class="col"><strong>Total Size:</strong> <span id="size">0 MB</span></div>
            </div>
        </div>

        <div id="result" class="mt-4"></div>
    </div>
    """
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Seedr to YouTube Stream Uploader</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <script>
            function formatBytes(bytes) {{
                if (bytes === 0) return '0 MB';
                const mb = bytes / (1024 * 1024);
                return mb.toFixed(2) + ' MB';
            }}
            function formatTime(seconds) {{
                if (seconds < 60) return seconds + 's';
                if (seconds < 3600) return Math.floor(seconds / 60) + 'm ' + (seconds % 60) + 's';
                const h = Math.floor(seconds / 3600);
                const m = Math.floor((seconds % 3600) / 60);
                return h + 'h ' + m + 'm';
            }}
            function startProgress() {{
                document.getElementById('progress-container').style.display = 'block';
                document.getElementById('result').innerHTML = '';
                const evtSource = new EventSource("/progress-stream");
                const startTime = Date.now();
                evtSource.onmessage = function(event) {{
                    if (event.data === "DONE") {{ evtSource.close(); return; }}
                    const data = JSON.parse(event.data);
                    document.getElementById('phase').textContent = data.phase;
                    document.getElementById('stream-bar').style.width = data.progress + '%';
                    document.getElementById('stream-bar').textContent = data.progress + '%';
                    document.getElementById('stream-details').textContent = 
                        formatBytes(data.transferred) + ' / ' + data.size + ' • ' + data.speed + ' MB/s';

                    const elapsed = (Date.now() - startTime) / 1000;
                    document.getElementById('elapsed').textContent = formatTime(Math.floor(elapsed));
                    document.getElementById('eta').textContent = data.eta;
                    document.getElementById('size').textContent = data.size;
                }};
            }}
        </script>
    </head>
    <body class="bg-light">
        <div class="container mt-5">
            <h1 class="text-center mb-4">Seedr → YouTube Direct Stream</h1>
            {login_btn if not show_form else form}
        </div>
    </body>
    </html>
    """

# ------------------- Routes -------------------
@app.get("/", response_class=HTMLResponse)
def home():
    return get_home_html(show_form="creds" in TOKEN)

@app.get("/login")
def login():
    flow = Flow.from_client_secrets_file("client_secret.json", scopes=SCOPES, redirect_uri=REDIRECT_URI)
    auth_url, _ = flow.authorization_url(access_type="offline", include_granted_scopes="true", prompt="consent")
    return RedirectResponse(auth_url)

@app.get("/oauth2callback")
def oauth2callback(code: str):
    flow = Flow.from_client_secrets_file("client_secret.json", scopes=SCOPES, redirect_uri=REDIRECT_URI)
    flow.fetch_token(code=code)
    TOKEN["creds"] = flow.credentials.to_json()
    return RedirectResponse("/")

@app.get("/logout")
def logout():
    TOKEN.clear()
    return RedirectResponse("/")

# ------------------- Progress State -------------------
progress_state = {
    "phase": "Idle",
    "transferred": 0,
    "total_size": 0,
    "start_time": 0,
}

async def progress_generator(request: Request):
    progress_state["start_time"] = time.time()
    while True:
        if await request.is_disconnected():
            break

        total = progress_state["total_size"] or 1
        elapsed = time.time() - progress_state["start_time"]
        speed = progress_state["transferred"] / elapsed / (1024*1024) if elapsed > 0 else 0
        remaining = total - progress_state["transferred"]
        eta = remaining / (speed * 1024*1024) if speed > 0 else 999999

        data = {
            "phase": progress_state["phase"],
            "progress": round(progress_state["transferred"] / total * 100, 1),
            "transferred": progress_state["transferred"],
            "speed": f"{speed:.2f}",
            "eta": format_time(eta) if eta < 999999 else "Calculating...",
            "size": f"{total / (1024*1024):.2f} MB",
        }
        yield f"data: {json.dumps(data)}\n\n"
        await asyncio.sleep(1)

        if progress_state["phase"] in ["Done", "Error"]:
            yield "data: DONE\n\n"
            break

def format_time(seconds):
    if seconds < 60: return f"{int(seconds)}s"
    if seconds < 3600: return f"{int(seconds//60)}m {int(seconds%60)}s"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h {m}m"

@app.get("/progress-stream")
async def progress_stream(request: Request):
    return StreamingResponse(progress_generator(request), media_type="text/event-stream")

# ------------------- Manual Resumable Upload Function -------------------
def stream_upload_to_youtube(youtube, seedr_url, title):
    # Step 1: Initiate resumable session
    body = {
        "snippet": {"title": title, "categoryId": "22"},
        "status": {"privacyStatus": "private"}
    }
    init_request = youtube.videos().insert(part="snippet,status", body=body, media_body=None)
    init_request.uri = init_request.uri.replace("upload/youtube/v3/videos?", "upload/youtube/v3/videos?uploadType=resumable&")
    response = init_request.execute()
    session_uri = response["headers"]["location"]

    # Get total size from Seedr
    head_resp = requests.head(seedr_url, timeout=30)
    total_size = int(head_resp.headers.get("content-length", 0))
    if total_size == 0:
        raise Exception("Cannot determine file size from Seedr link.")
    
    progress_state["total_size"] = total_size
    progress_state["phase"] = "Streaming from Seedr to YouTube..."

    chunk_size = 5 * 1024 * 1024  # 5MB chunks
    bytes_sent = 0

    while bytes_sent < total_size:
        start = bytes_sent
        end = min(start + chunk_size - 1, total_size - 1)
        range_header = f"bytes={start}-{end}"

        # Fetch chunk from Seedr
        seedr_resp = requests.get(seedr_url, headers={"Range": range_header}, stream=True, timeout=120)
        seedr_resp.raise_for_status()
        chunk_data = b""
        for chunk in seedr_resp.iter_content(chunk_size=1024):
            if chunk:
                chunk_data += chunk

        if len(chunk_data) == 0:
            break

        # Upload chunk to YouTube
        content_range = f"bytes {start}-{start + len(chunk_data) - 1}/{total_size}"
        upload_headers = {
            "Content-Range": content_range,
            "Content-Length": str(len(chunk_data))
        }
        upload_resp = requests.put(
            session_uri,
            data=chunk_data,
            headers=upload_headers,
            timeout=120
        )
        upload_resp.raise_for_status()

        bytes_sent += len(chunk_data)
        progress_state["transferred"] = bytes_sent

    # Finalize upload
    if bytes_sent == total_size:
        final_resp = requests.put(session_uri, headers={"Content-Range": f"bytes */{total_size}"}, timeout=30)
        final_resp.raise_for_status()
        video_id = final_resp.json()["id"]
        return f"https://www.youtube.com/watch?v={video_id}"
    else:
        raise Exception("Incomplete upload")

# ------------------- Upload Route -------------------
@app.post("/upload", response_class=HTMLResponse)
async def upload(seedr_url: str = Form(...), title: str = Form(...)):
    if "creds" not in TOKEN:
        return RedirectResponse("/login")

    try:
        creds_info = json.loads(TOKEN["creds"])
        if "refresh_token" not in creds_info:
            return HTMLResponse("<div class='alert alert-danger'>Missing refresh token. <a href='/logout'>Re-login</a></div>")

        creds = Credentials.from_authorized_user_info(creds_info, SCOPES)
        # Refresh if needed
        if creds.expired and creds.refresh_token:
            creds.refresh(AuthRequest())
        youtube = build("youtube", "v3", credentials=creds)

        progress_state.update({
            "phase": "Initializing stream session...",
            "transferred": 0,
            "total_size": 0,
            "start_time": time.time(),
        })

        video_url = stream_upload_to_youtube(youtube, seedr_url, title)
        progress_state["phase"] = "Done"

        return f"""
        <div class="alert alert-success text-center p-4">
            <h3>✅ Stream Upload Complete!</h3>
            <p><strong>Title:</strong> {title}</p>
            <a href="{video_url}" target="_blank" class="btn btn-primary btn-lg">Watch on YouTube</a>
            <hr>
            <a href="/">Upload Another</a>
        </div>
        """

    except Exception as e:
        progress_state["phase"] = "Error"
        return f"""
        <div class="alert alert-danger">
            <h4>Stream Failed</h4>
            <pre>{str(e)}</pre>
            <a href="/">← Go Back</a>
        </div>
        """
