"use strict";

class RecordingControls {
  constructor() {
    this.button = document.getElementById("record-btn");
    this.timer = document.getElementById("record-timer");
    this.stateLabel = document.getElementById("record-state");
    this.modeLabel = document.getElementById("record-mode");
    this.policyLabel = document.getElementById("record-policy");
    this.errorLabel = document.getElementById("record-error");
    this.channel = null;
    this.snapshot = null;
    this.pending = null;
    this.clockAt = null;
    this.nextId = 0;
    this.button?.addEventListener("click", () => this.command());
    this.render();
  }

  setChannel(channel) {
    this.channel = channel;
    this.snapshot = null;
    this.pending = null;
    this.render();
  }

  receive(data) {
    if (data?.type === "recording-result") {
      if (!this.pending || data.id !== this.pending.id) return;
      this.pending = null;
      if (!data.ok) this.setError(data.error || "Desktop rejected the recording command");
      if (data.recorder) this.applySnapshot(data.recorder);
      else this.render();
      return;
    }
    if (data?.recorder) this.applySnapshot(data.recorder);
  }

  applySnapshot(snapshot) {
    if (!snapshot || !Number.isInteger(snapshot.revision)) return;
    if (this.snapshot && snapshot.revision < this.snapshot.revision) return;
    this.snapshot = {...snapshot};
    if (snapshot.state === "Recording") this.clockAt = performance.now();
    if (snapshot.state !== "Recording") this.clockAt = null;
    this.render();
  }

  setError(text) {
    if (this.errorLabel) this.errorLabel.textContent = text;
    this.render();
  }

  command() {
    const channel = this.channel;
    const snapshot = this.snapshot;
    if (!channel || channel.readyState !== "open" || !snapshot || this.pending) return;
    const action = snapshot.can_stop ? "stop" : snapshot.can_start ? "start" : null;
    if (!action) return;
    const id = `recording-${Date.now()}-${++this.nextId}`;
    this.pending = {id, action, sentAt: performance.now()};
    try {
      channel.send(JSON.stringify({
        type: "recording-command",
        v: 1,
        id,
        action,
        revision: snapshot.revision,
      }));
      this.render();
    } catch (_) {
      this.pending = null;
      this.setError("Connection lost. Wait for desktop confirmation before trying again.");
      this.setChannel(null);
    }
  }

  render() {
    if (this.pending && performance.now() - this.pending.sentAt >= 5000) {
      this.pending = null;
      this.snapshot = null;
      this.clockAt = null;
      if (this.errorLabel) this.errorLabel.textContent = "Desktop did not confirm the command. Reconnect before trying again.";
    }
    const connected = this.channel?.readyState === "open";
    const snapshot = this.snapshot;
    const pending = this.pending;
    const active = snapshot && ["Preparing", "Recording", "Paused"].includes(snapshot.state);
    const known = Boolean(connected && snapshot);
    if (this.button) {
      this.button.disabled = !known || Boolean(pending) || !(snapshot.can_start || snapshot.can_stop);
      this.button.textContent = pending ? (pending.action === "start" ? "Starting…" : "Stopping…") : active ? "Stop" : "Record";
      this.button.classList.toggle("recording", Boolean(active));
      this.button.setAttribute("aria-label", active ? "Stop desktop recording" : "Start desktop recording");
    }
    if (this.stateLabel) this.stateLabel.textContent = pending ? "Waiting for desktop confirmation…" : snapshot?.state || (connected ? "Desktop state unavailable" : "Phone disconnected");
    if (this.modeLabel) this.modeLabel.textContent = snapshot?.mode ? `Mode: ${snapshot.mode}` : "Mode: —";
    if (this.policyLabel) this.policyLabel.textContent = snapshot?.disconnect_policy ? `Phone disconnect: ${snapshot.disconnect_policy}` : "Phone disconnect: —";
    this.renderTimer();
  }

  renderTimer() {
    const seconds = this.snapshot ? this.snapshot.elapsed + (
      this.snapshot.state === "Recording" && this.clockAt !== null ? (performance.now() - this.clockAt) / 1000 : 0
    ) : 0;
    const total = Math.max(0, Math.floor(seconds + 1e-6));
    const h = String(Math.floor(total / 3600)).padStart(2, "0");
    const m = String(Math.floor(total / 60) % 60).padStart(2, "0");
    const s = String(total % 60).padStart(2, "0");
    if (this.timer) this.timer.textContent = `${h}:${m}:${s}`;
  }
}

window.RecordingControls = RecordingControls;
window.framelinkRecording = new RecordingControls();
setInterval(() => window.framelinkRecording.render(), 1000);
