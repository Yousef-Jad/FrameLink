"use strict";
const $ = id => document.getElementById(id);
let token = location.hash.slice(1);
history.replaceState(null, "", "/");
let session = null, pc = null, stream = null, wakeLock = null;
let stopped = true, busy = false, heartbeat = 0, retries = 0, reconnectTimer = null;
function message(text) { $("message").textContent = text; }
function status(text) { $("status").textContent = text; }
async function post(path, body) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json", "X-Luma-Session": session || ""}, body: JSON.stringify(body), signal: controller.signal, cache: "no-store", credentials: "omit"});
    if (!response.ok) throw new Error(await response.text());
    return await response.json();
  } finally { clearTimeout(timeout); }
}
function constraints() {
  const width = Number($("resolution").value);
  return {video: {facingMode: {ideal: $("facing").value}, width: {ideal: width}, height: {ideal: width === 640 ? 480 : width * 9 / 16}, frameRate: {ideal: Number($("fps").value), max: Number($("fps").value)}}, audio: $("audio").checked ? {echoCancellation: true, noiseSuppression: true} : false};
}
async function acquireCamera() {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error("Camera access requires trusted HTTPS. Complete the certificate setup shown on the desktop, then reopen the pairing URL.");
  stream = await navigator.mediaDevices.getUserMedia(constraints());
  $("preview").srcObject = stream;
  await $("preview").play();
  const track = stream.getVideoTracks()[0], actual = track.getSettings();
  $("actual").textContent = `Actual camera: ${actual.width || "?"} × ${actual.height || "?"} · ${Math.round(actual.frameRate || 0)} FPS`;
  $("preview").style.transform = actual.facingMode === "user" ? "scaleX(-1)" : "none";
  $("indicator").textContent = "CAMERA ACTIVE · LIVE TO DESKTOP";
  $("indicator").className = "active";
  track.onended = () => { if (!stopped) { message("Camera access ended. Reconnect to resume."); disconnect(); } };
  if ("wakeLock" in navigator) {
    try { wakeLock = await navigator.wakeLock.request("screen"); } catch (_) { /* The OS may deny wake lock. */ }
  }
}
async function negotiate() {
  if (pc) { pc.onconnectionstatechange = null; pc.close(); }
  const peer = new RTCPeerConnection({iceServers: [], bundlePolicy: "max-bundle"});
  pc = peer;
  stream.getTracks().forEach(track => peer.addTrack(track, stream));
  const channel = peer.createDataChannel("luma-status");
  channel.onmessage = event => {
    try {
      const data = JSON.parse(event.data);
      heartbeat = Date.now();
      if (data.disconnect) { disconnect(); message("Disconnected by the desktop."); return; }
      const recording = data.recording === "Recording";
      $("indicator").textContent = recording ? "RECORDING ON DESKTOP" : `CAMERA ACTIVE · ${data.recording || "PREVIEW"}`;
      $("indicator").className = recording ? "recording" : "active";
    } catch (_) { message("Invalid desktop status message."); }
  };
  peer.onconnectionstatechange = () => {
    if (peer !== pc || stopped) return;
    status(peer.connectionState === "connected" ? "Connected securely" : peer.connectionState);
    if (peer.connectionState === "connected") { retries = 0; heartbeat = Date.now(); message("Camera is live on your desktop. Start recording in LumaCapture."); }
    if (["failed", "disconnected"].includes(peer.connectionState)) scheduleReconnect();
  };
  await peer.setLocalDescription(await peer.createOffer());
  if (peer.iceGatheringState !== "complete") await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Local network discovery timed out.")), 12000);
    const changed = () => { if (peer.iceGatheringState === "complete") { clearTimeout(timer); peer.removeEventListener("icegatheringstatechange", changed); resolve(); } };
    peer.addEventListener("icegatheringstatechange", changed); changed();
  });
  const answer = await post("/offer", {type: peer.localDescription.type, sdp: peer.localDescription.sdp});
  if (stopped || peer !== pc) return;
  await peer.setRemoteDescription(answer);
}
function scheduleReconnect() {
  if (stopped || reconnectTimer || busy) return;
  if (++retries > 5) { disconnect(); message("Connection could not be restored. Check Wi-Fi, firewall and hotspot client isolation, then scan a new QR code."); return; }
  message(`Connection interrupted. Reconnecting (${retries}/5)…`);
  reconnectTimer = setTimeout(async () => {
    reconnectTimer = null; busy = true;
    let failed = false;
    try { await negotiate(); } catch (error) { message(error.message); failed = true; }
    finally { busy = false; }
    if (failed) scheduleReconnect();
  }, Math.min(1000 * 2 ** retries, 8000));
}
async function connect() {
  if (busy) return;
  busy = true; $("connect").disabled = true;
  try {
    if (!session) {
      if (!token) throw new Error("Scan a fresh pairing QR code from the desktop.");
      session = (await post("/pair", {token})).session; token = null;
    }
    stopped = false;
    await acquireCamera();
    await negotiate();
    $("switch").disabled = false; $("disconnect").disabled = false;
    ["facing", "resolution", "fps", "audio"].forEach(id => $(id).disabled = true);
  } catch (error) { await disconnect(); message(error.name === "NotAllowedError" ? "Camera permission was denied. Allow camera access in your browser settings and scan a new code." : error.message); }
  finally { busy = false; $("connect").disabled = !stopped; }
}
async function disconnect() {
  stopped = true; clearTimeout(reconnectTimer); reconnectTimer = null;
  if (pc) { pc.onconnectionstatechange = null; pc.close(); pc = null; }
  if (stream) { stream.getTracks().forEach(track => track.stop()); stream = null; }
  $("preview").srcObject = null;
  if (wakeLock) { try { await wakeLock.release(); } catch (_) {} wakeLock = null; }
  $("indicator").textContent = "CAMERA OFF"; $("indicator").className = "";
  status("Disconnected"); $("connect").disabled = false;
  $("switch").disabled = true; $("disconnect").disabled = true;
  ["facing", "resolution", "fps", "audio"].forEach(id => $(id).disabled = false);
  if (session) { const old = session; try { await post("/disconnect", {}); } catch (_) {} if (session === old) session = null; }
}
$("connect").onclick = connect;
$("disconnect").onclick = () => { disconnect(); message("Camera stopped. Scan a new QR code to pair again."); };
$("switch").onclick = async () => {
  if (busy || stopped) return;
  busy = true; $("switch").disabled = true;
  try {
    if (stream) stream.getTracks().forEach(track => { track.onended = null; track.stop(); });
    $("facing").value = $("facing").value === "user" ? "environment" : "user";
    await acquireCamera(); await negotiate();
  } catch (error) { await disconnect(); message(error.message); }
  finally { busy = false; $("switch").disabled = stopped; }
};
window.addEventListener("pagehide", () => { stopped = true; if (stream) stream.getTracks().forEach(t => t.stop()); if (pc) pc.close(); });
document.addEventListener("visibilitychange", () => {
  if (document.hidden && !stopped) message("The operating system may suspend the camera while this page is hidden. Return here to continue.");
  if (!document.hidden && !stopped) {
    if ("wakeLock" in navigator) navigator.wakeLock.request("screen").then(lock => wakeLock = lock).catch(() => {});
    if (pc?.connectionState !== "connected") scheduleReconnect();
  }
});
setInterval(() => { if (!stopped && heartbeat && Date.now() - heartbeat > 20000) { disconnect(); message("Desktop connection lost. Camera stopped for privacy. Scan a new code to reconnect."); } }, 2000);
