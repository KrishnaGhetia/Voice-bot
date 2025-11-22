import os
import time
import base64
import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, Response
from openai import OpenAI
from gtts import gTTS
import tempfile

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


# ---------------- STT ----------------
def whisper_stt(audio_bytes):
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
        res = requests.get(
            f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
            headers=headers
        ).json()
        if res["status"] == "completed":
            return res.get("text", "")
        if res["status"] == "error":
            raise Exception(res.get("error"))
        time.sleep(1)


# ---------------- TTS (gTTS FIXED) ----------------
# ----------- Free TTS (Navi TTS – no API key needed) -----------
# ----------- TTS via OpenRouter (FastSpeech2) -----------
def make_tts_bytes(text: str) -> bytes:
    """
    Use OpenRouter's huggingface/facebook-fastspeech2-en model
    to turn text into MP3 audio bytes.
    """
    text = text.strip()
    if not text:
        return b""

    url = "https://openrouter.ai/api/v1/audio/speech"
    headers = {
        "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}",
        "Content-Type": "application/json",
    }
    json_body = {
        "model": "huggingface/facebook-fastspeech2-en",
        "input": text,
        # Optional: "voice" & "format" are model-dependent; this one just uses defaults.
    }

    try:
        resp = requests.post(url, headers=headers, json=json_body)
        if resp.status_code != 200:
            print("OpenRouter TTS error:", resp.status_code, resp.text)
            return b""
        return resp.content          # MP3 bytes
    except Exception as e:
        print("OpenRouter TTS HTTP error:", e)
        return b""


# Sentence-based trigger
def sentence_complete(s: str) -> bool:
    s = s.strip()
    return s.endswith((".", "!", "?"))


# ---------------- STREAM ENDPOINT ----------------
@app.route("/stream", methods=["POST"])
def stream():
    audio_bytes = request.data

    try:
        user_text = whisper_stt(audio_bytes)
    except Exception as e:
        print("STT ERROR:", e)
        user_text = ""

    user_text = user_text.strip() or "Hello?"

    def event_stream():
        yield f"data: TEXT::*User*: {user_text}\n\n"

        try:
            stream_resp = client.chat.completions.create(
                model="openai/gpt-oss-20b",
                stream=True,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Give short, clear answers (200–300 words max). "
                            "Explain in detail ONLY if the user asks."
                        )
                    },
                    {"role": "user", "content": user_text},
                ],
            )
        except Exception as e:
            print("AI ERROR:", e)
            yield "data: TEXT::Sorry, an AI error occurred.\n\n"
            yield "data: DONE\n\n"
            return

        sentence_buffer = ""

        for chunk in stream_resp:
            token = ""
            try:
                token = chunk.choices[0].delta.content or ""
            except:
                pass

            if not token:
                continue

            yield f"data: TEXT::{token}\n\n"
            sentence_buffer += token

            if sentence_complete(sentence_buffer):
                try:
                    audio = make_tts_bytes(sentence_buffer)
                    sentence_buffer = ""
                    if audio:
                        b64 = base64.b64encode(audio).decode()
                        yield f"data: AUDIO::{b64}\n\n"
                except Exception as e:
                    print("TTS ERROR:", e)

        if sentence_buffer.strip():
            audio = make_tts_bytes(sentence_buffer)
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
