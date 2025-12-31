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
from google.auth.transport.requests import Request as AuthRequest

app = FastAPI()

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
REDIRECT_URI = "https://ys-duda.onrender.com/oauth2callback"
TOKEN = {}

# ------------------- HTML UI with Detailed Progress -------------------
def get_home_html(show_form: bool = False):
    login_btn = '<a href="/login"><button class="btn btn-primary btn-lg">Login with Google</button></a>'
    form = """
    <div class="card p-4 mt-4 shadow">
        <h3>Seedr → YouTube Private Uploader (5-6 GB+ Supported)</h3>
        <p><small>Uses minimal RAM • Temporary disk for reliability • Live progress</small></p>
        <form id="upload-form" method="post" action="/upload" onsubmit="startProgress()">
            <div class="mb-3">
                <input type="text" name="seedr_url" class="form-control form-control-lg" placeholder="Seedr Direct Link" required>
            </div>
            <div class="mb-3">
                <input type="text" name="title" class="form-control form-control-lg" placeholder="Video Title" required>
            </div>
            <button type="submit" class="btn btn-success btn-lg">Upload to YouTube (Private)</button>
        </form>

        <div id="progress-container" class="mt-5" style="display:none;">
            <h4 id="phase" class="text-primary">Starting...</h4>
            
            <div class="mb-4">
                <strong>Download from Seedr</strong>
                <div class="progress mb-2" style="height:30px;">
                    <div id="dl-bar" class="progress-bar bg-info progress-bar-striped" style="width:0%">0%</div>
                </div>
                <small id="dl-details">0 MB / 0 MB • 0.00 MB/s</small>
            </div>

            <div class="mb-4">
                <strong>Upload to YouTube</strong>
                <div class="progress mb-2" style="height:30px;">
                    <div id="ul-bar" class="progress-bar bg-success progress-bar-striped" style="width:0%">0%</div>
                </div>
                <small id="ul-details">0 MB / 0 MB • 0.00 MB/s</small>
            </div>

            <div class="mb-3">
                <strong>Overall Progress</strong>
                <div class="progress" style="height:40px;">
                    <div id="overall-bar" class="progress-bar bg-primary progress-bar-striped progress-bar-animated" style="width:0%; font-size:20px;">0%</div>
                </div>
            </div>

            <div class="row text-center mt-4 fs-5">
                <div class="col"><strong>Elapsed:</strong> <span id="elapsed">0s</span></div>
                <div class="col"><strong>ETA:</strong> <span id="eta">--</span></div>
                <div class="col"><strong>Size:</strong> <span id="size">Detecting...</span></div>
            </div>
        </div>

        <div id="result" class="mt-5"></div>
    </div>
    """
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Seedr to YouTube Uploader</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <script>
            function formatBytes(b) {{ return (b / (1024*1024)).toFixed(2) + ' MB'; }}
            function formatTime(s) {{
                if (s < 60) return Math.floor(s) + 's';
                if (s < 3600) return Math.floor(s/60) + 'm ' + Math.floor(s%60) + 's';
                const h = Math.floor(s/3600); return h + 'h ' + Math.floor((s%3600)/60) + 'm';
            }}
            function startProgress() {{
                document.getElementById('progress-container').style.display = 'block';
                const evtSource = new EventSource("/progress-stream");
                const start = Date.now();
                evtSource.onmessage = function(e) {{
                    if (e.data === "DONE") {{ evtSource.close(); return; }}
                    const d = JSON.parse(e.data);
                    document.getElementById('phase').textContent = d.phase;
                    document.getElementById('dl-bar').style.width = d.dl_percent + '%'; document.getElementById('dl-bar').textContent = d.dl_percent + '%';
                    document.getElementById('ul-bar').style.width = d.ul_percent + '%'; document.getElementById('ul-bar').textContent = d.ul_percent + '%';
                    document.getElementById('overall-bar').style.width = d.overall + '%'; document.getElementById('overall-bar').textContent = d.overall + '%';
                    document.getElementById('dl-details').textContent = formatBytes(d.dl_bytes) + ' / ' + d.size + ' • ' + d.dl_speed + ' MB/s';
                    document.getElementById('ul-details').textContent = formatBytes(d.ul_bytes) + ' / ' + d.size + ' • ' + d.ul_speed + ' MB/s';
                    const elapsed = (Date.now() - start) / 1000;
                    document.getElementById('elapsed').textContent = formatTime(elapsed);
                    document.getElementById('eta').textContent = d.eta;
                    document.getElementById('size').textContent = d.size;
                }};
            }}
        </script>
    </head>
    <body class="bg-light min-vh-100">
        <div class="container py-5 text-center">
            <h1 class="display-4 mb-5">Seedr → YouTube</h1>
            {login_btn if not show_form else form}
        </div>
    </body>
    </html>
    """

@app.get("/", response_class=HTMLResponse)
def home():
    return get_home_html(show_form="creds" in TOKEN)

# ... (keep /login, /oauth2callback, /logout exactly as before)

progress_state = {
    "phase": "Idle",
    "dl_bytes": 0, "ul_bytes": 0, "total_size": 0,
    "start_time": 0,
}

async def progress_generator(request: Request):
    progress_state["start_time"] = time.time()
    while True:
        if await request.is_disconnected(): break
        total = progress_state["total_size"] or 1
        elapsed = time.time() - progress_state["start_time"] or 1
        dl_speed = progress_state["dl_bytes"] / elapsed / (1024*1024)
        ul_speed = progress_state["ul_bytes"] / elapsed / (1024*1024)
        avg_speed = (dl_speed + ul_speed) / 2 if (dl_speed + ul_speed) > 0 else 0.01
        remaining = total - progress_state["ul_bytes"]
        eta = remaining / (avg_speed * 1024*1024)
        data = {
            "phase": progress_state["phase"],
            "dl_percent": round(progress_state["dl_bytes"] / total * 100, 1),
            "ul_percent": round(progress_state["ul_bytes"] / total * 100, 1),
            "overall": round((progress_state["dl_bytes"] + progress_state["ul_bytes"]) / (total * 2) * 100, 1),
            "dl_bytes": progress_state["dl_bytes"],
            "ul_bytes": progress_state["ul_bytes"],
            "dl_speed": f"{dl_speed:.2f}",
            "ul_speed": f"{ul_speed:.2f}",
            "eta": formatTime(eta) if avg_speed > 0 else "Calculating...",
            "size": f"{total / (1024*1024):.2f} MB" if total > 0 else "Detecting...",
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

@app.post("/upload", response_class=HTMLResponse)
async def upload(seedr_url: str = Form(...), title: str = Form(...)):
    if "creds" not in TOKEN: return RedirectResponse("/login")

    temp_path = None
    try:
        info = json.loads(TOKEN["creds"])
        if "refresh_token" not in info:
            return HTMLResponse("<div class='alert alert-danger'>Missing refresh token. <a href='/logout'>Re-login</a></div>")

        creds = Credentials.from_authorized_user_info(info, SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(AuthRequest())
        youtube = build("youtube", "v3", credentials=creds)

        progress_state.update({
            "phase": "Detecting file size...",
            "dl_bytes": 0, "ul_bytes": 0, "total_size": 0,
            "start_time": time.time(),
        })

        # Get size
        head = requests.head(seedr_url, timeout=60)
        total_size = int(head.headers.get("content-length", 0))
        if total_size == 0:
            raise Exception("Cannot detect file size. Try a fresh Seedr direct link.")
        progress_state["total_size"] = total_size

        # Temp file
        tf = tempfile.NamedTemporaryFile(delete=False, suffix=".mkv")
        temp_path = tf.name
        tf.close()

        progress_state["phase"] = "Downloading from Seedr (low RAM mode)..."

        # Download chunked to disk
        with requests.get(seedr_url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with open(temp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024*1024*5):  # 5MB chunks
                    if chunk:
                        f.write(chunk)
                        progress_state["dl_bytes"] += len(chunk)

        progress_state["phase"] = "Uploading to YouTube (resumable)..."
        progress_state["ul_bytes"] = progress_state["dl_bytes"]  # Start from full download

        media = MediaFileUpload(temp_path, mimetype="video/*", resumable=True, chunksize=1024*1024*5)

        request = youtube.videos().insert(
            part="snippet,status",
            body={"snippet": {"title": title, "categoryId": "22"}, "status": {"privacyStatus": "private"}},
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
        <div class="alert alert-success text-center p-5">
            <h2>Upload Complete!</h2>
            <p><strong>{title}</strong></p>
            <a href="{link}" target="_blank" class="btn btn-primary btn-lg">Open on YouTube</a>
            <hr class="my-4">
            <a href="/" class="btn btn-outline-secondary">Upload Another</a>
        </div>
        """

    except Exception as e:
        progress_state["phase"] = "Error"
        return f"""
        <div class="alert alert-danger p-5">
            <h4>Failed: {str(e)}</h4>
            <p>Common fixes: Use a fresh Seedr direct link • Ensure file is .mp4/.mkv • Try again</p>
            <a href="/" class="btn btn-outline-light">← Back</a>
        </div>
        """
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
