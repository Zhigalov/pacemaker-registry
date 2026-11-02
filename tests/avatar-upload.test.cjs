const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const helper = readFileSync('src/pacemaker_registry/static/avatar-upload.js', 'utf8');
const profile = readFileSync('src/pacemaker_registry/static/profile.js', 'utf8');

function compressor({width = 4000, height = 3000, output = {size: 80000}, broken = false} = {}) {
  const calls = {revoked: [], draw: []};
  const context = {fillRect() {}, drawImage: (...args) => calls.draw.push(args)};
  const canvas = {getContext: () => context, toBlob: (fn, type, quality) => {
    calls.type = type; calls.quality = quality; fn(output);
  }};
  const sandbox = {URL: {createObjectURL: () => 'blob:test', revokeObjectURL: url => calls.revoked.push(url)},
    Image: class {
      naturalWidth = width; naturalHeight = height;
      set src(value) { queueMicrotask(() => broken ? this.onerror() : this.onload()); }
    }, document: {createElement: () => canvas}};
  runInNewContext(helper, sandbox);
  return {prepare: sandbox.PacersAvatar.prepare, canvas, calls};
}

test('a photo above the cloud request limit becomes a small centered JPEG before upload', async () => {
  const {prepare, canvas, calls} = compressor();
  const output = await prepare({size: 4 * 1024 * 1024, type: 'image/jpeg'});
  assert.equal(output.size, 80000);
  assert.equal(canvas.width, 512); assert.equal(canvas.height, 512);
  assert.deepEqual(calls.draw[0].slice(1), [500, 0, 3000, 3000, 0, 0, 512, 512]);
  assert.equal(calls.type, 'image/jpeg'); assert.deepEqual(calls.revoked, ['blob:test']);
});

test('invalid input, excessive pixels and failed/oversized encoding never produce an upload', async () => {
  for (const options of [{width: 6000, height: 4000}, {broken: true}, {output: null}, {output: {size: 2 * 1024 * 1024}}]) {
    const {prepare, calls} = compressor(options);
    await assert.rejects(prepare({size: 4 * 1024 * 1024, type: 'image/jpeg'}));
    assert.deepEqual(calls.revoked, ['blob:test']);
  }
  for (const file of [{size: 6 * 1024 * 1024, type: 'image/jpeg'}, {size: 100, type: 'text/html'}]) {
    await assert.rejects(compressor().prepare(file));
  }
});

function formHarness(prepare, modern = true) {
  const listeners = {};
  const button = {textContent: 'Сохранить', disabled: false};
  const input = {files: [{size: 4 * 1024 * 1024}], addEventListener() {}};
  const error = {textContent: '', hidden: true, scrollIntoView() {}};
  const form = {addEventListener: (event, fn) => {listeners[event] = fn;},
    querySelector: () => button, requestSubmit: () => {form.submits++;}, submits: 0};
  input.form = form;
  const nodes = {'[data-avatar-input]': input, '[data-avatar-preview]': {getAttribute: () => null},
    '[data-photo-error]': error};
  runInNewContext(profile, {document: {querySelector: s => nodes[s], querySelectorAll: () => []},
    window: modern ? {FormDataEvent: class {}} : {}, PacersAvatar: {prepare}});
  const event = () => ({submitter: button, prevented: false, preventDefault() {this.prevented = true;}});
  return {listeners, button, input, error, form, event};
}

test('native submit waits for compression, then replaces only the photo part', async () => {
  let finish;
  const compressed = {size: 80000};
  const h = formHarness(() => new Promise(resolve => {finish = resolve;}));
  const first = h.event(); const pending = h.listeners.submit(first);
  assert.equal(first.prevented, true); assert.equal(h.button.disabled, true);
  assert.equal(h.form.submits, 0);
  const duplicate = h.event(); await h.listeners.submit(duplicate);
  assert.equal(duplicate.prevented, true);
  finish(compressed); await pending;
  assert.equal(h.form.submits, 1); assert.equal(h.button.disabled, false);
  const second = h.event(); await h.listeners.submit(second); assert.equal(second.prevented, false);
  const parts = new Map([['about', 'Keep my profile'], ['photo', h.input.files[0]]]);
  h.listeners.formdata({formData: {set: (key, value) => parts.set(key, value)}});
  assert.equal(parts.get('photo'), compressed); assert.equal(parts.get('about'), 'Keep my profile');
});

test('compression failure and unsupported browser do not submit a raw oversized file', async () => {
  for (const h of [formHarness(async () => {throw new Error('Cannot decode');}), formHarness(async () => ({}), false)]) {
    const event = h.event(); await h.listeners.submit(event);
    assert.equal(event.prevented, true); assert.equal(h.form.submits, 0);
    assert.equal(h.error.hidden, false); assert.ok(h.error.textContent);
  }
});

test('changing the selected file while decoding never submits the old photo', async () => {
  let finish;
  const h = formHarness(() => new Promise(resolve => {finish = resolve;}));
  const pending = h.listeners.submit(h.event());
  h.input.files = [{size: 4 * 1024 * 1024}];
  finish({size: 80000}); await pending;
  assert.equal(h.form.submits, 0); assert.equal(h.button.disabled, false);
});
