"use strict";
const startupTiming = (() => {
  const source = window.framelinkTiming || {};
  const marks = source.marks || {
    t0_page_opened: null,
    t1_permission_granted: null,
    t2_get_user_media_completed: null,
    t3_sdp_offer_created: null,
    t4_sdp_answer_received: null,
    t5_ice_connected: null,
  };
  const availability = source.availability || {permission_observation: "unavailable"};
  const now = () => performance.now();
  source.clock = "performance.now";
  source.marks = marks;
  source.availability = availability;
  source.mark = name => {
    if (marks[name] === null || marks[name] === undefined) marks[name] = now();
    return marks[name];
  };
  source.markLatest = name => { marks[name] = now(); return marks[name]; };
  source.snapshot = () => ({
    clock: source.clock,
    marks: {...marks},
    availability: {...availability},
  });
  window.framelinkTiming = source;
  source.mark("t0_page_opened");
  return source;
})();

async function observeCameraPermission(permissions = navigator.permissions, timeoutMs = 250) {
  if (!permissions?.query) return {state: null, availability: "unavailable"};
  let timer;
  try {
    const result = await Promise.race([
      permissions.query({name: "camera"}),
      new Promise(resolve => { timer = setTimeout(() => resolve(null), timeoutMs); }),
    ]);
    if (!result) return {state: null, availability: "unavailable-timeout"};
    return {state: result.state || null, availability: "supported", permission: result};
  } catch (_) {
    return {state: null, availability: "unavailable"};
  } finally {
    clearTimeout(timer);
  }
}
window.__observeCameraPermission = observeCameraPermission;
const $ = id => document.getElementById(id);
let token = location.hash.slice(1);
if (token) {
  try { sessionStorage.setItem("framelink_token", token); } catch (_) {}
  history.replaceState(null, "", "/");
} else {
  try { token = sessionStorage.getItem("framelink_token"); } catch (_) {}
}

let session = null;
try { session = sessionStorage.getItem("framelink_session"); } catch (_) {}
let pc = null, stream = null, wakeLock = null;
let stopped = true, busy = false, heartbeat = 0, retries = 0, reconnectTimer = null;
let lifecycle = 0;
let currentFacing = "environment";
let torchActive = false;
let quality = null;
let audioMuted = false;
let cameraPermission = null;

function message(text) {
  const el = $("message");
  if (el) el.textContent = text;
}

function status(text, badgeClass = "") {
  const el = $("status");
  if (el) {
    el.textContent = text;
    el.className = "badge " + badgeClass;
  }
}

function updateIndicator(text, stateClass = "") {
  const el = $("indicator");
  if (el) {
    el.textContent = text;
    el.className = stateClass;
  }
}

function parseIceCandidateLines(sdp) {
  return String(sdp || "").split(/\r?\n/).flatMap(line => {
    const match = line.match(/^a=candidate:\S+\s+\d+\s+(\S+)\s+\d+\s+(\S+)\s+(\d+)\s+typ\s+(\S+)/);
    return match ? [{address: match[2], port: Number(match[3]), protocol: match[1].toLowerCase(), type: match[4]}] : [];
  });
}

window.framelinkBuildIceDiagnostics = (localSdp, remoteSdp) => ({
  local_candidates: parseIceCandidateLines(localSdp),
  remote_candidates: parseIceCandidateLines(remoteSdp),
});

