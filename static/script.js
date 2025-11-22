// minimal safe version - keeps your flow, fixes small bugs
const recBtn = document.getElementById("recBtn");
const stopBtn = document.getElementById("stopBtn");
const statusText = document.getElementById("status");
const chatWindow = document.getElementById("chatWindow");

let mediaRecorder;
let recording = false;
let chunks = [];
let reader = null;
let audioQueue = [];
let stopSignal = false;

recBtn.onclick = async () => {
  if (!recording) await startRecording();
  else stopRecording();
};

stopBtn.onclick = () => {
  stopSignal = true;

  // cancel streaming read if active
  if (reader && reader.cancel) {
    try { reader.cancel(); } catch (e) {}
  }

  // stop audio playback
  audioQueue.forEach(a => {
    try { a.pause(); } catch (e) {}
  });
  audioQueue = [];

  // stop avatar animation safely
  const av = document.getElementById("avatar");
  if (av) av.classList.remove("talking");

  statusText.innerText = "Stopped.";
  stopBtn.style.display = "none";
};

async function startRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
  } catch (e) {
    alert("Microphone blocked or unavailable.");
    return;
  }

  chunks = [];
  mediaRecorder.ondataavailable = e => chunks.push(e.data);
  mediaRecorder.onstop = sendAudio;

  mediaRecorder.start();
  recording = true;

  // visual updates
  recBtn.classList.add("recording");
  statusText.innerText = "Listening...";
  const wf = document.getElementById("waveform");
  if (wf) wf.style.display = "flex";
  stopSignal = false;
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    mediaRecorder.stop();
  }
  recording = false;

  recBtn.classList.remove("recording");
  const wf = document.getElementById("waveform");
  if (wf) wf.style.display = "none";
  statusText.innerText = "Processing...";
}

function addBubble(text, sender = "ai") {
  const div = document.createElement("div");
  div.className = `bubble ${sender}`;
  div.innerText = text;
  chatWindow.appendChild(div);
  chatWindow.scrollTop = chatWindow.scrollHeight;
}

function sendAudio() {
  const blob = new Blob(chunks, { type: "audio/webm" });

  // show user bubble
  addBubble("🎤 Processing voice...", "user");

  fetch("/stream", { method: "POST", body: blob }).then(response => {
    if (!response.body) {
      statusText.innerText = "No response body";
      return;
    }

    reader = response.body.getReader();
    const decoder = new TextDecoder();
    audioQueue = [];

    // placeholder AI bubble
    addBubble("", "ai");
    let aiBubble = chatWindow.lastChild;

    stopBtn.style.display = "block";
    statusText.innerText = "AI responding...";

    function read() {
      if (stopSignal) return;

      reader.read().then(({ done, value }) => {
        if (done) {
          stopBtn.style.display = "none";
          statusText.innerText = "Idle";
          const avEnd = document.getElementById("avatar");
          if (avEnd) avEnd.classList.remove("talking");
          return;
        }

        const text = decoder.decode(value, { stream: true });
        // SSE can send partial chunks; split by double-newline which delimits messages
        const events = text.split("\n\n");
        events.forEach(line => {
          if (!line) return;
          if (!line.startsWith("data:")) return;
          const payload = line.replace("data:", "").trim();

          // TEXT token
          if (payload.startsWith("TEXT::")) {
            const token = payload.replace("TEXT::", "");
            aiBubble.innerText += token;
            chatWindow.scrollTop = chatWindow.scrollHeight;
          }

          // AUDIO chunk
          if (payload.startsWith("AUDIO::") && !stopSignal) {
            // ensure avatar exists and animate
            const av = document.getElementById("avatar");
            if (av) av.classList.add("talking");

            const b64 = payload.replace("AUDIO::", "");
            const audio = new Audio("data:audio/mp3;base64," + b64);
            audioQueue.push(audio);
            if (audioQueue.length === 1) playQueue();
          }

          // DONE marker (optional)
          if (payload === "DONE") {
            stopBtn.style.display = "none";
            statusText.innerText = "Idle";
          }
        });

        // continue reading
        read();
      }).catch(err => {
        // silent fail but show status
        console.warn("Stream read error:", err);
        stopBtn.style.display = "none";
        statusText.innerText = "Stream error";
        const av = document.getElementById("avatar");
        if (av) av.classList.remove("talking");
      });
    }

    read();
  }).catch(err => {
    console.error("Fetch /stream error:", err);
    statusText.innerText = "Upload error";
    stopBtn.style.display = "none";
  });
}

function playQueue() {
  if (!audioQueue.length || stopSignal) {
    const av = document.getElementById("avatar");
    if (av) av.classList.remove("talking");
    return;
  }

  const a = audioQueue[0];
  a.play().catch(e => {
    // ignore play error (autoplay policy), update UI
    console.warn("Audio play error:", e);
  });

  a.onended = () => {
    audioQueue.shift();
    if (!audioQueue.length) {
      const av = document.getElementById("avatar");
      if (av) av.classList.remove("talking");
    }
    // continue playing next
    playQueue();
  };
}
