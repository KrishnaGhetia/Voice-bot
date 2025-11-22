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

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY")
)


# ---------------- STT FIX ----------------

ASSEMBLYAI_KEY = os.getenv("ASSEMBLYAI_API_KEY")

def whisper_stt(audio_bytes):
    """
    Replaces Whisper with AssemblyAI STT.
    Supports WebM audio directly.
    """

    # Upload audio to AssemblyAI
    upload_url = "https://api.assemblyai.com/v2/upload"
    headers = {"authorization": ASSEMBLYAI_KEY}

    # Send in chunks (required by AssemblyAI)
    def gen():
        yield audio_bytes

    upload_res = requests.post(upload_url, headers=headers, data=gen())
    audio_url = upload_res.json()["upload_url"]

    # Create transcription job
    transcript_res = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        json={"audio_url": audio_url},
        headers=headers
    )

    transcript_id = transcript_res.json()["id"]

    # Poll until transcription is ready
    while True:
        status_res = requests.get(
            f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
            headers=headers
        ).json()

        if status_res["status"] == "completed":
            return status_res["text"]
        elif status_res["status"] == "error":
            raise RuntimeError(f"AssemblyAI error: {status_res['error']}")




# ---------------- TTS ----------------
def make_tts_bytes(text: str) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        path = tmp.name

    tts = gTTS(text=text, lang="en")
    tts.save(path)

    with open(path, "rb") as f:
        data = f.read()

    os.remove(path)
    return data


def should_emit_audio(buffer_text: str, last_token: str, char_threshold=120):
    if len(buffer_text) >= char_threshold:
        return True
    if last_token.strip().endswith((".", "?", "!")):
        return True
    return False


# ---------------- MAIN STREAM ENDPOINT ----------------
@app.route("/stream", methods=["POST"])
def stream():
    audio_bytes = request.data

    try:
        user_text = whisper_stt(audio_bytes)
    except Exception as e:
        print("STT error:", e)
        user_text = ""

    print("User said:", user_text)

    if not user_text.strip():
        user_text = "Hello?"

    def event_stream():
        yield f"data: TEXT::*User*: {user_text}\n\n"

        stream_resp = client.chat.completions.create(
            model="openrouter/gpt-oss-20b",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Provide **short, concise answers** (max 200-300 words). "
                        "Do NOT write long essays unless the user clearly asks for a detailed explanation. "
                        "If the user wants more detail, they will ask for it explicitly. "
                        "Focus on clarity and summarizing first."
                    )
                },
                {
                    "role": "user",
                    "content": user_text
                }
            ],
            stream=True
        )


        buffer_for_tts = ""
        last_token = ""

        for chunk in stream_resp:
            if chunk.choices and chunk.choices[0].delta.content:
                token = chunk.choices[0].delta.content
                last_token = token

                # stream text token immediately
                yield f"data: TEXT::{token}\n\n"

                buffer_for_tts += token

                if should_emit_audio(buffer_for_tts, last_token):
                    try:
                        tts_bytes = make_tts_bytes(buffer_for_tts)
                        b64 = base64.b64encode(tts_bytes).decode()
                        yield f"data: AUDIO::{b64}\n\n"
                        buffer_for_tts = ""
                    except Exception as e:
                        print("TTS error:", e)

        if buffer_for_tts.strip():
            tts_bytes = make_tts_bytes(buffer_for_tts)
            b64 = base64.b64encode(tts_bytes).decode()
            yield f"data: AUDIO::{b64}\n\n"

        yield "data: DONE\n\n"

    return Response(event_stream(), mimetype="text/event-stream")


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    from flask import Flask
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
