const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');

function setup(saved = null, blocked = false) {
  const handlers = {}, attributes = {}, tracks = {}, captured = new Set(), classes = new Set();
  let width = 1020, stored = saved, onResize;
  const classList = { add: value => classes.add(value), remove: value => classes.delete(value) };
  const handle = {
    addEventListener: (name, fn) => { handlers[name] = fn; }, classList,
    setAttribute: (name, value) => { attributes[name] = value; },
    setPointerCapture: id => captured.add(id), hasPointerCapture: id => captured.has(id),
    releasePointerCapture: id => captured.delete(id),
  };
  const panel = { getBoundingClientRect: () => ({ width }), style: { setProperty: (key, value) => { tracks[key] = value; } } };
  const window = { innerWidth: 1440, addEventListener: () => {} };
  const context = vm.createContext({
    $: selector => selector === '#taskColumnResizer' ? handle : panel,
    clamp: (v, min, max) => Math.min(Math.max(v, min), max),
    window, document: { body: { classList } },
    localStorage: {
      getItem: () => { if (blocked) throw Error('storage unavailable'); return stored; },
      setItem: (_, value) => { if (blocked) throw Error('storage unavailable'); stored = value; },
    },
    ResizeObserver: class { constructor(fn) { onResize = fn; } observe() {} },
  });
  vm.runInContext(source.slice(source.indexOf('function taskColumnBounds'), source.indexOf('async function apiGet')), context);
  vm.runInContext('setupTaskColumnResizer()', context);
  return {
    attributes, tracks, captured, classes, window, saved: () => stored,
    resize: value => { width = value; onResize(); },
    fire: (name, values = {}) => handlers[name]({ button: 0, pointerId: 1, clientX: 500, preventDefault() {}, ...values }),
  };
}

test('drag adjusts both columns, clamps widths and persists across reload', () => {
  const ui = setup();
  ui.fire('pointerdown'); ui.fire('pointermove', { clientX: 600 }); ui.fire('pointerup');
  assert.equal(ui.tracks['--task-left-track'], '0.6fr');
  assert.equal(ui.tracks['--task-right-track'], '0.4fr');
  assert.equal(ui.captured.size, 0);
  assert.equal(ui.classes.size, 0);
  assert.equal(setup(ui.saved()).attributes['aria-valuenow'], '60');
  ui.fire('pointerdown'); ui.fire('pointermove', { clientX: 99999 }); ui.fire('pointercancel');
  assert.equal(ui.attributes['aria-valuenow'], '68');
  assert.equal(ui.captured.size, 0);
  ui.fire('dblclick');
  assert.equal(ui.attributes['aria-valuenow'], '50');
  assert.equal(ui.saved(), '0.5');
});

test('keyboard controls and responsive resizing respect minimum widths', () => {
  const ui = setup('0.6');
  ui.fire('keydown', { key: 'ArrowLeft' });
  assert.equal(ui.attributes['aria-valuenow'], '58');
  ui.fire('keydown', { key: 'Home' });
  assert.equal(ui.attributes['aria-valuenow'], '32');
  ui.resize(720);
  assert.equal(ui.attributes['aria-valuenow'], '46');
  ui.resize(1020);
  assert.equal(ui.attributes['aria-valuenow'], '32');
  ui.window.innerWidth = 900;
  ui.fire('pointerdown');
  assert.equal(ui.captured.size, 0);
});

test('invalid or blocked storage does not break dragging', () => {
  assert.equal(setup('not json').attributes['aria-valuenow'], '50');
  assert.equal(setup('null').attributes['aria-valuenow'], '50');
  const ui = setup(null, true);
  ui.fire('pointerdown');
  ui.fire('pointermove', { clientX: 600, pointerId: 2 });
  assert.equal(ui.attributes['aria-valuenow'], '50');
  ui.fire('pointermove', { clientX: 600 });
  ui.fire('lostpointercapture');
  assert.equal(ui.attributes['aria-valuenow'], '60');
  assert.equal(ui.classes.size, 0);
});
