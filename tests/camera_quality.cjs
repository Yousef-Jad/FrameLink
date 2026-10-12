const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {window: {}, document: {getElementById: () => null}, setInterval, clearInterval};
vm.createContext(context);
vm.runInContext(fs.readFileSync('src/framelink/web/quality.js', 'utf8') + '\nglobalThis.CameraQuality = CameraQuality;', context);

(async () => {
  const actual = {width: 1920, height: 1080, frameRate: 30};
  let parameters, reject = false, rtt = .01, timestamp = 0;
  const reports = [];
  const track = {getSettings: () => actual, getCapabilities: () => ({})};
  const sender = {getParameters: () => ({encodings: [{}]}), setParameters: async p => {
    if (reject) throw Error('unsupported parameter');
    parameters = p;
  }};
  const peer = {connectionState: 'connected', getStats: async () => {
    timestamp += 2000;
    return new Map([
      ['video', {id: 'video', type: 'outbound-rtp', kind: 'video', timestamp,
        bytesSent: timestamp * 800, framesEncoded: timestamp * .03,
        frameWidth: 1920, frameHeight: 1080, framesPerSecond: 30,
        qualityLimitationReason: 'none', transportId: 'transport'}],
      ['transport', {selectedCandidatePairId: 'pair'}],
      ['pair', {currentRoundTripTime: rtt, availableOutgoingBitrate: 300000}],
    ]);
  }};
  const quality = new context.CameraQuality(track, sender, peer, {readyState: 'open', send: r => reports.push(JSON.parse(r))});
  await quality.apply();
  const budget = parameters.encodings[0].maxBitrate;
  assert.equal(budget, 7464960);
  assert.equal(parameters.encodings[0].scaleResolutionDownBy, 1);
  assert.equal(parameters.degradationPreference, 'maintain-resolution');
  quality.feedback({recording: 'Recording', receiver: {fps: 3}});
  await quality.sample();
  // Low receive cadence alone is not evidence of network congestion. Feeding it
  // back into the bitrate ceiling made a low startup estimate self-perpetuating.
  assert.equal(parameters.encodings[0].maxBitrate, budget);
  rtt = .5;
  await quality.sample();
  assert.ok(parameters.encodings[0].maxBitrate < budget);
  const reduced = parameters.encodings[0].maxBitrate;
  rtt = .01;
  await quality.sample();
  assert.ok(parameters.encodings[0].maxBitrate > reduced);
  assert.equal(reports.at(-1).sent_fps, 30);
  assert.equal(reports.at(-1).bitrate, 6400000);
  reject = true;
  await quality.sample();
  assert.equal(reports.at(-1).parameter_error, 'unsupported parameter');
  quality.stop();
  const count = reports.length;
  await quality.sample();
  assert.equal(reports.length, count);
  console.log('Camera bitrate adaptation and diagnostics passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
