import test from 'node:test';
import assert from 'node:assert/strict';
import { renderMarkdownInto } from '../services/markdown.js';

class TestNode {
  constructor(tagName, ownerDocument) {
    this.tagName = tagName;
    this.ownerDocument = ownerDocument;
    this.children = [];
    this.attributes = {};
    this._text = '';
  }

  append(...nodes) {
    this.children.push(...nodes);
  }

  replaceChildren(...nodes) {
    this.children = nodes;
  }

  set textContent(value) {
    this._text = String(value);
    this.children = [];
  }

  get textContent() {
    return this._text + this.children.map((child) => child.textContent).join('');
  }

  set href(value) { this.attributes.href = value; }
  set target(value) { this.attributes.target = value; }
  set rel(value) { this.attributes.rel = value; }
  get href() { return this.attributes.href; }
  get rel() { return this.attributes.rel; }
}

function createDocument() {
  const doc = {
    baseURI: 'https://console.example/operator/',
    createElement(tagName) {
      return new TestNode(tagName, doc);
    },
    createTextNode(text) {
      const node = new TestNode('#text', doc);
      node.textContent = text;
      return node;
    },
  };
  return doc;
}

function render(markdown) {
  const doc = createDocument();
  const root = doc.createElement('div');
  renderMarkdownInto(root, markdown);
  return root;
}

test('renders headings, paragraphs, emphasis, and inline code as elements', () => {
  const root = render('# Status\n\n**Memory Capacity** is `unknown`.');
  assert.deepEqual(root.children.map((node) => node.tagName), ['h1', 'p']);
  assert.deepEqual(root.children[1].children.map((node) => node.tagName), ['strong', '#text', 'code', '#text']);
  assert.equal(root.textContent, 'StatusMemory Capacity is unknown.');
});

test('renders ordered, unordered, and fenced code blocks', () => {
  const root = render('- first\n- second\n\n1. one\n2. two\n\n```js\nconst answer = 42;\n```');
  assert.deepEqual(root.children.map((node) => node.tagName), ['ul', 'ol', 'pre']);
  assert.deepEqual(root.children[0].children.map((node) => node.tagName), ['li', 'li']);
  assert.equal(root.children[2].children[0].textContent, 'const answer = 42;');
});

test('allows safe links and renders unsafe links as text', () => {
  const root = render('[docs](https://example.com/docs) [bad](javascript:alert)');
  const links = root.children[0].children.filter((node) => node.tagName === 'a');
  assert.equal(links.length, 1);
  assert.equal(links[0].href, 'https://example.com/docs');
  assert.equal(links[0].rel, 'noopener noreferrer');
  assert.equal(root.textContent, 'docs bad');
});

test('untrusted HTML and script-like input remain text nodes', () => {
  const root = render('<img src=x onerror=alert(1)> **safe**');
  assert.equal(root.children[0].tagName, 'p');
  assert.equal(root.children[0].children.some((node) => node.tagName === 'img'), false);
  assert.equal(root.textContent, '<img src=x onerror=alert(1)> safe');
});
