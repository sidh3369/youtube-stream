import io
import time
import requests
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse, StreamingResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2.credentials import Credentials

app = FastAPI()

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
REDIRECT_URI = "https://ys-duda.onrender.com/oauth2callback"
TOKEN = {}

def get_home_html(show_form: bool = False):
    login_btn = '<a href="/login"><button>Login with Google (One Time)</button></a>'
    form = """
    <h2>Upload to YouTube (Private)</h2>
    <form id="upload-form" action="/upload" method="post" hx-post="/upload" hx-target="#result" hx-swap="innerHTML">
        <input type="text" name="seedr_url" placeholder="Paste Seedr Direct Link" size="80" required><br><br>
        <input type="text" name="title" placeholder="Video Title (required)" required><br><br>
        <button type="submit">Upload</button>
    </form>
    <div id="progress-container" style="display:none; margin-top:20px;">
        <h3>Progress</h3>
        <div>Phase: <span id="phase">Preparing...</span></div>
        <div>Download: <span id="dl-progress">0%</span> (<span id="dl-speed">0</span> MB/s)</div>
        <div>Upload: <span id="ul-progress">0%</span> (<span id="ul-speed">0</span> MB/s)</div>
        <div style="background:#eee; border-radius:5px; margin-top:10px;">
            <div id="overall-bar" style="width:0%; height:30px; background:#4caf50; border-radius:5px; text-align:center; color:white;">0%</div>
        </div>
        <div>ETA: <span id="eta">-</span> | Size: <span id="size">0 MB</span></div>
    </div>
    """
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>YouTube Private Uploader</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
        <script>
            function startProgress() {{
                document.getElementById('progress-container').style.display = 'block';
                const evtSource = new EventSource("/progress-stream");
                evtSource.onmessage = function(event) {{
                    if (event.data === "DONE") {{
                        evtSource.close();
                        return;
                    }}
                    const data = JSON.parse(event.data);
                    document.getElementById('phase').textContent = data.phase;
                    document.getElementById('dl-progress').textContent = data.dl_percent + '%';
                    document.getElementById('ul-progress').textContent = data.ul_percent + '%';
                    document.getElementById('dl-speed').textContent = data.dl_speed;
                    document.getElementById('ul-speed').textContent = data.ul_speed;
                    document.getElementById('overall-bar').style.width = data.overall + '%';
                    document.getElementById('overall-bar').textContent = data.overall + '%';
                    document.getElementById('eta').textContent = data.eta;
                    document.getElementById('size').textContent = data.size;
                }};
                evtSource.onerror = function() {{ evtSource.close(); }};
            }}
        </script>
    </head>
    <body class="container mt-5">
        <h2>YouTube Private Uploader</h2>
        {login_btn if not show_form else form}
        <div id="result" class="mt-4"></div>
    </body>
    </html>
    """

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
    auth_url, _ = flow.authorization_url(access_type="offline", include_granted_scopes="true")
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
    TOKEN["creds"] = creds.to_json()  # Better: store as JSON string
    return RedirectResponse("/")

# Global progress state (simple in-memory for single-user app)
progress_state = {
    "phase": "Idle",
    "dl_bytes": 0,
    "ul_bytes": 0,
    "total_size": 0,
    "start_time": 0,
    "dl_start": 0,
    "ul_start": 0,
}

async def progress_generator(request: Request):
    while True:
        if await request.is_disconnected():
            break
        data = {
            "phase": progress_state["phase"],
            "dl_percent": round((progress_state["dl_bytes"] / progress_state["total_size"] * 100) if progress_state["total_size"] > 0 else 0, 1),
            "ul_percent": round((progress_state["ul_bytes"] / progress_state["total_size"] * 100) if progress_state["total_size"] > 0 else 0, 1),
            "overall": round(((progress_state["dl_bytes"] + progress_state["ul_bytes"]) / (progress_state["total_size"] * 2) * 100) if progress_state["total_size"] > 0 else 0, 1),
            "dl_speed": f"{(progress_state['dl_bytes'] / (time.time() - progress_state['dl_start']) / 1e6):.2f}" if progress_state["dl_start"] else "0",
            "ul_speed": f"{(progress_state['ul_bytes'] / (time.time() - progress_state['ul_start']) / 1e6):.2f}" if progress_state["ul_start"] else "0",
            "eta": "Calculating..." if progress_state["total_size"] == 0 else f"{(progress_state['total_size'] * 2 - progress_state['dl_bytes'] - progress_state['ul_bytes']) / 1e6 / 10:.1f} min (est)",  # rough ETA
            "size": f"{progress_state['total_size'] / 1e6:.1f} MB",
        }
        yield f"data: {__import__('json').dumps(data)}\n\n"
        await __import__('asyncio').sleep(1)
        if progress_state["phase"] == "Done" or progress_state["phase"].startswith("Error"):
            yield "data: DONE\n\n"
            break

@app.get("/progress-stream")
async def progress_stream(request: Request):
    return StreamingResponse(progress_generator(request), media_type="text/event-stream")

@app.post("/upload", response_class=HTMLResponse)
async def upload(seedr_url: str = Form(...), title: str = Form(...)):
    if "creds" not in TOKEN:
        return RedirectResponse("/login")

    # Reset progress
    progress_state.update({
        "phase": "Downloading from Seedr...",
        "dl_bytes": 0, "ul_bytes": 0, "total_size": 0,
        "start_time": time.time(), "dl_start": time.time(), "ul_start": 0,
    })

    try:
        creds = Credentials.from_authorized_user_info(__import__('json').loads(TOKEN["creds"]))
        youtube = build("youtube", "v3", credentials=creds)

        # Download with progress
        r = requests.get(seedr_url, stream=True, timeout=60)
        r.raise_for_status()
        content_length = int(r.headers.get('content-length', 0))
        progress_state["total_size"] = content_length

        stream = io.BytesIO()
        for chunk in r.iter_content(chunk_size=1024*1024):
            if chunk:
                stream.write(chunk)
                progress_state["dl_bytes"] += len(chunk)

        stream.seek(0)
        progress_state["phase"] = "Uploading to YouTube..."
        progress_state["ul_start"] = time.time()

        media = MediaIoBaseUpload(stream, mimetype="video/mp4", resumable=True)

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
        progress_state["phase"] = f"Error: {str(e)}"
        return f"""
        <div class="alert alert-danger">
            <h3>Error</h3>
            <pre>{str(e)}</pre>
            <a href="/">Go back</a>
        </div>
        """
