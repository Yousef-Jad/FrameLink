"use strict";
// Local measurements only. No external probes, fixed bitrate floors, or upscaling.
class CameraQuality {
  constructor(track, sender, peer, channel) {
    this.track = track; this.sender = sender; this.peer = peer; this.channel = channel;
    this.previous = null; this.good = 0; this.busy = false; this.recording = false;
    this.receiver = {}; this.target = this.budget(track.getSettings());
    this.capabilities = track.getCapabilities ? track.getCapabilities() : {};
    this.timer = null; this.closed = false; this.parameterError = "";
  }
  budget(s) {
    return Math.round(Math.min(20000000, Math.max(1000000, (s.width || 1280) * (s.height || 720) * (s.frameRate || 30) * .12)));
  }
  async apply() {
    const p = this.sender.getParameters();
    if (!p.encodings?.length) return;
    p.encodings[0].maxBitrate = this.target;
    p.encodings[0].maxFramerate = this.track.getSettings().frameRate || 30;
    p.encodings[0].scaleResolutionDownBy = 1;
    p.degradationPreference = "maintain-resolution";
    try { await this.sender.setParameters(p); this.parameterError = ""; }
    catch (error) { this.parameterError = error.message; }
  }
  async start() {
    await this.apply();
    if (this.closed) return;
    this.timer = setInterval(() => this.sample(), 2000);
  }
  stop() { this.closed = true; clearInterval(this.timer); }
  feedback(data) {
    this.recording = ["Preparing", "Recording", "Paused", "Stopping", "Finalizing recording"].includes(data.recording);
    this.receiver = data.receiver || {};
  }
  async sample() {
    if (this.busy || this.closed || this.peer.connectionState !== "connected") return;
    this.busy = true;
    try {
      const stats = await this.peer.getStats();
      const values = Array.from(stats.values());
      const out = values.find(s => s.type === "outbound-rtp" && s.kind === "video");
      if (!out) return;
      const remote = values.find(s => s.type === "remote-inbound-rtp" && s.localId === out.id);
      const transport = stats.get(out.transportId);
      const pair = stats.get(transport?.selectedCandidatePairId) || values.find(s => s.type === "candidate-pair" && s.nominated && s.state === "succeeded");
      const seconds = this.previous ? (out.timestamp - this.previous.timestamp) / 1000 : 0;
      const bitrate = seconds > 0 ? Math.max(0, 8 * (out.bytesSent - this.previous.bytesSent) / seconds) : 0;
      const fps = seconds > 0 ? Math.max(0, (out.framesEncoded - this.previous.framesEncoded) / seconds) : (out.framesPerSecond || 0);
      this.previous = out;
      let actual = this.track.getSettings();
      const available = pair?.availableOutgoingBitrate;
      const rtt = remote?.roundTripTime ?? pair?.currentRoundTripTime ?? 0;
      const constrained = out.qualityLimitationReason === "cpu" || rtt > .3 || (remote?.fractionLost || 0) > .03;
      if (constrained) {
        this.good = 0;
        this.target = Math.max(500000, Math.round(this.target * .8));
      } else {
        this.good++;
        this.target = Math.min(this.budget(actual), Math.round(this.target * 1.15));
      }
      // An estimate based on a quiet scene is not a capacity test. Capping our
      // ceiling to that estimate repeatedly would prevent quality from recovering.
      if (constrained && Number.isFinite(available) && available > 0) this.target = Math.min(this.target, Math.max(300000, Math.round(available * .8)));
      // Probe a higher camera mode only with observed network headroom and receiver
      // throughput. Keep source dimensions stable while desktop recording is active.
      if (this.good >= 5 && !this.recording && this.receiver.fps >= (actual.frameRate || 30) * .85 && Number.isFinite(available)) {
        const caps = this.capabilities;
        let candidate = null;
        if (actual.width >= actual.height && actual.width < 3840 && (caps.width?.max || 0) >= 3840 && (caps.height?.max || 0) >= 2160) candidate = {width: 3840, height: 2160, frameRate: Math.min(30, actual.frameRate || 30)};
        else if (actual.frameRate < 55 && (caps.frameRate?.max || 0) >= 60) candidate = {width: actual.width, height: actual.height, frameRate: 60};
        if (candidate && available * .7 >= this.budget(candidate)) {
          try { await this.track.applyConstraints({width: {ideal: candidate.width}, height: {ideal: candidate.height}, frameRate: {ideal: candidate.frameRate, max: candidate.frameRate}}); }
          catch (_) { /* Unsupported combinations retain the last working mode. */ }
          actual = this.track.getSettings(); this.target = this.budget(actual);
        }
        this.good = 0;
      }
      await this.apply();
      const report = {type: "quality", capture_width: actual.width || 0, capture_height: actual.height || 0,
        capture_fps: actual.frameRate || 0, sent_width: out.frameWidth || 0, sent_height: out.frameHeight || 0,
        sent_fps: fps, bitrate, target_bitrate: this.target, rtt, limitation: out.qualityLimitationReason || "unknown",
        parameter_error: this.parameterError};
      window.framelinkQuality = report;
      const display = document.getElementById("actual");
      if (display) display.textContent = `${actual.width} × ${actual.height} · ${Math.round(actual.frameRate || 0)} FPS capture · ${(bitrate / 1000000).toFixed(1)} Mbit/s sent`;
      if (this.channel.readyState === "open" && !this.closed) this.channel.send(JSON.stringify(report));
    } catch (error) { this.parameterError = error.message; }
    finally { this.busy = false; }
  }
}
