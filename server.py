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


# ----------- STT -----------
def whisper_stt(audio_bytes: bytes) -> str:
    headers = {"authorization": ASSEMBLYAI_KEY}

    # Upload
    upload_res = requests.post(
        "https://api.assemblyai.com/v2/upload",
        headers=headers,
        data=audio_bytes
    )
    upload_res.raise_for_status()
    audio_url = upload_res.json()["upload_url"]

    # Transcription job
    transcript_res = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        headers=headers,
        json={"audio_url": audio_url},
    )
    transcript_res.raise_for_status()
    transcript_id = transcript_res.json()["id"]

    # Poll
    while True:
        poll = requests.get(
            f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
            headers=headers,
        ).json()
        if poll["status"] == "completed":
            return poll.get("text", "")
        if poll["status"] == "error":
            raise RuntimeError(poll.get("error"))
        time.sleep(1)


# ----------- TTS -----------
def make_tts_bytes(text: str) -> bytes:
    if not text.strip():
        return b""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        path = tmp.name
    tts = gTTS(text=text, lang="en")
    tts.save(path)
    with open(path, "rb") as f:
        data = f.read()
    os.remove(path)
    return data


# Speak only **after full sentence** OR if text too long (fallback)
def should_emit_audio(buffer_text: str, last_token: str) -> bool:
    last_token = last_token.strip()
    if last_token.endswith(('.', '!', '?')):
        return True
    if len(buffer_text) > 260:  # fallback safety (long reply)
        return True
    return False


# ----------- STREAM -----------
@app.route("/stream", methods=["POST"])
def stream():
    audio_bytes = request.data

    try:
        user_text = whisper_stt(audio_bytes)
    except Exception as e:
        print("STT error:", e)
        user_text = ""

    user_text = user_text.strip() or "Hello?"

    def event_stream():
        yield f"data: TEXT::*User*: {user_text}\n\n"

        try:
            stream_resp = client.chat.completions.create(
                model="openai/gpt-oss-20b",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Provide short, concise answers — 200 to 300 words max. "
                            "Explain only in detail if the user asks explicitly."
                        )
                    },
                    {"role": "user", "content": user_text},
                ],
                stream=True,
            )
        except Exception as e:
            import traceback
            print("\n\n🔴 OPENROUTER ERROR BEGIN 🔴")
            traceback.print_exc()
            print("🔴 OPENROUTER ERROR END 🔴\n\n")
            yield "data: TEXT::Sorry, I had an error talking to the AI model.\n\n"
            yield "data: DONE\n\n"
            return

        buffer_for_tts = ""
        last_token = ""

        for chunk in stream_resp:
            token = ""
            try:
                token = chunk.choices[0].delta.content or ""
            except Exception:
                pass

            if not token:
                continue

            last_token = token
            yield f"data: TEXT::{token}\n\n"  # text streaming
            buffer_for_tts += token

            if should_emit_audio(buffer_for_tts, last_token):
                try:
                    tts_bytes = make_tts_bytes(buffer_for_tts)
                    buffer_for_tts = ""  # Clear before speaking
                    if tts_bytes:
                        b64 = base64.b64encode(tts_bytes).decode()
                        yield f"data: AUDIO::{b64}\n\n"
                except Exception as e:
                    print("TTS chunk error:", e)

        # Flush last part of answer as audio
        if buffer_for_tts.strip():
            try:
                tts_bytes = make_tts_bytes(buffer_for_tts)
                if tts_bytes:
                    b64 = base64.b64encode(tts_bytes).decode()
                    yield f"data: AUDIO::{b64}\n\n"
            except Exception as e:
                print("TTS final error:", e)

        yield "data: DONE\n\n"

    return Response(event_stream(), mimetype="text/event-stream")


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