async function sendIceDiagnostics(peer, channel) {
  if (!peer || !channel || channel.readyState !== "open") return;
  try {
    const stats = await peer.getStats();
    const values = Array.from(stats.values());
    const pair = values.find(s => s.type === "candidate-pair" && (s.selected || s.nominated)) || {};
    const transport = values.find(s => s.type === "transport") || {};
    const dtls = values.find(s => s.type === "candidate-pair" && s.dtlsState) || transport;
    const diagnostics = window.framelinkBuildIceDiagnostics(
      peer.localDescription?.sdp,
      peer.remoteDescription?.sdp,
    );
    diagnostics.ice_gathering_state = peer.iceGatheringState;
    diagnostics.ice_connection_state = peer.iceConnectionState;
    diagnostics.peer_connection_state = peer.connectionState;
    diagnostics.dtls_state = dtls.dtlsState || "unknown";
    const localCandidate = values.find(s => s.id === pair.localCandidateId);
    const remoteCandidate = values.find(s => s.id === pair.remoteCandidateId);
    diagnostics.selected_pair = pair.localCandidateId ? {
      local_candidate_id: pair.localCandidateId,
      remote_candidate_id: pair.remoteCandidateId,
      local: localCandidate ? {
        address: localCandidate.address || "",
        port: localCandidate.port || 0,
        protocol: localCandidate.protocol || "",
        type: localCandidate.candidateType || "",
      } : null,
      remote: remoteCandidate ? {
        address: remoteCandidate.address || "",
        port: remoteCandidate.port || 0,
        protocol: remoteCandidate.protocol || "",
        type: remoteCandidate.candidateType || "",
      } : null,
      current_round_trip_time: pair.currentRoundTripTime || 0,
      state: pair.state || "",
    } : null;
    channel.send(JSON.stringify({type: "ice-diagnostics", ...diagnostics}));
  } catch (_) {}
}

window.addEventListener("hashchange", () => {
  if (location.hash && stopped) {
    token = location.hash.slice(1);
    history.replaceState(null, "", "/");
    message("New pairing code received. Tap Connect to continue.");
    connect();
  }
});

async function post(path, body, credential = session) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-FrameLink-Session": credential || "",
        "X-Luma-Session": credential || ""
      },
      body: JSON.stringify(body),
      signal: controller.signal,
      cache: "no-store",
      credentials: "omit"
    });
    if (!response.ok) throw new Error(await response.text());
    return await response.json();
  } finally {
    clearTimeout(timeout);
  }
}

function getCameraConstraints() {
  return {
    video: {
      facingMode: { ideal: currentFacing },
      width: { ideal: 1920 },
      height: { ideal: 1080 },
      frameRate: { ideal: 30, max: 30 }
    },
    audio: !audioMuted ? { echoCancellation: true, noiseSuppression: true } : false
  };
}

async function acquireCamera() {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    throw new Error("Camera requires trusted HTTPS. Complete desktop certificate setup and reopen.");
  }
  const epoch = lifecycle;
  const acquired = await navigator.mediaDevices.getUserMedia(getCameraConstraints());
  if (cameraPermission?.state === "granted") startupTiming.mark("t1_permission_granted");
  startupTiming.mark("t2_get_user_media_completed");
  if (stopped || epoch !== lifecycle) {
    acquired.getTracks().forEach(t => t.stop());
    throw new Error("Camera cancelled.");
  }
  stream = acquired;
  const videoEl = $("preview");
  if (videoEl) {
    videoEl.srcObject = stream;
    await videoEl.play().catch(() => {});
  }
  const vTrack = stream.getVideoTracks()[0];
  const capabilities = vTrack.getCapabilities ? vTrack.getCapabilities() : {};
  if (capabilities.width && capabilities.height) {
    try {
      await vTrack.applyConstraints({width: {ideal: Math.min(1920, capabilities.width.max)},
        height: {ideal: Math.min(1080, capabilities.height.max)},
        frameRate: {ideal: Math.min(30, capabilities.frameRate?.max || 30), max: 30}});
    } catch (_) { /* Keep the successfully acquired mode if a combination is unavailable. */ }
  }
  if (stopped || epoch !== lifecycle) {
    acquired.getTracks().forEach(t => t.stop());
    throw new Error("Camera cancelled.");
  }
  const settings = vTrack.getSettings();
  console.info("FrameLink camera capture", {width: settings.width, height: settings.height, frameRate: settings.frameRate});
  if (settings.facingMode) currentFacing = settings.facingMode;
  if (videoEl) {
    videoEl.style.transform = currentFacing === "user" ? "scaleX(-1)" : "none";
  }
  const actualEl = $("actual");
  if (actualEl) {
    actualEl.textContent = `${settings.width || "?"} × ${settings.height || "?"} · ${Math.round(settings.frameRate || 30)} FPS`;
  }
  updateIndicator("CAMERA LIVE", "active");

  // Check torch capability
  checkTorchSupport(vTrack);

  vTrack.onended = () => {
    if (!stopped) {
      message("Camera access ended. Reconnecting…");
      scheduleReconnect();
    }
  };

  if ("wakeLock" in navigator) {
    try { wakeLock = await navigator.wakeLock.request("screen"); } catch (_) {}
  }
}

