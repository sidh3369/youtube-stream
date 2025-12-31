import io
import requests
from fastapi import FastAPI, Form
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2.credentials import Credentials

app = FastAPI()

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
REDIRECT_URI = "https://YOUR-RENDER-URL.onrender.com/oauth2callback"

# TEMP storage (in-memory tokens)
TOKEN_STORE = {}

@app.get("/")
def home():
    return {
        "status": "ok",
        "message": "POST /upload with seedr_url"
    }

@app.get("/auth")
def auth():
    flow = Flow.from_client_secrets_file(
        "client_secret.json",
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true"
    )
    TOKEN_STORE["state"] = state
    return {"auth_url": auth_url}

@app.get("/oauth2callback")
def oauth2callback(code: str, state: str):
    flow = Flow.from_client_secrets_file(
        "client_secret.json",
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
        state=state
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    TOKEN_STORE["creds"] = creds_to_dict(creds)
    return {"status": "authorized"}

@app.post("/upload")
def upload(seedr_url: str = Form(...), title: str = Form("Private Video")):
    if "creds" not in TOKEN_STORE:
        return {"error": "Authorize first via /auth"}

    creds = Credentials(**TOKEN_STORE["creds"])
    youtube = build("youtube", "v3", credentials=creds)

    # STREAM from Seedr
    r = requests.get(seedr_url, stream=True, timeout=30)
    r.raise_for_status()

    stream = io.BytesIO()
    for chunk in r.iter_content(chunk_size=1024 * 1024):
        if chunk:
            stream.write(chunk)

    stream.seek(0)

    media = MediaIoBaseUpload(
        stream,
        mimetype="video/mp4",
        resumable=True
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": "Cloud to cloud private upload",
                "categoryId": "22"
            },
            "status": {
                "privacyStatus": "private"
            }
        },
        media_body=media
    )

    response = request.execute()
    return {
        "youtube_link": f"https://www.youtube.com/watch?v={response['id']}"
    }

def creds_to_dict(creds):
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }
