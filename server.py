import os
import time
import base64
import tempfile
import requests
from dotenv import load_dotenv
from flask import Flask, render_template, request, Response
from openai import OpenAI
from gtts import gTTS

# ---------------- ENV & APP INIT ----------------
load_dotenv()

app = Flask(__name__, static_folder="static", template_folder="templates")

# ----- OpenRouter client -----
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY"),
    default_headers={
        "HTTP-Referer": "https://voice-bot-lr4b.onrender.com",
        "X-Title": "Voice AI Bot"
    }
)


# ----- AssemblyAI key -----
ASSEMBLYAI_KEY = os.getenv("ASSEMBLYAI_API_KEY")
if not ASSEMBLYAI_KEY:
    raise RuntimeError("ASSEMBLYAI_API_KEY is missing in environment variables")


# ---------------- STT via AssemblyAI ----------------
def whisper_stt(audio_bytes: bytes) -> str:
    """
    Speech-to-text using AssemblyAI.
    Uploads raw WebM bytes, creates a transcript job,
    polls until completion, then returns text.
    """
    headers = {"authorization": ASSEMBLYAI_KEY}

    # 1) Upload audio
    upload_url = "https://api.assemblyai.com/v2/upload"
    try:
        upload_res = requests.post(upload_url, headers=headers, data=audio_bytes)
        upload_res.raise_for_status()
    except Exception as e:
        print("AssemblyAI upload error:", e, "Response:", getattr(upload_res, "text", None))
        raise

    audio_url = upload_res.json().get("upload_url")
    if not audio_url:
        raise RuntimeError(f"AssemblyAI upload_url missing in response: {upload_res.text}")

    # 2) Create transcript
    transcript_url = "https://api.assemblyai.com/v2/transcript"
    try:
        transcript_res = requests.post(
            transcript_url,
            headers=headers,
            json={"audio_url": audio_url},
        )
        transcript_res.raise_for_status()
    except Exception as e:
        print("AssemblyAI transcript create error:", e, "Response:", getattr(transcript_res, "text", None))
        raise

    transcript_id = transcript_res.json().get("id")
    if not transcript_id:
        raise RuntimeError(f"AssemblyAI transcript id missing: {transcript_res.text}")

    # 3) Poll status
    poll_url = f"{transcript_url}/{transcript_id}"
    while True:
        poll_res = requests.get(poll_url, headers=headers)
        try:
            poll_res.raise_for_status()
        except Exception as e:
            print("AssemblyAI poll error:", e, "Response:", poll_res.text)
            raise

        data = poll_res.json()
        status = data.get("status")
        if status == "completed":
            return data.get("text", "")
        if status == "error":
            raise RuntimeError(f"AssemblyAI error: {data.get('error')}")
        # still processing
        time.sleep(1.0)


# ---------------- TTS via gTTS ----------------
def make_tts_bytes(text: str) -> bytes:
    """
    Convert text to MP3 bytes using gTTS.
    """
    if not text.strip():
        return b""

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        path = tmp.name

    try:
        tts = gTTS(text=text, lang="en")
        tts.save(path)
        with open(path, "rb") as f:
            data = f.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    return data


def should_emit_audio(buffer_text: str, last_token: str, char_threshold: int = 120) -> bool:
    """
    Decide when to send a TTS chunk:
    - when buffered text is long enough
    - or when the last token ends with sentence punctuation.
    """
    if len(buffer_text) >= char_threshold:
        return True
    last_token = last_token.strip()
    if last_token and last_token[-1] in ".!?":
        return True
    return False


# ---------------- STREAM ENDPOINT ----------------
@app.route("/stream", methods=["POST"])
def stream():
    audio_bytes = request.data

    try:
        user_text = whisper_stt(audio_bytes)
    except Exception as e:
        print("STT error:", e)
        user_text = ""

    print("User said:", repr(user_text))

    if not user_text.strip():
        user_text = "Hello?"

    def event_stream():
        # Send user message as first event
        yield f"data: TEXT::*User*: {user_text}\n\n"

        try:
            stream_resp = client.chat.completions.create(
                model="openrouter/gpt-oss-20b",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Provide short, concise answers (max 200-300 words). "
                            "Do NOT write long essays unless the user clearly asks for a detailed explanation. "
                            "If the user wants more detail, they will ask for it explicitly. "
                            "Focus on clarity and summarizing first."
                        ),
                    },
                    {
                        "role": "user",
                        "content": user_text,
                    },
                ],
                stream=True,
            )
        except Exception as e:
            print("OpenRouter chat error:", e)
            yield "data: TEXT::Sorry, I had an error talking to the AI model.\n\n"
            yield "data: DONE\n\n"
            return

        buffer_for_tts = ""
        last_token = ""

        # Stream tokens + periodic audio
        for chunk in stream_resp:
            try:
                delta = chunk.choices[0].delta
                token = delta.content or ""
            except Exception:
                token = ""

            if not token:
                continue

            last_token = token
            # text stream
            yield f"data: TEXT::{token}\n\n"
            buffer_for_tts += token

            # audio stream (chunked)
            if should_emit_audio(buffer_for_tts, last_token):
                try:
                    tts_bytes = make_tts_bytes(buffer_for_tts)
                    if tts_bytes:
                        b64 = base64.b64encode(tts_bytes).decode()
                        yield f"data: AUDIO::{b64}\n\n"
                    buffer_for_tts = ""
                except Exception as e:
                    print("TTS error:", e)

        # flush remaining buffer as final audio
        if buffer_for_tts.strip():
            try:
                tts_bytes = make_tts_bytes(buffer_for_tts)
                if tts_bytes:
                    b64 = base64.b64encode(tts_bytes).decode()
                    yield f"data: AUDIO::{b64}\n\n"
            except Exception as e:
                print("Final TTS error:", e)

        yield "data: DONE\n\n"

    return Response(event_stream(), mimetype="text/event-stream")


# ---------------- ROOT ROUTE ----------------
@app.route("/")
def index():
    return render_template("index.html")


# ---------------- ENTRYPOINT ----------------
if __name__ == "__main__":
    # Works locally and on Render/other PaaS
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
