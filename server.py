import os
import time
import base64
import tempfile
import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, Response
from openai import OpenAI
from gtts import gTTS
from gevent import monkey
import gevent

monkey.patch_all()   # REQUIRED for Render to flush SSE instantly

load_dotenv()
app = Flask(__name__, static_folder="static", template_folder="templates")

# ---- OpenRouter ----
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    default_headers={
        "Referer": "https://voice-bot-lr4b.onrender.com",
        "X-Title": "Voice AI Bot",
    },
)

# ---- AssemblyAI ----
ASSEMBLYAI_KEY = os.getenv("ASSEMBLYAI_API_KEY")
if not ASSEMBLYAI_KEY:
    raise RuntimeError("ASSEMBLYAI_API_KEY is missing")

STOP_REQUEST = False   # used for STOP button


@app.post("/stop")
def stop():
    global STOP_REQUEST
    STOP_REQUEST = True
    return {"status": "ok"}


# ---------------- STT ----------------
def whisper_stt(audio_bytes):
    headers = {"authorization": ASSEMBLYAI_KEY}

    upload = requests.post(
        "https://api.assemblyai.com/v2/upload",
        headers=headers, data=audio_bytes
    )
    upload.raise_for_status()
    audio_url = upload.json()["upload_url"]

    transcript = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        headers=headers, json={"audio_url": audio_url}
    )
    transcript.raise_for_status()
    tid = transcript.json()["id"]

    while True:
        res = requests.get(
            f"https://api.assemblyai.com/v2/transcript/{tid}",
            headers=headers
        ).json()
        if res["status"] == "completed":
            return res.get("text", "")
        if res["status"] == "error":
            raise RuntimeError(res.get("error"))
        time.sleep(1)


# ---------------- TTS (gTTS working) ----------------
def make_tts_bytes(text: str) -> bytes:
    text = text.strip()
    if not text:
        return b""

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        path = tmp.name

    try:
        gTTS(text=text, lang="en").save(path)
        with open(path, "rb") as f:
            audio = f.read()
    finally:
        try: os.remove(path)
        except: pass

    return audio


# ---------------- STREAM (Render-safe, STOP support) ----------------
@app.route("/stream", methods=["POST"])
def stream():
    global STOP_REQUEST
    STOP_REQUEST = False  # reset every new conversation

    audio_bytes = request.data

    try:
        user_text = whisper_stt(audio_bytes)
    except:
        user_text = ""

    user_text = user_text.strip() or "Hello?"

    def event_stream():
        yield f"data: TEXT::*User*: {user_text}\n\n"
        gevent.sleep(0.02)

        try:
            resp = client.chat.completions.create(
                model="openai/gpt-oss-20b",
                stream=True,
                messages=[
                    {"role": "system",
                     "content": "Reply concisely first (max 250 words)."},
                    {"role": "user", "content": user_text},
                ],
            )
        except Exception as e:
            yield "data: TEXT::AI error.\n\n"
            yield "data: DONE\n\n"
            return

        buffer = ""
        enders = (".", "!", "?")

        for chunk in resp:
            if STOP_REQUEST:
                yield "data: DONE\n\n"
                return

            token = ""
            try:
                token = chunk.choices[0].delta.content or ""
            except:
                pass
            if not token:
                continue

            # live streaming text
            yield f"data: TEXT::{token}\n\n"
            gevent.sleep(0.01)
            buffer += token

            # speak only completed sentences
            if buffer.strip().endswith(enders):
                speak = buffer.strip()
                buffer = ""          # clear BEFORE generating TTS

                audio = make_tts_bytes(speak)
                if audio:
                    b64 = base64.b64encode(audio).decode()
                    yield f"data: AUDIO::{b64}\n\n"
                    gevent.sleep(0.02)

        # leftover
        if buffer.strip() and not STOP_REQUEST:
            audio = make_tts_bytes(buffer)
            if audio:
                b64 = base64.b64encode(audio).decode()
                yield f"data: AUDIO::{b64}\n\n"
                gevent.sleep(0.02)

        yield "data: DONE\n\n"

    # 🔥 Render fix — disable buffering to stop mid-sentence audio glitches
    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
