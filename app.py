import io
import requests
from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2.credentials import Credentials

app = FastAPI()

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
REDIRECT_URI = "https://ys-duda.onrender.com/oauth2callback"

TOKEN = {}

@app.get("/", response_class=HTMLResponse)
def home():
    if "creds" not in TOKEN:
        return """
        <h2>YouTube Private Uploader</h2>
        <a href="/login"><button>Login with Google (One Time)</button></a>
        """
    return """
    <h2>Upload to YouTube (Private)</h2>
    <form action="/upload" method="post">
        <input type="text" name="seedr_url" placeholder="Paste Seedr Direct Link" size="80" required><br><br>
        <input type="text" name="title" placeholder="Video Title"><br><br>
        <button type="submit">Upload</button>
    </form>
    """

@app.get("/login")
def login():
    flow = Flow.from_client_secrets_file(
        "client_secret.json",
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true"
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
    TOKEN["creds"] = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }
    return RedirectResponse("/")

@app.post("/upload", response_class=HTMLResponse)
def upload(seedr_url: str = Form(...), title: str = Form("Private Video")):
    creds = Credentials(**TOKEN["creds"])
    youtube = build("youtube", "v3", credentials=creds)

    r = requests.get(seedr_url, stream=True)
    r.raise_for_status()

    stream = io.BytesIO()
    for chunk in r.iter_content(chunk_size=1024 * 1024):
        if chunk:
            stream.write(chunk)
    stream.seek(0)

    media = MediaIoBaseUpload(stream, mimetype="video/mp4", resumable=True)

    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {"title": title, "categoryId": "22"},
            "status": {"privacyStatus": "private"}
        },
        media_body=media
    )

    response = request.execute()
    link = f"https://www.youtube.com/watch?v={response['id']}"

    return f"""
    <h3>Upload Successful</h3>
    <a href="{link}" target="_blank">{link}</a>
    <br><br>
    <a href="/">Upload another</a>
    """