function checkTorchSupport(track) {
  const torchBtn = $("torch-btn");
  if (!torchBtn) return;
  const caps = track.getCapabilities ? track.getCapabilities() : {};
  if (caps.torch) {
    torchBtn.style.display = "inline-flex";
  } else {
    torchBtn.style.display = "none";
  }
}

async function toggleTorch() {
  if (!stream) return;
  const track = stream.getVideoTracks()[0];
  if (!track) return;
  try {
    torchActive = !torchActive;
    await track.applyConstraints({ advanced: [{ torch: torchActive }] });
    const torchBtn = $("torch-btn");
    if (torchBtn) torchBtn.classList.toggle("active", torchActive);
  } catch (err) {
    torchActive = false;
  }
}

async function toggleMute() {
  audioMuted = !audioMuted;
  const muteBtn = $("mute-btn");
  if (muteBtn) muteBtn.classList.toggle("active", audioMuted);
  if (stream) {
    stream.getAudioTracks().forEach(t => t.enabled = !audioMuted);
  }
}

async function negotiate() {
  if (stopped || !stream) return;
  if (quality) { quality.stop(); quality = null; }
  if (pc) { pc.onconnectionstatechange = null; pc.close(); }

  status("Connecting…", "connecting");
  const peer = new RTCPeerConnection({ iceServers: [], bundlePolicy: "max-bundle" });
  pc = peer;

  stream.getTracks().forEach(t => peer.addTrack(t, stream));
  const videoTransceiver = peer.getTransceivers().find(t => t.sender.track?.kind === "video");
  const codecs = RTCRtpSender.getCapabilities?.("video")?.codecs || [];
  if (videoTransceiver?.setCodecPreferences && codecs.some(c => c.mimeType.toLowerCase() === "video/h264")) {
    videoTransceiver.setCodecPreferences([
      ...codecs.filter(c => c.mimeType.toLowerCase() === "video/h264"),
      ...codecs.filter(c => c.mimeType.toLowerCase() !== "video/h264")
    ]);
  }

  const channel = peer.createDataChannel("framelink-status");
  window.framelinkRecording?.setChannel(channel);
  channel.onopen = () => {
    window.framelinkRecording?.setChannel(channel);
    sendIceDiagnostics(peer, channel);
  };
  channel.onclose = () => {
    if (window.framelinkRecording?.channel === channel) window.framelinkRecording.setChannel(null);
  };
  channel.onmessage = event => {
    try { window.framelinkHandleServerMessage(JSON.parse(event.data)); } catch (_) {}
  };

  peer.onconnectionstatechange = () => {
    if (peer !== pc || stopped) return;
    if (peer.connectionState === "connected") {
      retries = 0;
      heartbeat = Date.now();
      status("Connected · awaiting video", "connecting");
      updateIndicator("CONNECTED · WAITING FOR VIDEO", "connecting");
      message("WebRTC connected. Waiting for the first video frame…");
      sendIceDiagnostics(peer, channel);
    } else if (peer.connectionState === "connecting") {
      status("Connecting…", "connecting");
    } else if (["failed", "disconnected"].includes(peer.connectionState)) {
      window.framelinkRecording?.setChannel(null);
      status("Reconnecting…", "connecting");
      scheduleReconnect();
    }
  };

  peer.oniceconnectionstatechange = () => {
    if (peer !== pc || stopped) return;
    if (["connected", "completed"].includes(peer.iceConnectionState)) {
      startupTiming.mark("t5_ice_connected");
    } else if (peer.iceConnectionState === "failed") {
      message("Local WebRTC UDP connectivity failed. Check the hotspot interface and firewall; no internet service is required.");
    }
    sendIceDiagnostics(peer, channel);
  };

  const offer = await peer.createOffer();
  startupTiming.markLatest("t3_sdp_offer_created");
  await peer.setLocalDescription(offer);

  // Wait for ICE candidates with fast fallback
  if (peer.iceGatheringState !== "complete") {
    await new Promise(resolve => {
      let resolved = false;
      const done = () => {
        if (!resolved) {
          resolved = true;
          peer.removeEventListener("icegatheringstatechange", checkState);
          peer.removeEventListener("icecandidate", checkCandidate);
          resolve();
        }
      };
      const checkState = () => { if (peer.iceGatheringState === "complete") done(); };
      const checkCandidate = e => { if (!e.candidate) done(); };
      peer.addEventListener("icegatheringstatechange", checkState);
      peer.addEventListener("icecandidate", checkCandidate);
      setTimeout(done, 3000);
      checkState();
    });
  }

  if (stopped || peer !== pc) return;
  const answer = await post("/offer", {
    type: peer.localDescription.type,
    sdp: peer.localDescription.sdp
  });
  startupTiming.markLatest("t4_sdp_answer_received");

  if (stopped || peer !== pc) return;
  // Chromium reads this optional startup hint from the remote description.
  // The local offer does not configure its sending bitrate.
  // Safari may ignore it; congestion control and maxBitrate remain authoritative.
  const actual = stream.getVideoTracks()[0].getSettings();
  const initialKbps = Math.round(Math.min(8000, Math.max(1000, actual.width * actual.height * (actual.frameRate || 30) * .10 / 1000)));
  const videoCodecs = [...answer.sdp.matchAll(/^a=rtpmap:(\d+) (?:H264|VP8)\/90000/gm)].map(m => m[1]);
  for (const payload of videoCodecs) {
    const pattern = new RegExp(`(a=fmtp:${payload} [^\\r\\n]*)`);
    if (pattern.test(answer.sdp)) answer.sdp = answer.sdp.replace(pattern, `$1;x-google-start-bitrate=${initialKbps}`);
    else answer.sdp = answer.sdp.replace(new RegExp(`(a=rtpmap:${payload} [^\\r\\n]*\\r\\n)`), `$1a=fmtp:${payload} x-google-start-bitrate=${initialKbps}\r\n`);
  }
  await peer.setRemoteDescription(answer);
  sendIceDiagnostics(peer, channel);
  const sender = peer.getSenders().find(s => s.track?.kind === "video");
  if (sender && typeof CameraQuality !== "undefined") {
    quality = new CameraQuality(sender.track, sender, peer, channel);
    await quality.start();
  }
}

