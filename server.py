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

# ---- Global STOP flag ----
STOP_REQUEST = False


@app.post("/stop")
def stop():
    """Called when user presses STOP button."""
    global STOP_REQUEST
    STOP_REQUEST = True
    return {"status": "ok"}


# ---------------- STT ----------------
def whisper_stt(audio_bytes: bytes) -> str:
    headers = {"authorization": ASSEMBLYAI_KEY}

    upload = requests.post(
        "https://api.assemblyai.com/v2/upload",
        headers=headers,
        data=audio_bytes
    )
    upload.raise_for_status()
    audio_url = upload.json()["upload_url"]

    transcript = requests.post(
        "https://api.assemblyAI.com/v2/transcript",
        headers=headers,
        json={"audio_url": audio_url},
    )
    transcript.raise_for_status()
    transcript_id = transcript.json()["id"]

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


# -------------- TTS (gTTS working version) ----------------
def make_tts_bytes(text: str) -> bytes:
    text = text.strip()
    if not text:
        return b""

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        path = tmp.name

    try:
        gTTS(text=text, lang="en").save(path)
        with open(path, "rb") as f:
            data = f.read()
    finally:
        try: os.remove(path)
        except: pass

    return data


# ---------------- STREAM (with STOP support) ----------------
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

        try:
            resp = client.chat.completions.create(
                model="openai/gpt-oss-20b",
                stream=True,
                messages=[
                    {"role": "system",
                     "content": "Answer concisely first (max 250 words) unless user asks detail."},
                    {"role": "user", "content": user_text},
                ],
            )
        except:
            yield "data: TEXT::AI error.\n\n"
            yield "data: DONE\n\n"
            return

        buffer = ""            # sentence being built
        sentence_end = (".", "!", "?")

        for chunk in resp:
            token = ""
            try: token = chunk.choices[0].delta.content or ""
            except: pass

            if not token:
                continue

            # live streaming text
            yield f"data: TEXT::{token}\n\n"
            buffer += token

            # send speech only on completed sentence
            if buffer.strip().endswith(sentence_end):
                speak_text = buffer.strip()
                buffer = ""                     # reset BEFORE generating TTS
                try:
                    audio = make_tts_bytes(speak_text)
                    if audio:
                        b64 = base64.b64encode(audio).decode()
                        yield f"data: AUDIO::{b64}\n\n"
                except Exception as e:
                    print("TTS error:", e)

        # leftover small final sentence
        if buffer.strip():
            audio = make_tts_bytes(buffer)
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
