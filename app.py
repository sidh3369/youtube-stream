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
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials

app = FastAPI()

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
REDIRECT_URI = "https://ys-duda.onrender.com/oauth2callback"
TOKEN = {}

# ------------------- HTML UI -------------------
def get_home_html(show_form: bool = False):
    login_btn = '<a href="/login"><button class="btn btn-primary btn-lg">Login with Google</button></a>'
    form = """
    <div class="card p-4 mt-4">
        <h3>Upload Video to YouTube (Private)</h3>
        <form id="upload-form" method="post" action="/upload" onsubmit="startProgress()">
            <div class="mb-3">
                <input type="text" name="seedr_url" class="form-control" placeholder="Seedr Direct Download Link" required>
            </div>
            <div class="mb-3">
                <input type="text" name="title" class="form-control" placeholder="Video Title" required>
            </div>
            <button type="submit" class="btn btn-success btn-lg">Start Upload</button>
        </form>

        <div id="progress-container" class="mt-4" style="display:none;">
            <h4 id="phase">Preparing...</h4>
            
            <div class="mb-3">
                <strong>Download Progress</strong>
                <div class="progress mb-2">
                    <div id="dl-bar" class="progress-bar bg-info" style="width:0%">0%</div>
                </div>
                <small id="dl-details">0 MB / 0 MB • 0.00 MB/s</small>
            </div>

            <div class="mb-3">
                <strong>Upload Progress</strong>
                <div class="progress mb-2">
                    <div id="ul-bar" class="progress-bar bg-success" style="width:0%">0%</div>
                </div>
                <small id="ul-details">0 MB / 0 MB • 0.00 MB/s</small>
            </div>

            <div class="mb-3">
                <strong>Overall Progress</strong>
                <div class="progress" style="height:35px;">
                    <div id="overall-bar" class="progress-bar progress-bar-striped progress-bar-animated bg-primary" 
                         style="width:0%; font-size:18px;">0%</div>
                </div>
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
        <title>YouTube Private Uploader</title>
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
                    document.getElementById('dl-bar').style.width = data.dl_percent + '%';
                    document.getElementById('dl-bar').textContent = data.dl_percent + '%';
                    document.getElementById('ul-bar').style.width = data.ul_percent + '%';
                    document.getElementById('ul-bar').textContent = data.ul_percent + '%';
                    document.getElementById('overall-bar').style.width = data.overall + '%';
                    document.getElementById('overall-bar').textContent = data.overall + '%';

                    document.getElementById('dl-details').textContent = 
                        formatBytes(data.dl_bytes) + ' / ' + data.size + ' • ' + data.dl_speed + ' MB/s';
                    document.getElementById('ul-details').textContent = 
                        formatBytes(data.ul_bytes) + ' / ' + data.size + ' • ' + data.ul_speed + ' MB/s';

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
            <h1 class="text-center mb-4">YouTube Private Uploader</h1>
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
    "dl_bytes": 0,
    "ul_bytes": 0,
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

        dl_speed = progress_state["dl_bytes"] / elapsed / (1024*1024) if elapsed > 0 else 0
        ul_speed = progress_state["ul_bytes"] / elapsed / (1024*1024) if elapsed > 0 else 0
        avg_speed = (dl_speed + ul_speed) / 2

        remaining_bytes = total - progress_state["ul_bytes"]
        eta = remaining_bytes / (avg_speed * 1024*1024) if avg_speed > 0 else 999999

        data = {
            "phase": progress_state["phase"],
            "dl_percent": round(progress_state["dl_bytes"] / total * 100, 1),
            "ul_percent": round(progress_state["ul_bytes"] / total * 100, 1),
            "overall": round((progress_state["dl_bytes"] + progress_state["ul_bytes"]) / (total * 2) * 100, 1),
            "dl_bytes": progress_state["dl_bytes"],
            "ul_bytes": progress_state["ul_bytes"],
            "dl_speed": f"{dl_speed:.2f}",
            "ul_speed": f"{ul_speed:.2f}",
            "eta": formatTime(eta) if eta < 999999 else "Calculating...",
            "size": f"{total / (1024*1024):.2f} MB",
        }
        yield f"data: {json.dumps(data)}\n\n"
        await asyncio.sleep(1)

        if progress_state["phase"] in ["Done", "Error"]:
            yield "data: DONE\n\n"
            break

def formatTime(seconds):
    if seconds < 60: return f"{int(seconds)}s"
    if seconds < 3600: return f"{int(seconds//60)}m {int(seconds%60)}s"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h {m}m"

@app.get("/progress-stream")
async def progress_stream(request: Request):
    return StreamingResponse(progress_generator(request), media_type="text/event-stream")

# ------------------- Upload Route -------------------
@app.post("/upload", response_class=HTMLResponse)
async def upload(seedr_url: str = Form(...), title: str = Form(...)):
    if "creds" not in TOKEN:
        return RedirectResponse("/login")

    temp_path = None
    try:
        creds_info = json.loads(TOKEN["creds"])
        if "refresh_token" not in creds_info:
            return HTMLResponse("<div class='alert alert-danger'>Missing refresh token. <a href='/logout'>Re-login</a></div>")

        creds = Credentials.from_authorized_user_info(creds_info, SCOPES)
        youtube = build("youtube", "v3", credentials=creds)

        progress_state.update({
            "phase": "Fetching video info...",
            "dl_bytes": 0, "ul_bytes": 0, "total_size": 0,
            "start_time": time.time(),
        })

        # Get file size
        head = requests.head(seedr_url, timeout=30)
        total_size = int(head.headers.get("content-length", 0))
        if total_size == 0:
            raise Exception("Could not detect video size. Check Seedr link.")
        progress_state["total_size"] = total_size

        # Create temp file
        tf = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
        temp_path = tf.name
        tf.close()

        progress_state["phase"] = "Downloading video from Seedr..."

        # Download with progress
        with requests.get(seedr_url, stream=True, timeout=180) as r:
            r.raise_for_status()
            with open(temp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f.write(chunk)
                        progress_state["dl_bytes"] += len(chunk)

        progress_state["phase"] = "Uploading to YouTube..."
        progress_state["ul_bytes"] = 0

        media = MediaFileUpload(temp_path, mimetype="video/mp4", resumable=True, chunksize=1024*1024)

        request = youtube.videos().insert(
            part="snippet,status",
            body={
                "snippet": {"title": title, "categoryId": "22"},
                "status": {"privacyStatus": "private"}
            },
            media_body=media
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                progress_state["ul_bytes"] = int(status.resumable_progress)

        video_url = f"https://www.youtube.com/watch?v={response['id']}"
        progress_state["phase"] = "Done"

        return f"""
        <div class="alert alert-success text-center p-4">
            <h3>✅ Upload Complete!</h3>
            <p><strong>Title:</strong> {title}</p>
            <a href="{video_url}" target="_blank" class="btn btn-primary btn-lg">Watch on YouTube</a>
            <hr>
            <a href="/">Upload Another Video</a>
        </div>
        """

    except Exception as e:
        progress_state["phase"] = "Error"
        return f"""
        <div class="alert alert-danger">
            <h4>Upload Failed</h4>
            <pre>{str(e)}</pre>
            <a href="/">← Go Back</a>
        </div>
        """
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except:
                pass
