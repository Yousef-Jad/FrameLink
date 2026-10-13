const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const listeners = {};
const intervals = [];
const storage = new Map();
const elements = new Map([
  ['status', {textContent: '', className: ''}],
  ['indicator', {textContent: '', className: ''}],
]);
const context = {
  console,
  window: {
    isSecureContext: true,
    addEventListener: (name, handler) => { listeners[name] = handler; },
  },
  document: {
    getElementById: id => elements.get(id) || null,
    addEventListener: () => {},
  },
  navigator: {
    mediaDevices: {},
  },
  location: {hash: ''},
  history: {replaceState: () => {}},
  sessionStorage: {
    getItem: key => storage.get(key) || null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: key => storage.delete(key),
  },
  performance: {now: (() => { let value = 100; return () => value++; })()},
  setInterval: fn => { intervals.push(fn); return intervals.length; },
  clearInterval: () => {},
  setTimeout,
  clearTimeout,
};
vm.createContext(context);
vm.runInContext(
  fs.readFileSync('src/framelink/web/camera.js', 'utf8') +
     '\nglobalThis.__startupTiming = window.framelinkTiming;\n' +
     'globalThis.__handleServerMessage = window.framelinkHandleServerMessage;\n' +
    'globalThis.__observeCameraPermission = window.__observeCameraPermission;\n' +
    'globalThis.__buildIceDiagnostics = window.framelinkBuildIceDiagnostics;',
  context,
);

assert.ok(context.__startupTiming, 'camera page exposes startup timing');
const timing = context.__startupTiming.snapshot();
for (const mark of ['t0_page_opened', 't2_get_user_media_completed', 't3_sdp_offer_created', 't4_sdp_answer_received', 't5_ice_connected']) {
  assert.ok(Object.hasOwn(timing.marks, mark), `${mark} is present`);
}
assert.equal(timing.clock, 'performance.now');
assert.equal(timing.availability.permission_observation, 'unavailable');
assert.equal(typeof context.__handleServerMessage, 'function');
assert.equal(JSON.stringify(context.__buildIceDiagnostics(
  'a=candidate:1 1 UDP 1 172.20.10.2 5000 typ host',
  'a=candidate:2 1 UDP 1 phone-uuid.local 5001 typ host',
)), JSON.stringify({
  local_candidates: [{address: '172.20.10.2', port: 5000, protocol: 'udp', type: 'host'}],
  remote_candidates: [{address: 'phone-uuid.local', port: 5001, protocol: 'udp', type: 'host'}],
}));
context.__handleServerMessage({status: 'awaiting-video', recording: 'Ready'});
assert.match(elements.get('status').textContent, /awaiting video/i);
context.__handleServerMessage({status: 'video-received', recording: 'Ready'});
assert.equal(elements.get('status').textContent, 'Live to Studio');

(async () => {
  assert.equal(typeof context.__observeCameraPermission, 'function');
  const result = await context.__observeCameraPermission(
    {query: () => new Promise(() => {})},
    10,
  );
  assert.equal(result.availability, 'unavailable-timeout');
  console.log('Camera startup timing API passed');
})().catch(error => { console.error(error); process.exitCode = 1; });

// Recording status alone must never invent a desktop video acknowledgment.
context.__handleServerMessage({status: 'awaiting-video', recording: 'Recording'});
assert.doesNotMatch(elements.get('indicator').textContent, /LIVE TO STUDIO/);
const cameraSource = fs.readFileSync('src/framelink/web/camera.js', 'utf8');
assert.doesNotMatch(cameraSource, /disconnect\(\);\s*message\("Desktop connection timed out/);
assert.match(cameraSource, /if \(!cameraUsable\(\)\) await acquireCamera\(\)/);

// A missing desktop heartbeat preserves both authorization and the live camera.
vm.runInContext(`
 stopped = false; heartbeat = Date.now() - 25000; session = "existing-session";
 stream = {getVideoTracks: () => [{readyState: "live", muted: false, enabled: true}]};
 pc = {connectionState: "connected", iceConnectionState: "connected"};
`, context);
const realTimeout = context.setTimeout;
context.setTimeout = () => 123;
intervals.at(-1)();
assert.equal(vm.runInContext('session', context), 'existing-session');
assert.equal(vm.runInContext('stopped', context), false);
assert.equal(vm.runInContext('cameraUsable()', context), true);
assert.equal(vm.runInContext('reconnectTimer', context), 123);
context.setTimeout = realTimeout;
