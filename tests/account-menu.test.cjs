const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');

test('account menu toggles and dismisses on outside click or Escape', () => {
  const handlers = {};
  const trigger = {
    addEventListener: (type, handler) => { handlers.triggerClick = handler; },
    setAttribute: (name, value) => { assert.equal(name, 'aria-expanded'); trigger.expanded = value; },
    focus: () => { trigger.focused = true; },
  };
  const panel = {hidden: true};
  const menu = {
    querySelector: (selector) => selector === '.site-account-avatar' ? trigger : panel,
    contains: (element) => element === trigger || element === panel,
  };
  const document = {
    querySelector: () => menu,
    addEventListener: (type, handler) => { handlers[type] = handler; },
  };
  runInNewContext(readFileSync('src/pacemaker_registry/static/account-menu.js', 'utf8'), {document});

  handlers.triggerClick();
  handlers.click({target: trigger});
  assert.equal(panel.hidden, false);
  assert.equal(trigger.expanded, 'true');
  handlers.click({target: panel});
  assert.equal(panel.hidden, false);
  handlers.click({target: {}});
  assert.equal(panel.hidden, true);
  assert.equal(trigger.expanded, 'false');

  handlers.triggerClick();
  handlers.keydown({key: 'Escape'});
  assert.equal(panel.hidden, true);
  assert.equal(trigger.focused, true);
});