function scheduleReconnect() {
  if (stopped || reconnectTimer || busy) return;
  if (retries < 6) retries++;
  if (retries >= 6) {
    status("Waiting to reconnect", "connecting");
    message("Connection lost. Retrying with the existing pairing session…");
    retries = 6;
  }
  if (retries < 6) message(`Connection interrupted. Reconnecting (${retries}/6)…`);
  reconnectTimer = setTimeout(async () => {
    reconnectTimer = null;
    busy = true;
    let failed = false;
    try {
      await negotiate();
    } catch (e) {
      failed = true;
    } finally {
      busy = false;
    }
    if (failed) scheduleReconnect();
  }, Math.min(1000 * 2 ** (retries - 1), 6000));
}

window.framelinkHandleServerMessage = data => {
  heartbeat = Date.now();
  window.framelinkRecording?.receive(data);
  if (quality) quality.feedback(data);
  if (data.disconnect) {
    disconnect();
    message("Disconnected by desktop.");
    return;
  }
  const isRec = data.recording === "Recording";
  const ready = data.status === "video-received" || data.status === "preview-ready";
  const isLive = ready || isRec;
  updateIndicator(isRec ? "RECORDING ON DESKTOP" : isLive ? "LIVE TO STUDIO" : "CONNECTED · WAITING FOR VIDEO", isRec ? "recording" : isLive ? "active" : "connecting");
  status(isRec ? "Recording active" : isLive ? "Live to Studio" : "Connected · awaiting video", isRec ? "recording" : isLive ? "connected" : "connecting");
  if (ready) message("Connected! Streaming video directly to FrameLink Studio.");
};

