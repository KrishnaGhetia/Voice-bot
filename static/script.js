// ---- DOM elements ----
const recBtn = document.getElementById("recBtn");
const stopBtn = document.getElementById("stopBtn");
const statusText = document.getElementById("status");
const chatWindow = document.getElementById("chatWindow");

let mediaRecorder;
let recording = false;
let chunks = [];
let reader = null;
let stopSignal = false;
let audioQueue = [];
window.isSpeaking = false; // prevent overlap

// ---- Start / Stop Recording ----
recBtn.onclick = async () => (!recording ? startRecording() : stopRecording());
stopBtn.onclick = stopEverything;

async function startRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
  } catch {
    alert("Microphone blocked");
    return;
  }

  chunks = [];
  mediaRecorder.ondataavailable = e => chunks.push(e.data);
  mediaRecorder.onstop = sendAudio;
  mediaRecorder.start();
  recording = true;

  recBtn.classList.add("recording");
  statusText.innerText = "Listening…";
  document.getElementById("waveform")?.style.setProperty("display", "flex");

  stopSignal = false;
  stopBtn.style.display = "none";
}

// stop recording only — does NOT stop AI
function stopRecording() {
  if (mediaRecorder?.state !== "inactive") mediaRecorder.stop();
  recording = false;
  recBtn.classList.remove("recording");
  document.getElementById("waveform")?.style.setProperty("display", "none");
  statusText.innerText = "Processing…";
}

// ---- STOP EVERYTHING (AI + speech) ----
function stopEverything() {
  stopSignal = true;

  if (reader?.cancel) reader.cancel();
  audioQueue = [];
  window.isSpeaking = false;

  if (window.currentAudio) {
    window.currentAudio.pause();
  }

  document.getElementById("avatar")?.classList.remove("talking");
  stopBtn.style.display = "none";
  statusText.innerText = "Stopped";
}

// ---- Add chat bubbles ----
function addBubble(text, sender = "ai") {
  const div = document.createElement("div");
  div.className = `bubble ${sender}`;
  div.innerText = text;
  chatWindow.appendChild(div);
  chatWindow.scrollTop = chatWindow.scrollHeight;
}

// ---- Send audio to backend ----
function sendAudio() {
  const blob = new Blob(chunks, { type: "audio/webm" });
  addBubble("🎤 Processing voice…", "user");

  fetch("/stream", { method: "POST", body: blob }).then(res => {
    if (!res.body) return;

    reader = res.body.getReader();
    const decoder = new TextDecoder();

    addBubble("", "ai");
    let aiBubble = chatWindow.lastChild;

    audioQueue = [];
    stopBtn.style.display = "block";
    statusText.innerText = "AI responding…";

    function read() {
      if (stopSignal) return;

      reader.read().then(({ done, value }) => {
        if (done) {
          stopBtn.style.display = "none";
          statusText.innerText = "Idle";
          document.getElementById("avatar")?.classList.remove("talking");
          return;
        }

        const text = decoder.decode(value, { stream: true });
        const events = text.split("\n\n");

        events.forEach(line => {
          if (!line.startsWith("data:")) return;
          const payload = line.replace("data:", "").trim();

          // ---- Text token ----
          if (payload.startsWith("TEXT::")) {
            const token = payload.replace("TEXT::", "");
            aiBubble.innerText += token;
            chatWindow.scrollTop = chatWindow.scrollHeight;
          }

          // ---- Audio chunk ----
          if (payload.startsWith("AUDIO::") && !stopSignal) {
            const b64 = payload.replace("AUDIO::", "");
            const url = `data:audio/mp3;base64,${b64}`;
            audioQueue.push(url);
            playQueue();
          }

          if (payload === "DONE") {
            stopBtn.style.display = "none";
            statusText.innerText = "Idle";
          }
        });

        read();
      }).catch(() => stopEverything());
    }

    read();
  });
}

// ---- Queue-safe Audio playback ----
function playQueue() {
  if (stopSignal) return;
  if (window.isSpeaking) return;
  if (!audioQueue.length) {
    document.getElementById("avatar")?.classList.remove("talking");
    return;
  }

  window.isSpeaking = true;
  const url = audioQueue.shift();
  const audio = new Audio(url);
  window.currentAudio = audio;

  document.getElementById("avatar")?.classList.add("talking");

  audio.play().catch(() => {});

  audio.onended = () => {
    window.isSpeaking = false;
    playQueue(); // play next only after previous ended
  };

  audio.onerror = () => {
    window.isSpeaking = false;
    playQueue();
  };
}
