const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');

test('same-sol variants remain distinct and restore the original comparison on selection', async () => {
  class Element extends EventTarget {
    constructor() {
      super(); this.children = []; this.dataset = {}; this.attributes = {};
      this.style = { setProperty() {} }; this.classes = new Set();
      this.classList = { toggle: (name, on) => on ? this.classes.add(name) : this.classes.delete(name), remove() {} };
    }
    append(...children) { this.children.push(...children); }
    setAttribute(name, value) { this.attributes[name] = value; }
    matches() { return false; }
  }
  const nodes = new Map();
  const get = key => { if (!nodes.has(key)) nodes.set(key, new Element()); return nodes.get(key); };
  const events = new EventTarget();
  global.document = {
    getElementById: get, querySelector: get, createElement: () => new Element(),
    querySelectorAll: selector => selector === '[data-specimen]' ? get('contact-frames').children : [],
    addEventListener: (...args) => events.addEventListener(...args)
  };
  global.matchMedia = () => ({ matches: true, addEventListener() {} });
  global.addEventListener = () => {};
  global.location = { search: '?sol=1100-color' };
  const original = { sol: 1100, folder: 'stops/sol1100', photos: 19, area_m2: 1824, error: .0518 };
  const other = { ...original, sol: 1104, folder: 'stops/sol1104' };
  const color = { ...original, id: '1100-color', label: 'Sol 1100 · Mastcam color',
    variant: 'mastcam-color', folder: 'stops/sol1100-color', color_coverage: .223, color_photos: 20 };
  global.window = { marswalk: { S: { data: { closeups: [original, other], closeup_variants: [color] } } } };
  const source = readFileSync(path.join(__dirname, '../ui/interactions.js'), 'utf8');
  const { connectExperience } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
  connectExperience('close');
  events.dispatchEvent(new Event('marswalk:ready'));
  const cards = get('contact-frames').children;
  assert.deepEqual(cards.map(c => c.dataset.specimen), ['1100', '1100-color', '1104']);
  assert.match(get('specimen-title').textContent, /MASTCAM COLOR/);
  assert.equal(get('comparison-photo').src, 'stops/sol1100-color/img/step1_photo.jpg');
  assert.equal(get('comparison-depth').hidden, true);
  assert.match(get('.comparison-copy p').textContent, /22.3%/);
  cards[1].dispatchEvent(new Event('click'));
  assert.equal(get('close-pick').value, '1100-color');
  events.dispatchEvent(new CustomEvent('marswalk:specimen', { detail: '1100' }));
  assert.match(get('specimen-title').textContent, /NAVCAM GRAYSCALE/);
  assert.equal(get('comparison-photo').src, 'stops/sol1100/img/step1_photo.jpg');
  assert.equal(get('comparison-depth').hidden, false);
  assert.match(get('.comparison-copy p').textContent, /compare the source photograph/);
});
