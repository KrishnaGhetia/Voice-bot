import os
import time
import base64
import tempfile
import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, Response
from openai import OpenAI
from gtts import gTTS

load_dotenv()
app = Flask(__name__, static_folder="static", template_folder="templates")

# ---------------- OPENROUTER ----------------
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    default_headers={
        "Referer": "https://voice-bot-lr4b.onrender.com",
        "X-Title": "Voice AI Bot",
    },
)

# ---------------- ASSEMBLY AI ----------------
ASSEMBLYAI_KEY = os.getenv("ASSEMBLYAI_API_KEY")
if not ASSEMBLYAI_KEY:
    raise RuntimeError("ASSEMBLYAI_API_KEY missing")


# ---- STOP FLAG FOR INTERRUPT ----
STOP_FLAG = False

@app.post("/stop")
def stop():
    global STOP_FLAG
    STOP_FLAG = True
    return {"status": "ok"}


# ---------------- SPEECH-TO-TEXT ----------------
def whisper_stt(audio_bytes: bytes) -> str:
    headers = {"authorization": ASSEMBLYAI_KEY}

    # Upload audio
    upload = requests.post(
        "https://api.assemblyai.com/v2/upload",
        headers=headers,
        data=audio_bytes
    )
    upload.raise_for_status()
    audio_url = upload.json()["upload_url"]

    # Create transcript job
    transcript = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        headers=headers,
        json={"audio_url": audio_url},
    )
    transcript.raise_for_status()
    transcript_id = transcript.json()["id"]

    # Poll
    while True:
        res = requests.get(
            f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
            headers=headers,
        ).json()
        if res["status"] == "completed":
            return res.get("text", "")
        if res["status"] == "error":
            raise RuntimeError(res.get("error"))
        time.sleep(1)


# ---------------- TEXT-TO-SPEECH ----------------
def make_tts_bytes(text: str) -> bytes:
    text = text.strip()
    if not text:
        return b""

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        path = tmp.name

    try:
        gTTS(text=text, lang="en").save(path)
        with open(path, "rb") as f:
            audio_bytes = f.read()
    finally:
        try: os.remove(path)
        except: pass

    return audio_bytes


# ---------------- STREAM ----------------
@app.route("/stream", methods=["POST"])
def stream():
    audio_bytes = request.data

    try:
        user_text = whisper_stt(audio_bytes)
    except:
        user_text = ""

    user_text = user_text.strip() or "Hello?"

    def event_stream():
        yield f"data: TEXT::*User*: {user_text}\n\n"
        full_text = ""

        try:
            resp = client.chat.completions.create(
                model="openai/gpt-oss-20b",
                stream=True,
                messages=[
                    {"role": "system",
                     "content": "Answer concisely (200–300 words)."},
                    {"role": "user", "content": user_text},
                ],
            )
        except:
            yield "data: TEXT::AI error.\n\n"
            yield "data: DONE\n\n"
            return

        for chunk in resp:
            token = ""
            try:
                token = chunk.choices[0].delta.content or ""
            except:
                pass

            if not token:
                continue

            full_text += token
            yield f"data: TEXT::{token}\n\n"

        # 🔥 TTS after entire response
        audio = make_tts_bytes(full_text)
        if audio:
            b64 = base64.b64encode(audio).decode()
            yield f"data: AUDIO::{b64}\n\n"

        yield "data: DONE\n\n"

    return Response(event_stream(), mimetype="text/event-stream")


@app.route("/")
def index():
    return render_template("index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

