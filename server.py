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
    global STOP_FLAG
    STOP_FLAG = False

    audio_bytes = request.data
    try:
        user_text = whisper_stt(audio_bytes)
    except:
        user_text = ""

    user_text = user_text.strip() or "Hello?"

    def event_stream():
        yield f"data: TEXT::*User*: {user_text}\n\n"
        time.sleep(0.01)

        try:
            resp = client.chat.completions.create(
                model="openai/gpt-oss-20b",
                stream=True,
                messages=[
                    {"role": "system",
                     "content": "Give short clear answers first. Max 250 words."},
                    {"role": "user", "content": user_text},
                ],
            )
        except Exception as e:
            yield "data: TEXT::AI error occurred.\n\n"
            yield "data: DONE\n\n"
            return

        buffer = ""
        enders = (".", "!", "?")

        for chunk in resp:
            if STOP_FLAG:
                yield "data: DONE\n\n"
                return

            token = ""
            try:
                token = chunk.choices[0].delta.content or ""
            except:
                pass

            if not token:
                continue

            yield f"data: TEXT::{token}\n\n"
            buffer += token
            time.sleep(0.004)

            if buffer.strip().endswith(enders):
                speak = buffer.strip()
                buffer = ""

                try:
                    audio = make_tts_bytes(speak)
                    b64 = base64.b64encode(audio).decode()
                    yield f"data: AUDIO::{b64}\n\n"
                except Exception as e:
                    print("TTS error:", e)

        if buffer.strip() and not STOP_FLAG:
            audio = make_tts_bytes(buffer)
            b64 = base64.b64encode(audio).decode()
            yield f"data: AUDIO::{b64}\n\n"

        yield "data: DONE\n\n"

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@app.route("/")
def index():
    return render_template("index.html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

