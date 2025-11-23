const recBtn = document.getElementById("recBtn");
const stopBtn = document.getElementById("stopBtn");
const statusText = document.getElementById("status");
const chatWindow = document.getElementById("chatWindow");

let mediaRecorder;
let chunks = [];
let reader = null;
let stopSignal = false;
let audioQueue = [];
window.isSpeaking = false;

recBtn.onclick = async () => (!window.recording ? startRecording() : stopRecording());
stopBtn.onclick = stopEverything;

async function startRecording() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  mediaRecorder = new MediaRecorder(stream);
  chunks = [];
  mediaRecorder.ondataavailable = e => chunks.push(e.data);
  mediaRecorder.onstop = sendAudio;
  mediaRecorder.start();
  window.recording = true;

  recBtn.classList.add("recording");
  statusText.innerText = "Listening…";
  stopSignal = false;
  stopBtn.style.display = "none";

  fetch("/stop", { method: "POST" });
}

function stopRecording() {
  mediaRecorder.stop();
  window.recording = false;
  recBtn.classList.remove("recording");
  statusText.innerText = "Processing…";
}

function stopEverything() {
  stopSignal = true;
  fetch("/stop", { method: "POST" });

  if (reader?.cancel) reader.cancel();
  audioQueue = [];
  window.isSpeaking = false;

  if (window.currentAudio) window.currentAudio.pause();
  stopBtn.style.display = "none";
  statusText.innerText = "Stopped";
}

function addBubble(text, sender = "ai") {
  const div = document.createElement("div");
  div.className = `bubble ${sender}`;
  div.innerText = text;
  chatWindow.appendChild(div);
  chatWindow.scrollTop = chatWindow.scrollHeight;
}

function sendAudio() {
  addBubble("🎤 Processing voice…", "user");
  const blob = new Blob(chunks, { type: "audio/webm" });

  fetch("/stream", { method: "POST", body: blob }).then(async res => {
    reader = res.body.getReader();
    addBubble("", "ai");
    let aiBubble = chatWindow.lastChild;
    stopBtn.style.display = "block";
    audioQueue = [];

    const decoder = new TextDecoder();

    while (true) {
      if (stopSignal) return;
      const { done, value } = await reader.read();
      if (done) break;

      const text = decoder.decode(value);
      const lines = text.split("\n\n");

      for (let l of lines) {
        if (!l.startsWith("data:")) continue;
        const payload = l.replace("data:", "").trim();

        if (payload.startsWith("TEXT::")) {
          aiBubble.innerText += payload.replace("TEXT::", "");
          chatWindow.scrollTop = chatWindow.scrollHeight;
        }

        if (payload.startsWith("AUDIO::") && !stopSignal) {
          const url = "data:audio/mp3;base64," + payload.replace("AUDIO::", "");
          audioQueue.push(url);
          playQueue();
        }
      }
    }
  });
}

function playQueue() {
  if (stopSignal || window.isSpeaking || !audioQueue.length) return;

  window.isSpeaking = true;
  const url = audioQueue.shift();
  const audio = new Audio(url);
  window.currentAudio = audio;

  audio.play();
  audio.onended = () => {
    window.isSpeaking = false;
    playQueue();
  };
}
