const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');

test('registry script only auto-submits filters, without card hover handlers', () => {
  const handlers = [];
  let submitted = 0;
  const document = {
    querySelectorAll: (selector) => {
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
});
