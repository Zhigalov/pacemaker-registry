const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');

test('registry filters auto-submit and broken avatar falls back to initials', () => {
  const handlers = [];
  const image = {hidden: false, addEventListener: (type, fn) => { assert.equal(type, 'error'); image.onError = fn; }};
  let submitted = 0;
  let searchInput;
  const search = {value: 'Иван', form: {requestSubmit: () => submitted++},
    addEventListener: (type, fn) => { assert.equal(type, 'input'); searchInput = fn; }};
  const document = {
    querySelectorAll: (selector) => {
      if (selector === '[data-avatar-image]') return [image];
      if (selector === '[data-registry-search]') return [search];
      assert.equal(selector, '[data-registry-filter]');
      return [
        {form: {requestSubmit: () => submitted++}, addEventListener: (type, fn) => handlers.push([type, fn])},
        {form: null, addEventListener: (type, fn) => handlers.push([type, fn])},
      ];
    },
  };
  runInNewContext(readFileSync('src/pacemaker_registry/static/registry.js', 'utf8'), {document});
  assert.deepEqual(handlers.map(([type]) => type), ['change', 'change']);
  handlers.forEach(([, fn]) => fn());
  assert.equal(submitted, 1);
  searchInput();
  assert.equal(submitted, 1); // Typing does not reload and interrupt the input.
  search.value = '';
  searchInput();
  assert.equal(submitted, 2); // Clearing submits the same form, preserving other filters.
  image.onError();
  assert.equal(image.hidden, true);
});