async function connect() {
  if (busy) return;
  busy = true;
  const connectBtn = $("connect-btn");
  if (connectBtn) connectBtn.disabled = true;

  try {
    if (!session) {
      if (!token) throw new Error("Scan a pairing QR code from FrameLink on your desktop.");
      status("Pairing…", "connecting");
      session = (await post("/pair", { token })).session;
      token = null;
    }
    stopped = false;
    heartbeat = Date.now();
    retries = 0;
    const permission = await observeCameraPermission();
    cameraPermission = permission.permission || null;
    startupTiming.availability.permission_observation = permission.availability;
    if (cameraPermission) {
      const markPermission = () => {
        if (cameraPermission.state === "granted") startupTiming.mark("t1_permission_granted");
      };
      cameraPermission.onchange = markPermission;
      markPermission();
    }
    message("Opening camera…");
    await acquireCamera();
    message("Negotiating connection with desktop…");
    await negotiate();

    if ($("controls")) $("controls").style.display = "flex";
    if (connectBtn) connectBtn.style.display = "none";
  } catch (error) {
    await disconnect();
    message(error.name === "NotAllowedError"
      ? "Camera permission denied. Allow camera in browser settings and scan a new code."
      : error.message);
  } finally {
    busy = false;
    if (connectBtn) connectBtn.disabled = !stopped;
  }
}

async function disconnect() {
  stopped = true;
  if (quality) { quality.stop(); quality = null; }
  lifecycle++;
  clearTimeout(reconnectTimer);
  reconnectTimer = null;
  const old = session;
  session = null;
  window.framelinkRecording?.setChannel(null);
  try {
    sessionStorage.removeItem("framelink_session");
    sessionStorage.removeItem("framelink_token");
  } catch (_) {}
  if (pc) { pc.onconnectionstatechange = null; pc.close(); pc = null; }
  if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
  const videoEl = $("preview");
  if (videoEl) videoEl.srcObject = null;
  if (wakeLock) { try { await wakeLock.release(); } catch (_) {} wakeLock = null; }

  updateIndicator("CAMERA OFF", "");
  status("Disconnected", "");
  const connectBtn = $("connect-btn");
  if (connectBtn) {
    connectBtn.disabled = false;
    connectBtn.style.display = "block";
  }
  if ($("controls")) $("controls").style.display = "none";

  if (old) {
    try { await post("/disconnect", {}, old); } catch (_) {}
  }
}

async function flipCamera() {
  if (busy || stopped) return;
  busy = true;
  const flipBtn = $("flip-btn");
  if (flipBtn) flipBtn.disabled = true;
  try {
    if (stream) stream.getTracks().forEach(t => { t.onended = null; t.stop(); });
    currentFacing = currentFacing === "user" ? "environment" : "user";
    await acquireCamera();
    await negotiate();
  } catch (err) {
    message(err.message);
  } finally {
    busy = false;
    if (flipBtn) flipBtn.disabled = false;
  }
}

// Bind UI
if ($("connect-btn")) $("connect-btn").onclick = connect;
if ($("disconnect-btn")) $("disconnect-btn").onclick = () => { disconnect(); message("Camera disconnected."); };
if ($("flip-btn")) $("flip-btn").onclick = flipCamera;
if ($("torch-btn")) $("torch-btn").onclick = toggleTorch;
if ($("mute-btn")) $("mute-btn").onclick = toggleMute;

window.addEventListener("pagehide", () => { disconnect(); });
document.addEventListener("visibilitychange", () => {
  if (document.hidden && !stopped) {
    message("The OS may pause camera when browser is backgrounded. Return here to resume.");
  }
  if (!document.hidden && !stopped) {
    if ("wakeLock" in navigator) {
      navigator.wakeLock.request("screen").then(l => wakeLock = l).catch(() => {});
    }
    if (pc?.connectionState !== "connected") scheduleReconnect();
  }
});

setInterval(() => {
  if (!stopped && heartbeat && Date.now() - heartbeat > 20000) {
    disconnect();
    message("Desktop connection timed out. Scan a new QR code to reconnect.");
  }
}, 2500);

// Auto-start on load if token is available
window.addEventListener("DOMContentLoaded", () => {
  if (token) {
    connect();
  }
});
