const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const source = readFileSync('src/pacemaker_registry/static/analytics.js', 'utf8');

function run({host = 'pacersreg.ru', id = '113525433', goals = [], referrer = 'https://pacersreg.ru/add?result_url=private#secret'} = {}) {
  const appended = [], handlers = {}, window = {};
  const document = {
    currentScript: {dataset: {metricaId: id}}, referrer,
    head: {append: (script) => appended.push(script)}, createElement: () => ({}),
    querySelectorAll: () => goals.map((name) => ({dataset: {analyticsGoal: name}})),
    addEventListener: (type, handler, capture) => { handlers[type] = {handler, capture}; },
  };
  runInNewContext(source, {document, window, URL, location: {hostname: host, origin: `https://${host}`, href: `https://${host}/add?result_url=private#secret`}});
  return {appended, handlers, window, calls: () => Array.from(window.ym?.a || [], (args) => Array.from(args))};
}

test('one sanitized pageview, no webvisor, form values, titles, or auto-link tracking', () => {
  const r = run();
  assert.equal(r.appended.length, 1);
  assert.equal(r.appended[0].async, true);
  assert.equal(r.calls().length, 1);
  const [id, method, config] = r.calls()[0];
  assert.equal(id, 113525433); assert.equal(method, 'init');
  assert.equal(config.url, 'https://pacersreg.ru/add');
  assert.equal(config.referrer, 'https://pacersreg.ru/add');
  for (const key of ['webvisor', 'clickmap', 'trackLinks', 'sendTitle', 'ecommerce']) assert.equal(config[key], false);
  assert.ok(!JSON.stringify(r.calls()).includes('private'));
  assert.equal(run({referrer:'https://search.example/private?q=secret'}).calls()[0][2].referrer, 'https://search.example/');
});
test('disabled on local or technical domains, invalid IDs', () => {
  for (const options of [{host:'localhost'}, {host:'bbapk9h0bc3hh8o1audt.containers.yandexcloud.net'}, {id:'NaN'}, {id:'0'}]) {
    assert.equal(run(options).appended.length, 0);
  }
});
test('allowlisted goals without payload; capture before form auto-submit; blocked SDK is safe', () => {
  const r = run({goals:['result_open', 'result_saved', 'untrusted']});
  assert.deepEqual(r.calls().slice(1), [[113525433,'reachGoal','result_open'], [113525433,'reachGoal','result_saved']]);
  for (const [type, selector, expected] of [['change','[data-registry-filter]','registry_filter'], ['submit','.js-fetch-result','result_parse']]) {
    assert.equal(r.handlers[type].capture, true);
    r.handlers[type].handler({target:{matches:(s) => s === selector}});
    assert.deepEqual(r.calls().at(-1), [113525433,'reachGoal',expected]);
  }
  r.window.ym = () => { throw Error('blocked'); };
  assert.doesNotThrow(() => r.handlers.change.handler({target:{matches:() => true}}));
});
