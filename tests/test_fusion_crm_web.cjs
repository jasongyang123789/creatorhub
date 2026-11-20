/* Fixture-only DOM interaction checks. No HTTP listener, browser, account or platform. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { randomUUID } = require('node:crypto');
const { test } = require('node:test');
const WEB = path.resolve(__dirname, '../app/web');
const html = fs.readFileSync(path.join(WEB, 'crm.html'), 'utf8');
const script = fs.readFileSync(path.join(WEB, 'crm.js'), 'utf8');

function harness() {
  const nodes = [];
  class Element {
    constructor(tag, text = '') {
      this.tagName = tag.toUpperCase(); this.textContent = text; this.children = [];
      this.listeners = {}; this.dataset = {}; this.attributes = {}; this.value = '';
      this.disabled = false; this.checked = false; this._classes = new Set(); nodes.push(this);
      this.classList = { add: (...names) => names.forEach(name => this._classes.add(name)),
        remove: (...names) => names.forEach(name => this._classes.delete(name)),
        toggle: (name, value) => { const add = value === undefined ? !this._classes.has(name) : value; if (add) this._classes.add(name); else this._classes.delete(name); },
        contains: name => this._classes.has(name) };
    }
    set className(value) { this._classes = new Set(value.split(/\s+/).filter(Boolean)); }
    get className() { return [...this._classes].join(' '); }
    setAttribute(key, value) { this.attributes[key] = value; }
    removeAttribute(key) { delete this.attributes[key]; }
    append(...values) { this.children.push(...values); }
    replaceChildren(...values) { this.children = [...values]; }
    addEventListener(name, handler) { (this.listeners[name] ||= []).push(handler); }
    trigger(name, extra = {}) { for (const handler of this.listeners[name] || []) handler({ preventDefault() {}, currentTarget: this, ...extra }); }
    focus() {} scrollIntoView() {} reset() {}
  }
  const ids = {};
  for (const match of html.matchAll(/<([\w-]+)\b[^>]*\bid="([^"]+)"[^>]*>/g)) ids[match[2]] = new Element(match[1]);
  const tabs = ['inbox', 'tasks', 'ledger'].map(tab => { const node = new Element('button'); node.dataset.tab = tab; return node; });
  const selectedLabels = Array.from({ length: 4 }, () => { const node = new Element('p'); node.className = 'selected-customer'; return node; });
  ids['task-filter'].value = 'due'; ids['rfm-window'].value = '365';
  const document = {
    getElementById: id => { assert.ok(ids[id], `missing HTML id ${id}`); return ids[id]; },
    createElement: tag => new Element(tag), createTextNode: text => new Element('#text', text),
    querySelectorAll: selector => {
      if (selector === '[data-tab]') return tabs;
      if (selector === '.selected-customer') return selectedLabels;
      if (selector === '[data-conversation-id]') return nodes.filter(node => node.dataset.conversationId);
      if (selector === '[data-customer-id]') return nodes.filter(node => node.dataset.customerId);
      throw new Error('Unexpected selector: ' + selector);
    },
  };
  const requests = []; const pending = new Map();
  const conversations = [
    { conversation_id: 11, platform: 'douyin', account_id: 1, peer_uid: 'peer-A', peer_nickname: 'A', last_time: 1, unread_count: 1 },
    { conversation_id: 12, platform: 'douyin', account_id: 2, peer_uid: 'peer-B', peer_nickname: 'B', last_time: 1, unread_count: 1 },
  ];
  const drafts = new Map([[11, []], [12, []]]);
  const customers = [{ id: 1, display_name: '同名', identities: [{ platform: 'douyin', uid: 'peer-A', source: 'dm' }] },
    { id: 2, display_name: '同名', identities: [{ platform: 'douyin', uid: 'peer-B', source: 'dm' }] }];
  let nextTask = 0;
  async function fetchFixture(url, options = {}) {
    const parsed = new URL(url, 'http://fixture.invalid'); const method = options.method || 'GET';
    const body = options.body ? JSON.parse(options.body) : undefined;
    requests.push({ url: parsed.pathname, query: Object.fromEntries(parsed.searchParams), method, body });
    const route = parsed.pathname.replace('/api/fusion/crm', '');
    let data;
    if (route === '/inbox/accounts') data = { items: [{ platform: 'douyin', account_id: 1, nickname: 'Fixture A', conversations: 1 }] };
    else if (route === '/inbox') data = { items: conversations, total: conversations.length };
    else if (route === '/customers') data = { items: customers, total: customers.length };
    else if (route.startsWith('/customers/')) data = customers.find(customer => customer.id === Number(route.split('/').pop()));
    else if (/^\/inbox\/\d+$/.test(route)) {
      const id = Number(route.split('/').pop());
      data = { conversation: conversations.find(row => row.conversation_id === id), messages: [{ id, direction: 'in', text: `message-${id}`, create_time: 1, msg_type: 'text' }], message_total: 1, scope_token: String(id).padStart(64, '0'), drafts: drafts.get(id) };
      if (pending.has(id)) return pending.get(id).promise.then(() => ({ ok: true, json: async () => data }));
    } else if (/^\/inbox\/\d+\/drafts$/.test(route)) {
      const id = Number(route.split('/')[2]);
      const draft = { id: randomUUID(), revision: 1, text: body.text, status: 'draft', updated_at: '2026-11-19T00:00:00Z' };
      drafts.get(id).push(draft); data = { draft, outbound_enabled: false };
    } else if (route === '/tasks' && method === 'GET') data = { items: [], total: 0 };
    else if (route === '/tasks' && method === 'POST') data = { task: { id: String(++nextTask), ...body }, created: true };
    else if (route === '/sops') data = { items: [] };
    else if (route === '/transactions' && method === 'GET') data = { items: [], total: 0 };
    else if (route === '/transactions' && method === 'POST') data = { transaction: { id: randomUUID(), ...body }, created: true };
    else if (route === '/rfm') data = { items: [], total: 0 };
    else throw new Error('Unexpected API fixture route ' + method + ' ' + route);
    return { ok: true, json: async () => data };
  }
  vm.runInNewContext(script, { document, Node: Element, Option: class Option extends Element { constructor(text, value) { super('option', text); this.value = value; } },
    crypto: { randomUUID }, Intl, Date, URLSearchParams, NodeFilter: {}, fetch: fetchFixture,
    location: { hash: '' }, history: { replaceState() {} }, window: { prompt() { throw new Error('Unexpected prompt'); } } });
  const settle = async () => { for (let count = 0; count < 6; count++) await new Promise(resolve => setImmediate(resolve)); };
  return { ids, requests, nodes, settle, tabs,
    deferConversation(id) { let release; const promise = new Promise(resolve => { release = resolve; }); pending.set(id, { promise, release }); return () => { pending.delete(id); release(); }; },
    clickConversation(id) { ids['inbox-list'].children.find(node => node.dataset.conversationId === String(id)).trigger('click'); },
    clickCustomer(id) { ids['customer-list'].children.find(node => node.dataset.customerId === String(id)).trigger('click'); },
  };
}

function submit(h, id) { h.ids[id].trigger('submit', { submitter: null }); }

test('HTML hooks resolve and reply draft is pinned to selected account/conversation', async () => {
  const h = harness(); await h.settle();
  assert.equal(h.ids.status.classList.contains('error'), false);
  h.clickConversation(11); await h.settle();
  h.ids['draft-text'].value = 'local reply A'; submit(h, 'draft-form'); await h.settle();
  const saved = h.requests.find(row => row.method === 'POST' && row.url.endsWith('/drafts'));
  assert.deepEqual(saved.query, { platform: 'douyin', account_id: '1' });
  assert.equal(saved.url, '/api/fusion/crm/inbox/11/drafts');
  assert.equal(saved.body.scope_token, '11'.padStart(64, '0'));
  assert.equal(saved.body.text, 'local reply A');
  assert.match(h.ids.status.textContent, /没有发送/);
  assert.ok(h.requests.every(row => row.url.startsWith('/api/fusion/crm/')));
});

test('late response from A cannot replace B or carry A composer into B', async () => {
  const h = harness(); await h.settle();
  const release = h.deferConversation(11);
  h.clickConversation(11); await h.settle();
  h.clickConversation(12); await h.settle();
  assert.equal(h.ids['conversation-title'].textContent, 'B');
  h.ids['draft-text'].value = 'local reply B';
  release(); await h.settle();
  assert.equal(h.ids['conversation-title'].textContent, 'B');
  assert.equal(h.ids['draft-text'].value, 'local reply B');
  submit(h, 'draft-form'); await h.settle();
  const saved = h.requests.find(row => row.method === 'POST' && row.url.endsWith('/drafts'));
  assert.equal(saved.url, '/api/fusion/crm/inbox/12/drafts');
  assert.equal(saved.query.account_id, '2');
  assert.equal(saved.body.scope_token, '12'.padStart(64, '0'));
});

test('customer ID confirmation is cleared when switching same-named customer', async () => {
  const h = harness(); await h.settle();
  h.clickCustomer(1); await h.settle();
  h.ids['sale-confirm-id'].value = '1'; h.ids['sale-confirm'].checked = true;
  h.clickCustomer(2); await h.settle();
  assert.equal(h.ids['sale-confirm-id'].value, '');
  assert.equal(h.ids['sale-confirm'].checked, false);
  h.ids['sale-cents'].value = '12345'; h.ids['sale-reference'].value = 'fixture-order';
  h.ids['sale-confirm-id'].value = '1'; h.ids['sale-confirm'].checked = true;
  submit(h, 'sale-form'); await h.settle();
  assert.equal(h.requests.filter(row => row.method === 'POST' && row.url.endsWith('/transactions')).length, 0);
  h.ids['sale-confirm-id'].value = '2'; submit(h, 'sale-form'); await h.settle();
  const saved = h.requests.find(row => row.method === 'POST' && row.url.endsWith('/transactions'));
  assert.equal(saved.body.customer_id, 2); assert.equal(saved.body.confirm_customer_id, 2);
  assert.equal(saved.body.amount_cents, 12345); assert.equal(saved.body.currency, 'CNY');
});

test('page tabs load due tasks and RFM using only local API routes', async () => {
  const h = harness(); await h.settle();
  h.tabs.find(node => node.dataset.tab === 'tasks').trigger('click'); await h.settle();
  assert.ok(h.requests.some(row => row.url.endsWith('/tasks') && row.query.due_only === 'true'));
  h.tabs.find(node => node.dataset.tab === 'ledger').trigger('click'); await h.settle();
  assert.ok(h.requests.some(row => row.url.endsWith('/rfm') && row.query.window_days === '365'));
  assert.equal(h.ids.status.classList.contains('error'), false);
});
