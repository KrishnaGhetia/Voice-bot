// ---- DOM elements ----
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

// ---- Start / Stop Recording ----
recBtn.onclick = async () => {
  if (!recording) await startRecording();
  else stopRecording();
};

stopBtn.onclick = stopEverything;

async function startRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
  } catch {
    alert("Microphone access denied");
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
  fetch("/stop", { method: "POST" }); // reset stop flag on server
}

function stopRecording() {
  if (mediaRecorder?.state !== "inactive") mediaRecorder.stop();
  recording = false;
  recBtn.classList.remove("recording");
  document.getElementById("waveform")?.style.setProperty("display", "none");
  statusText.innerText = "Processing…";
}

// ---- STOP EVERYTHING (speech + stream + backend) ----
function stopEverything() {
  stopSignal = true;
  fetch("/stop", { method: "POST" }); // notify backend to stop response

  if (reader?.cancel) reader.cancel();
  audioQueue.forEach(a => a.pause?.());
  audioQueue = [];

  document.getElementById("avatar")?.classList.remove("talking");

  stopBtn.style.display = "none";
  statusText.innerText = "Stopped.";
}

// ---- UI Bubble ----
function addBubble(text, sender = "ai") {
  const div = document.createElement("div");
  div.className = `bubble ${sender}`;
  div.innerText = text;
  chatWindow.appendChild(div);
  chatWindow.scrollTop = chatWindow.scrollHeight;
}

// ---- SEND AUDIO TO BACKEND ----
function sendAudio() {
  const blob = new Blob(chunks, { type: "audio/webm" });
  addBubble("🎤 Processing voice…", "user");

  fetch("/stream", { method: "POST", body: blob }).then(response => {
    if (!response.body) return;

    reader = response.body.getReader();
    const decoder = new TextDecoder();
    audioQueue = [];

    addBubble("", "ai");
    let aiBubble = chatWindow.lastChild;

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

        const chunk = decoder.decode(value, { stream: true });
        const events = chunk.split("\n\n");

        events.forEach(line => {
          if (!line.startsWith("data:")) return;
          const payload = line.replace("data:", "").trim();

          // ----- TEXT -----
          if (payload.startsWith("TEXT::")) {
            const token = payload.replace("TEXT::", "");
            aiBubble.innerText += token;
            chatWindow.scrollTop = chatWindow.scrollHeight;
          }

          // ----- AUDIO -----
          if (payload.startsWith("AUDIO::") && !stopSignal) {
            const b64 = payload.replace("AUDIO::", "");
            const url = `data:audio/mp3;base64,${b64}`;
            audioQueue.push(url);

            document.getElementById("avatar")?.classList.add("talking");
            if (audioQueue.length === 1) playQueue(); // start immediately
          }

          // ----- DONE -----
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

// ---- PLAY AUDIO QUEUE ----
function playQueue() {
  if (!audioQueue.length || stopSignal) {
    document.getElementById("avatar")?.classList.remove("talking");
    return;
  }

  const audio = new Audio(audioQueue[0]);
  audio.play().catch(err => console.warn("Autoplay blocked:", err));

  audio.onended = () => {
    audioQueue.shift();
    if (!audioQueue.length)
      document.getElementById("avatar")?.classList.remove("talking");
    playQueue();
  };
}
