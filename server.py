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

STOP_REQUEST = False


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
        headers=headers,
        data=audio_bytes
    )
    audio_url = upload.json()["upload_url"]

    transcript = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        headers=headers,
        json={"audio_url": audio_url},
    )
    transcript_id = transcript.json()["id"]

    while True:
        poll = requests.get(
            f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
            headers=headers,
        ).json()
        if poll["status"] == "completed":
            return poll.get("text", "")
        if poll["status"] == "error":
            return ""
        time.sleep(0.8)


# ---------------- TTS (gTTS) ----------------
def make_tts_bytes(text: str):
    text = text.strip()
    if not text:
        return b""

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        path = tmp.name
    gTTS(text=text, lang="en").save(path)
    with open(path, "rb") as f:
        audio = f.read()
    os.remove(path)
    return audio

# ---------------- STREAM ----------------
@app.route("/stream", methods=["POST"])
def stream():
    global STOP_REQUEST
    STOP_REQUEST = False

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
                    {
                        "role": "system",
                        "content": (
                            "Give short, clear answers. Max 250 words. "
                            "Detailed explanation only when user asks."
                        )
                    },
                    {"role": "user", "content": user_text},
                ],
            )
        except:
            yield "data: TEXT::AI error.\n\n"
            yield "data: DONE\n\n"
            return

        buffer = ""
        sentence_enders = (".", "!", "?")

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

            yield f"data: TEXT::{token}\n\n"
            buffer += token

            # Speak only on a complete sentence with enough length
            if len(buffer) > 35 and buffer.strip().endswith(sentence_enders):
                speak = buffer.strip()
                buffer = ""  # reset first

                audio = make_tts_bytes(speak)
                if audio:
                    b64 = base64.b64encode(audio).decode()
                    yield f"data: AUDIO::{b64}\n\n"

        # leftover end of reply
        if buffer.strip() and not STOP_REQUEST:
            audio = make_tts_bytes(buffer)
            if audio:
                b64 = base64.b64encode(audio).decode()
                yield f"data: AUDIO::{b64}\n\n"

        yield "data: DONE\n\n"

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # stops buffering on Render
        },
    )

@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
