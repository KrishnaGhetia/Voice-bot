import os
import time
import base64
import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, Response
from openai import OpenAI

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


# ----------- STT (AssemblyAI) -----------
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
        "https://api.assemblyai.com/v2/transcript",
        headers=headers,
        json={"audio_url": audio_url},
    )
    transcript.raise_for_status()
    transcript_id = transcript.json()["id"]

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
        

# ----------- TTS (StreamElements / Amazon Polly) -----------
# ----------- TTS (Silicon / natural voices) -----------
def make_tts_bytes(text: str) -> bytes:
    if not text.strip():
        return b""

    url = "https://api.siliconflow.cn/v1/audio/speech"
    json_data = {
        "audio_format": "mp3",
        "text": text,
        "voice": "alloy",       # voices: alloy, echo, river, verse, nova
        "speed": 1.0
    }

    # API requires NO key
    res = requests.post(url, json=json_data)
    res.raise_for_status()

    return res.content



# Speak only after sentence ends, or too long
def should_emit_audio(buffer: str, last: str) -> bool:
    last = last.strip()
    if last.endswith(('.', '!', '?')):
        return True
    if len(buffer) > 260:
        return True
    return False


# ----------- STREAM -----------    
@app.route("/stream", methods=["POST"])
def stream():
    audio_bytes = request.data

    try:
        user_text = whisper_stt(audio_bytes)
    except Exception as e:
        print("STT Error:", e)
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
                        "content":
                        "Provide short, clear answers (200–300 words max). "
                        "Explain in detail ONLY if the user asks."
                    },
                    {"role": "user", "content": user_text},
                ],
                stream=True,
            )
        except Exception as e:
            print("AI ERROR:", e)
            yield "data: TEXT::Sorry, there was an error talking to the AI.\n\n"
            yield "data: DONE\n\n"
            return

        buffer = ""
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
            buffer += token
            yield f"data: TEXT::{token}\n\n"

            if should_emit_audio(buffer, last_token):
                try:
                    audio = make_tts_bytes(buffer)
                    buffer = ""
                    if audio:
                        b64 = base64.b64encode(audio).decode()
                        yield f"data: AUDIO::{b64}\n\n"
                except Exception as e:
                    print("TTS ERROR:", e)

        if buffer.strip():
            try:
                audio = make_tts_bytes(buffer)
                if audio:
                    b64 = base64.b64encode(audio).decode()
                    yield f"data: AUDIO::{b64}\n\n"
            except Exception as e:
                print("FINAL TTS ERROR:", e)

        yield "data: DONE\n\n"

    return Response(event_stream(), mimetype="text/event-stream")


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
