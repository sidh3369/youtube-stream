import io
import time
import json
import asyncio
import requests
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse, StreamingResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as AuthRequest

app = FastAPI()

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
REDIRECT_URI = "https://ys-duda.onrender.com/oauth2callback"
TOKEN = {}

CHUNK_SIZE = 1024 * 1024 * 5  # 5MB chunks - good balance for speed/memory

# ------------------- Custom Streaming Class -------------------
class SeedrStream(io.IOBase):
    def __init__(self, url: str):
        self.url = url
        self.response = requests.get(url, stream=True, timeout=120)
        self.response.raise_for_status()
        self.iterator = self.response.iter_content(chunk_size=CHUNK_SIZE)
        self.buffer = b""
        self.total_size = int(self.response.headers.get("content-length", 0))
        self.transferred = 0

    def readable(self):
        return True

    def read(self, size=-1):
        if size == -1:
            size = CHUNK_SIZE
        while len(self.buffer) < size:
            try:
                chunk = next(self.iterator)
                if not chunk:
                    break
                self.buffer += chunk
                self.transferred += len(chunk)
                progress_state["transferred"] = self.transferred
            except StopIteration:
                break
        data, self.buffer = self.buffer[:size], self.buffer[size:]
        return data

    def size(self):
        return self.total_size

    def close(self):
        self.response.close()

# ------------------- HTML UI -------------------
def get_home_html(show_form: bool = False):
    login_btn = '<a href="/login"><button class="btn btn-primary btn-lg">Login with Google</button></a>'
    form = """
    <div class="card p-4 mt-4">
        <h3>Direct Stream from Seedr → YouTube (Private)</h3>
        <p><small>True streaming: no full download on server!</small></p>
        <form id="upload-form" method="post" action="/upload" onsubmit="startProgress()">
            <div class="mb-3">
                <input type="text" name="seedr_url" class="form-control" placeholder="Seedr Direct Link (e.g. https://rd22.seedr.cc/...)" required>
            </div>
            <div class="mb-3">
                <input type="text" name="title" class="form-control" placeholder="Video Title" required>
            </div>
            <button type="submit" class="btn btn-success btn-lg">Start Streaming Upload</button>
        </form>

        <div id="progress-container" class="mt-4" style="display:none;">
            <h4 id="phase">Preparing...</h4>
            <div class="mb-3">
                <strong>Streaming Progress</strong>
                <div class="progress mb-2" style="height:40px;">
                    <div id="stream-bar" class="progress-bar progress-bar-striped progress-bar-animated bg-success" 
                         style="width:0%; font-size:20px;">0%</div>
                </div>
                <small id="stream-details">0 MB / 0 MB • 0.00 MB/s</small>
            </div>
            <div class="row text-center mt-3">
                <div class="col"><strong>Elapsed:</strong> <span id="elapsed">0s</span></div>
                <div class="col"><strong>ETA:</strong> <span id="eta">--</span></div>
                <div class="col"><strong>Size:</strong> <span id="size">Detecting...</span></div>
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
        <title>Seedr → YouTube Stream Uploader</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <script>
            function formatBytes(bytes) {{ return (bytes / (1024*1024)).toFixed(2) + ' MB'; }}
            function formatTime(sec) {{
                if (sec < 60) return Math.floor(sec) + 's';
                if (sec < 3600) return Math.floor(sec/60) + 'm ' + Math.floor(sec%60) + 's';
                const h = Math.floor(sec/3600);
                return h + 'h ' + Math.floor((sec%3600)/60) + 'm';
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
                    document.getElementById('elapsed').textContent = formatTime(elapsed);
                    document.getElementById('eta').textContent = data.eta;
                    document.getElementById('size').textContent = data.size;
                }};
            }}
        </script>
    </head>
    <body class="bg-light">
        <div class="container mt-5 text-center">
            <h1>Seedr → YouTube Direct Stream</h1>
            {login_btn if not show_form else form}
        </div>
    </body>
    </html>
    """

# ------------------- Routes & Progress -------------------
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
            "progress": round(progress_state["transferred"] / total * 100, 1) if total else 0,
            "transferred": progress_state["transferred"],
            "speed": f"{speed:.2f}",
            "eta": format_time(eta) if eta < 999999 else "Calculating...",
            "size": f"{total / (1024*1024):.2f} MB" if total else "Detecting...",
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
        if creds.expired and creds.refresh_token:
            creds.refresh(AuthRequest())

        youtube = build("youtube", "v3", credentials=creds)

        progress_state.update({
            "phase": "Initializing stream from Seedr...",
            "transferred": 0,
            "total_size": 0,
            "start_time": time.time(),
        })

        # Create streaming object
        stream_obj = SeedrStream(seedr_url)
        progress_state["total_size"] = stream_obj.size() or 1  # Fallback if no content-length

        progress_state["phase"] = "Streaming to YouTube..."

        media = MediaIoBaseUpload(
            stream_obj,
            mimetype="video/mp4",
            chunksize=CHUNK_SIZE,
            resumable=True
        )

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
                progress_state["transferred"] = int(status.resumable_progress)

        video_url = f"https://www.youtube.com/watch?v={response['id']}"
        progress_state["phase"] = "Done"

        return f"""
        <div class="alert alert-success text-center p-4">
            <h3>✅ Streaming Upload Complete!</h3>
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
            <h4>Upload Failed</h4>
            <pre>{str(e)}</pre>
            <a href="/">← Go Back</a>
        </div>
        """
    finally:
        if 'stream_obj' in locals():
            stream_obj.close()
