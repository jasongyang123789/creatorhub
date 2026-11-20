/* Local CRM only. No sender, platform sync, workflow execution or legacy DM API. */
(() => {
  'use strict';
  const API = '/api/fusion/crm';
  const $ = id => document.getElementById(id);
  const state = { tab: 'inbox', scope: null, conversationVersion: 0, messages: [], messageTotal: 0,
    draft: null, draftKey: null, customer: null, csv: null, sopKey: null, taskKey: null,
    offsets: { inbox: 0, customers: 0, tasks: 0, transactions: 0, rfm: 0 },
    totals: {}, tickets: {} };
  const PAGE = 30;
  const taskLabel = { pending: '待处理', in_progress: '进行中', done: '已完成', cancelled: '已取消' };
  const draftLabel = { draft: '本地草稿', reviewed: '已核阅 · 未发送', discarded: '已丢弃' };
  const segmentLabel = { no_purchase: '无购买', dormant: '休眠', at_risk: '有流失风险', champions: '高价值', loyal: '忠诚', recent: '近期', developing: '待培育' };
  const uid = () => crypto.randomUUID();
  const money = cents => new Intl.NumberFormat('zh-CN', { style: 'currency', currency: 'CNY' }).format(cents / 100);
  const date = value => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—';
  const unixDate = value => value ? date(value * 1000) : '时间未知';
  const sameScope = version => version === state.conversationVersion;
  const scopeQuery = scope => new URLSearchParams({ platform: scope.platform, account_id: scope.account_id });

  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function notify(message, error = false) {
    $('status').textContent = message;
    $('status').classList.toggle('error', error);
  }
  async function api(path, body, method = 'POST') {
    const response = await fetch(API + path, { credentials: 'same-origin', cache: 'no-store',
      ...(body === undefined ? {} : { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) });
    const data = await response.json();
    if (!response.ok) {
      const detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail || data);
      throw new Error(`${response.status}：${detail}`);
    }
    return data;
  }
  function run(work, button) {
    if (button && button.dataset.busy === 'true') return;
    if (button) { button.dataset.busy = 'true'; button.disabled = true; }
    return Promise.resolve().then(work).catch(error => notify(error.message || String(error), true)).finally(() => {
      if (button) { button.dataset.busy = ''; button.disabled = false; }
    });
  }
  function button(text, work, className = 'secondary') {
    const node = el('button', text, className);
    node.type = 'button';
    node.addEventListener('click', () => run(work, node));
    return node;
  }
  function on(id, event, work) {
    $(id).addEventListener(event, e => {
      if (event === 'submit') e.preventDefault();
      const control = event === 'submit' ? e.submitter : (e.currentTarget.tagName === 'BUTTON' ? e.currentTarget : null);
      run(() => work(e), control);
    });
  }
  function table(headers, rows, numeric = []) {
    if (!rows.length) return el('p', '暂无本地记录。', 'empty');
    const root = el('table');
    const head = el('thead'); const headRow = el('tr');
    headers.forEach((name, index) => headRow.append(el('th', name, numeric.includes(index) ? 'numeric' : '')));
    head.append(headRow); root.append(head);
    const body = el('tbody');
    rows.forEach(values => {
      const row = el('tr');
      values.forEach((value, index) => { const cell = el('td', undefined, numeric.includes(index) ? 'numeric' : '');
        cell.append(value instanceof Node ? value : document.createTextNode(String(value ?? '—'))); row.append(cell); });
      body.append(row);
    });
    root.append(body); return root;
  }
  function page(kind, total, countId, previousId, nextId) {
    state.totals[kind] = total;
    const offset = state.offsets[kind];
    $(countId).textContent = total ? `${offset + 1}–${Math.min(offset + PAGE, total)} / ${total}` : '0 条';
    $(previousId).disabled = offset === 0;
    $(nextId).disabled = offset + PAGE >= total;
  }
  function pager(kind, previousId, nextId, load) {
    on(previousId, 'click', async () => { state.offsets[kind] = Math.max(0, state.offsets[kind] - PAGE); await load(); });
    on(nextId, 'click', async () => { if (state.offsets[kind] + PAGE < state.totals[kind]) state.offsets[kind] += PAGE; await load(); });
  }
  function localTime(value) {
    const date = new Date(value || Date.now());
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }
  function inputTime(id) {
    if (!$(id).value) throw new Error('请填写时间。');
    return new Date($(id).value).toISOString();
  }
  function selectedCustomer() {
    if (!state.customer) { $('customer-heading').scrollIntoView({ behavior: 'smooth' }); throw new Error('请先选择并核实下方客户 ID。'); }
    return state.customer.id;
  }
  function requestKey(kind, payload) {
    const value = JSON.stringify(payload);
    if (!state[kind] || state[kind].payload !== value) state[kind] = { payload: value, key: uid() };
    return state[kind].key;
  }

  function clearConversation() {
    state.conversationVersion += 1; state.scope = null; state.messages = []; state.draft = null; state.draftKey = null;
    $('conversation-title').textContent = '请选择左侧会话'; $('conversation-scope').textContent = '会话未选择';
    $('messages').replaceChildren(el('p', '选择会话后查看本地消息。', 'empty'));
    $('draft-list').replaceChildren(); $('messages-more').classList.add('hidden');
    $('draft-text').value = ''; $('draft-edit-label').textContent = '';
    for (const id of ['draft-text', 'draft-save', 'draft-new']) $(id).disabled = true;
  }
  function newDraft() {
    state.draft = null; state.draftKey = null; $('draft-text').value = ''; $('draft-edit-label').textContent = '';
  }
  async function loadAccounts() {
    const data = await api('/inbox/accounts'); const value = $('inbox-account').value;
    const options = [new Option('全部本地账号', '')];
    data.items.forEach(account => options.push(new Option(`${account.platform} / #${account.account_id} ${account.nickname} (${account.conversations})`, `${account.platform}:${account.account_id}`)));
    $('inbox-account').replaceChildren(...options); $('inbox-account').value = value;
  }
  async function loadInbox() {
    const ticket = uid(); state.tickets.inbox = ticket;
    const params = new URLSearchParams({ limit: PAGE, offset: state.offsets.inbox, query: $('inbox-query').value.trim() });
    const account = $('inbox-account').value;
    if (account) { const [platform, accountId] = account.split(':'); params.set('platform', platform); params.set('account_id', accountId); }
    const data = await api('/inbox?' + params);
    if (state.tickets.inbox !== ticket) return;
    const nodes = data.items.map(item => {
      const node = button('', async () => chooseConversation(item));
      node.dataset.conversationId = String(item.conversation_id);
      node.setAttribute('aria-current', String(state.scope?.conversation_id === item.conversation_id));
      node.append(el('strong', item.peer_nickname || item.peer_uid || '(未命名对方)'),
        el('small', `${item.platform} · 账号 #${item.account_id} · 会话 #${item.conversation_id}`),
        el('span', item.last_text || '无预览消息', 'preview'),
        el('small', `${unixDate(item.last_time)} · 未读 ${item.unread_count}`));
      return node;
    });
    $('inbox-list').replaceChildren(...(nodes.length ? nodes : [el('p', '暂无已同步会话。本页不会触发平台同步。', 'empty')]));
    page('inbox', data.total, 'inbox-count', 'inbox-prev', 'inbox-next');
  }
  async function chooseConversation(item) {
    clearConversation(); state.scope = { conversation_id: item.conversation_id, platform: item.platform, account_id: item.account_id };
    const version = state.conversationVersion;
    $('conversation-title').textContent = item.peer_nickname || item.peer_uid || '(未命名对方)';
    $('conversation-scope').textContent = `${item.platform} / 账号 #${item.account_id} / 会话 #${item.conversation_id} / 对方 UID ${item.peer_uid || '(无)'}`;
    $('messages').replaceChildren(el('p', '正在读取本地消息…', 'empty'));
    document.querySelectorAll('[data-conversation-id]').forEach(node => node.setAttribute('aria-current', String(node.dataset.conversationId === String(item.conversation_id))));
    try {
      const data = await api(`/inbox/${item.conversation_id}?${scopeQuery(item)}&limit=100`);
      if (!sameScope(version)) return;
      state.scope.scope_token = data.scope_token;
      state.messages = data.messages; state.messageTotal = data.message_total;
      renderMessages(); renderDrafts(data.drafts);
      for (const id of ['draft-text', 'draft-save', 'draft-new']) $(id).disabled = false;
    } catch (error) {
      if (sameScope(version)) { $('messages').replaceChildren(el('p', error.message, 'empty')); notify(error.message, true); }
    }
  }
  function renderMessages() {
    $('messages').replaceChildren(...(state.messages.length ? state.messages.map(message => {
      const node = el('div', undefined, 'message' + (message.direction === 'out' ? ' out' : ''));
      node.append(el('small', `${message.direction === 'out' ? '历史发出' : '收到'} · ${unixDate(message.create_time)} · ${message.msg_type}`),
        el('span', message.text || '[非文本消息]'));
      return node;
    }) : [el('p', '此会话暂无本地消息。', 'empty')]));
    $('messages-more').classList.toggle('hidden', state.messages.length >= state.messageTotal);
  }
  function renderDrafts(drafts) {
    $('draft-list').replaceChildren(...(drafts.length ? drafts.map(draft => {
      const root = el('article', undefined, 'draft');
      root.append(el('p', `${draftLabel[draft.status]} · 版本 ${draft.revision} · ${date(draft.updated_at)}`, 'muted'), el('div', draft.text, 'draft-text'));
      if (draft.status !== 'discarded') {
        const actions = el('div', undefined, 'actions');
        actions.append(button('编辑', () => { state.draft = draft; state.draftKey = null; $('draft-text').value = draft.text; $('draft-edit-label').textContent = `正在编辑本地草稿 v${draft.revision}`; $('draft-text').focus(); }));
        if (draft.status === 'draft') actions.append(button('标记已核阅（不发送）', () => patchDraft(draft, 'reviewed')));
        actions.append(button('丢弃', () => patchDraft(draft, 'discarded'), 'danger')); root.append(actions);
      }
      return root;
    }) : [el('p', '本会话没有本地草稿。', 'empty')]));
  }
  async function refreshDrafts(scope, version) {
    const data = await api(`/inbox/${scope.conversation_id}?${scopeQuery(scope)}&limit=1`);
    if (sameScope(version)) renderDrafts(data.drafts);
  }
  async function patchDraft(draft, status) {
    const scope = { ...state.scope }; const version = state.conversationVersion;
    await api(`/inbox/${scope.conversation_id}/drafts/${draft.id}?${scopeQuery(scope)}`, { expected_revision: draft.revision, status }, 'PATCH');
    if (sameScope(version)) { if (state.draft?.id === draft.id) newDraft(); await refreshDrafts(scope, version); }
    notify(status === 'reviewed' ? '草稿已标记本地核阅；没有发送。' : '本地草稿已丢弃。');
  }
  on('draft-form', 'submit', async () => {
    if (!state.scope || $('draft-text').disabled) throw new Error('请先选择有效会话。');
    const scope = { ...state.scope }; const version = state.conversationVersion;
    const text = $('draft-text').value.trim(); if (!text) throw new Error('回复草稿不能为空。');
    if (state.draft) await api(`/inbox/${scope.conversation_id}/drafts/${state.draft.id}?${scopeQuery(scope)}`, { text, expected_revision: state.draft.revision }, 'PATCH');
    else await api(`/inbox/${scope.conversation_id}/drafts?${scopeQuery(scope)}`, { text, scope_token: scope.scope_token, request_key: requestKey('draftKey', { scope, text }) });
    if (sameScope(version)) { newDraft(); await refreshDrafts(scope, version); }
    notify(`已保存到账号 #${scope.account_id} / 会话 #${scope.conversation_id} 的本地草稿；没有发送。`);
  });
  on('draft-new', 'click', newDraft);
  on('messages-more', 'click', async () => {
    const scope = { ...state.scope }; const version = state.conversationVersion; const offset = state.messages.length;
    const data = await api(`/inbox/${scope.conversation_id}?${scopeQuery(scope)}&limit=100&offset=${offset}`);
    if (sameScope(version)) { const known = new Set(state.messages.map(row => row.id)); state.messages = [...data.messages.filter(row => !known.has(row.id)), ...state.messages]; state.messageTotal = data.message_total; renderMessages(); }
  });
  on('inbox-refresh', 'click', async () => { await loadAccounts(); await loadInbox(); });
  on('inbox-search', 'click', async () => { state.offsets.inbox = 0; clearConversation(); await loadInbox(); });
  on('inbox-account', 'change', async () => { state.offsets.inbox = 0; clearConversation(); await loadInbox(); });
  pager('inbox', 'inbox-prev', 'inbox-next', loadInbox);

  function identityText(customer) {
    return customer.identities.length ? customer.identities.map(identity => `${identity.platform} / ${identity.uid} (${identity.source})`).join('；') : '独立手工主档 / 暂无平台身份';
  }
  function setCustomer(customer) {
    state.customer = customer; state.taskKey = null; state.sopKey = null;
    $('sale-confirm-id').value = ''; $('sale-confirm').checked = false;
    $('selected-customer').textContent = `已选客户 #${customer.id} · ${customer.display_name || '(未命名)'} · ${identityText(customer)}${customer.note ? ' · 备注：' + customer.note : ''}`;
    document.querySelectorAll('.selected-customer').forEach(node => { node.textContent = `客户 #${customer.id} · ${customer.display_name || '(未命名)'}`; });
    document.querySelectorAll('[data-customer-id]').forEach(node => node.setAttribute('aria-pressed', String(node.dataset.customerId === String(customer.id))));
    state.offsets.transactions = 0;
  }
  async function loadCustomers() {
    const ticket = uid(); state.tickets.customers = ticket;
    const data = await api('/customers?' + new URLSearchParams({ query: $('customer-query').value.trim(), limit: PAGE, offset: state.offsets.customers }));
    if (state.tickets.customers !== ticket) return;
    $('customer-list').replaceChildren(...(data.items.length ? data.items.map(customer => {
      const node = button('', async () => { const current = await api('/customers/' + customer.id); setCustomer(current); if (state.tab === 'ledger') await loadTransactions(); if ($('tasks-customer-only').checked) await loadTasks(); }, 'customer-card');
      node.dataset.customerId = String(customer.id); node.setAttribute('aria-pressed', String(state.customer?.id === customer.id));
      node.append(el('strong', `#${customer.id} · ${customer.display_name || '(未命名)'}`), el('small', identityText(customer)));
      if (customer.note) node.append(el('small', customer.note)); return node;
    }) : [el('p', '没有匹配主档。可新建独立手工客户，或从原客户区同步本地身份。', 'empty')]));
    page('customers', data.total, 'customer-count', 'customers-prev', 'customers-next');
  }
  on('customer-form', 'submit', async () => {
    const customer = await api('/customers', { display_name: $('customer-name').value.trim(), note: $('customer-note').value });
    setCustomer(customer); $('customer-form').reset(); await loadCustomers(); notify(`已创建独立客户 #${customer.id}。请核实身份后再入账。`);
  });
  on('customer-search', 'click', async () => { state.offsets.customers = 0; await loadCustomers(); });
  on('customers-refresh', 'click', loadCustomers);
  pager('customers', 'customers-prev', 'customers-next', loadCustomers);

  async function loadTasks() {
    const ticket = uid(); state.tickets.tasks = ticket;
    const filter = $('task-filter').value; const params = new URLSearchParams({ limit: PAGE, offset: state.offsets.tasks });
    if (filter === 'due') params.set('due_only', 'true'); else if (filter !== 'all') params.set('status', filter);
    if ($('tasks-customer-only').checked) params.set('customer_id', selectedCustomer());
    const data = await api('/tasks?' + params);
    if (state.tickets.tasks !== ticket) return;
    $('task-list').replaceChildren(...(data.items.length ? data.items.map(task => {
      const root = el('article', undefined, 'task-card' + (task.is_due ? ' is-due' : ''));
      root.append(el('div', `${task.title} · ${taskLabel[task.status]}${task.is_due ? ' · 已到期' : ''}`, 'task-title'),
        el('p', `客户 #${task.customer_id} · ${task.customer_name} · ${date(task.due_at)} · v${task.revision}`, 'muted'));
      if (task.note) root.append(el('p', task.note));
      const actions = el('div', undefined, 'actions');
      ['in_progress', 'done', 'cancelled', 'pending'].forEach(status => {
        if (task.status !== status) actions.append(button(status === 'pending' ? '重新打开' : taskLabel[status], () => changeTask(task, { status })));
      });
      const due = el('input'); due.type = 'datetime-local'; due.value = localTime(task.due_at); due.setAttribute('aria-label', '新的到期时间');
      actions.append(due, button('延期 / 改期', () => { if (!due.value) throw new Error('请填写新的到期时间。'); return changeTask(task, { due_at: new Date(due.value).toISOString() }); }));
      const history = el('div', '', 'history');
      actions.append(button('查看历史', async () => { const data = await api(`/tasks/${task.id}/history`); history.textContent = data.items.map(event => `v${event.revision} ${date(event.created_at)}：${taskLabel[event.from_status] || '创建'} → ${taskLabel[event.to_status]}；到期 ${date(event.due_at)}${event.note ? '；' + event.note : ''}`).join('\n'); }));
      root.append(actions, history); return root;
    }) : [el('p', '当前筛选下没有本地待办。', 'empty')]));
    page('tasks', data.total, 'task-count', 'tasks-prev', 'tasks-next');
  }
  async function changeTask(task, changes) {
    await api('/tasks/' + task.id, { expected_revision: task.revision, ...changes }, 'PATCH');
    await loadTasks(); notify('本地待办已更新；没有执行平台操作。');
  }
  async function loadSops() {
    const data = await api('/sops'); const selected = $('enroll-sop').value;
    $('enroll-sop').replaceChildren(new Option('请选择已启用模板', ''), ...data.items.filter(sop => sop.active).map(sop => new Option(`${sop.name} (${sop.steps.length} 步)`, sop.id)));
    $('enroll-sop').value = selected;
    $('sop-list').replaceChildren(...data.items.map(sop => { const node = el('div', undefined, 'draft');
      node.append(el('strong', `${sop.name} · ${sop.active ? '已启用' : '已停用'}`), el('p', sop.steps.map(step => `+${step.delay_hours}h ${step.title}`).join(' → '), 'muted'),
        button(sop.active ? '停用模板' : '启用模板', async () => { await api('/sops/' + sop.id, { active: !sop.active }, 'PATCH'); await loadSops(); notify('SOP 模板状态已更新；已有待办保持原状态。'); })); return node; }));
  }
  on('task-form', 'submit', async () => {
    const body = { customer_id: selectedCustomer(), title: $('task-title').value.trim(), due_at: inputTime('task-due'), note: $('task-note').value };
    await api('/tasks', { ...body, request_key: requestKey('taskKey', body) }); state.taskKey = null;
    $('task-title').value = ''; $('task-note').value = ''; await loadTasks(); notify('已创建本地人工待办。');
  });
  on('sop-form', 'submit', async () => {
    const steps = $('sop-steps').value.split(/\r?\n/).filter(line => line.trim()).map((line, index) => {
      const match = line.match(/^\s*(\d+)\s*\|\s*(.+?)\s*$/);
      if (!match) throw new Error(`SOP 第 ${index + 1} 行格式应为：延迟小时 | 任务标题`);
      return { delay_hours: Number(match[1]), title: match[2] };
    });
    await api('/sops', { name: $('sop-name').value.trim(), steps }); $('sop-form').reset(); await loadSops(); notify('SOP 模板已保存。登记客户后才会创建待办。');
  });
  on('enroll-form', 'submit', async () => {
    const sopId = $('enroll-sop').value; if (!sopId) throw new Error('请选择已启用的 SOP 模板。');
    const body = { customer_id: selectedCustomer(), start_at: inputTime('enroll-start') };
    const data = await api(`/sops/${sopId}/enroll`, { ...body, request_key: requestKey('sopKey', { sopId, ...body }) });
    // Keep the request key after success: repeated clicks cannot create another cycle.
    await loadTasks(); notify(`${data.created ? '已创建' : '本轮已存在，共'} ${data.tasks.length} 条本地待办。修改起始时间才会创建新一轮。`);
  });
  on('tasks-refresh', 'click', async () => { await loadTasks(); await loadSops(); });
  on('task-filter', 'change', async () => { state.offsets.tasks = 0; await loadTasks(); });
  on('tasks-customer-only', 'change', async () => { state.offsets.tasks = 0; await loadTasks(); });
  pager('tasks', 'tasks-prev', 'tasks-next', loadTasks);

  function invalidateCsv() { state.csv = null; $('csv-confirm-form').classList.add('hidden'); $('csv-result').replaceChildren(); $('csv-confirm').checked = false; $('csv-confirm-ids').value = ''; }
  on('csv-text', 'input', invalidateCsv);
  on('csv-file', 'change', async () => { invalidateCsv(); const file = $('csv-file').files[0]; if (!file) return; if (file.size > 1000000) throw new Error('CSV 文件不能超过 1 MB。'); $('csv-text').value = await file.text(); });
  on('csv-preview', 'click', async () => {
    invalidateCsv(); const text = $('csv-text').value;
    const data = await api('/transactions/import/preview', { csv_text: text });
    if ($('csv-text').value !== text) return;
    state.csv = data;
    const nodes = [el('p', `预览 ${data.row_count} 笔，共 ${money(data.total_cents)}（${data.total_cents} 分）。尚未写入成交账本。`)];
    data.customers.forEach(customer => nodes.push(el('p', `客户 #${customer.id} · ${customer.display_name} · ${identityText(customer)}`)));
    const wrap = el('div', undefined, 'table-wrap'); wrap.append(table(['客户 ID', '成交单号', '金额（分）', '成交时间'], data.rows.map(row => [row.customer_id, row.reference, row.amount_cents, date(row.occurred_at)]), [0, 2])); nodes.push(wrap);
    $('csv-result').replaceChildren(...nodes); $('csv-confirm-form').classList.remove('hidden'); notify('CSV 预览已保存。请核实所有客户 ID，再显式确认整批入账。');
  });
  on('csv-confirm-form', 'submit', async () => {
    const preview = state.csv; if (!preview || !$('csv-confirm').checked) throw new Error('请先预览并确认。');
    const ids = $('csv-confirm-ids').value.split(',').map(value => value.trim());
    if (ids.some(value => !/^[1-9]\d*$/.test(value))) throw new Error('请输入正整数客户 ID，并用英文逗号分隔。');
    const result = await api(`/transactions/import/${preview.import_id}/confirm`, { confirm: true, content_hash: preview.content_hash, confirmed_customer_ids: ids.map(Number) });
    invalidateCsv(); await loadLedger(); notify(`CSV 已确认入账：新增 ${result.created} 笔，重复 ${result.duplicates} 笔。`);
  });
  on('sale-form', 'submit', async () => {
    const customerId = selectedCustomer(); const cents = $('sale-cents').value;
    if (!/^[1-9]\d*$/.test(cents) || !Number.isSafeInteger(Number(cents))) throw new Error('金额必须为整数分。');
    if (!$('sale-confirm').checked || Number($('sale-confirm-id').value) !== customerId) throw new Error('请核实客户并再次输入相同客户 ID。');
    const body = { customer_id: customerId, confirm_customer_id: Number($('sale-confirm-id').value), confirm: true,
      reference: $('sale-reference').value.trim(), amount_cents: Number(cents), currency: 'CNY', occurred_at: inputTime('sale-time'), note: $('sale-note').value };
    await api('/transactions', body); $('sale-form').reset(); $('sale-time').value = localTime(); await loadLedger(); notify(`客户 #${customerId} 的已付款成交已记入本地账本。`);
  });
  async function voidSale(transaction) {
    const id = window.prompt(`作废 ${transaction.reference}（${money(transaction.amount_cents)}）\n请输入归属客户 ID #${transaction.customer_id} 确认：`);
    if (id === null) return; if (Number(id) !== transaction.customer_id) throw new Error('确认客户 ID 不一致。');
    const reason = window.prompt('请输入作废原因（保留原记录及原因，RFM 将排除该笔）：');
    if (reason === null) return; if (!reason.trim()) throw new Error('作废原因不能为空。');
    await api(`/transactions/${transaction.id}/void`, { confirm: true, confirm_customer_id: Number(id), expected_revision: transaction.revision, reason: reason.trim() });
    await loadLedger(); notify('成交已作废，原始记录和原因已保留。');
  }
  async function loadTransactions() {
    if (!state.customer) { $('transaction-list').replaceChildren(el('p', '请在下方选择并核实客户 ID。', 'empty')); page('transactions', 0, 'transaction-count', 'transactions-prev', 'transactions-next'); return; }
    const customerId = state.customer.id; const ticket = uid(); state.tickets.transactions = ticket;
    const data = await api('/transactions?' + new URLSearchParams({ customer_id: customerId, limit: PAGE, offset: state.offsets.transactions }));
    if (state.tickets.transactions !== ticket || state.customer?.id !== customerId) return;
    $('transaction-list').replaceChildren(table(['成交单号', '实付 CNY', '整数分', '时间 / 来源', '状态 / 操作'], data.items.map(row => [row.reference, money(row.amount_cents), row.amount_cents, `${date(row.occurred_at)} / ${row.source}`, row.status === 'void' ? `已作废：${row.void_reason}` : button('作废（保留记录）', () => voidSale(row), 'danger')]), [1, 2]));
    page('transactions', data.total, 'transaction-count', 'transactions-prev', 'transactions-next');
  }
  async function loadRfm() {
    const ticket = uid(); state.tickets.rfm = ticket;
    const data = await api('/rfm?' + new URLSearchParams({ window_days: $('rfm-window').value, limit: PAGE, offset: state.offsets.rfm }));
    if (state.tickets.rfm !== ticket) return;
    $('rfm-list').replaceChildren(table(['客户 ID / 名称', 'R（天）', 'F（笔）', 'M（CNY）', 'R / F / M 分', '分层'], data.items.map(row => [ `#${row.customer_id} · ${row.display_name}`, row.recency_days ?? '无购买', row.frequency, `${money(row.monetary_cents)} / ${row.monetary_cents}分`, `${row.r_score} / ${row.f_score} / ${row.m_score}`, segmentLabel[row.segment] || row.segment ]), [1, 2, 3]));
    page('rfm', data.total, 'rfm-count', 'rfm-prev', 'rfm-next');
  }
  async function loadLedger() { await Promise.all([loadTransactions(), loadRfm()]); }
  on('ledger-refresh', 'click', loadLedger);
  on('rfm-window', 'change', async () => { state.offsets.rfm = 0; await loadRfm(); });
  pager('transactions', 'transactions-prev', 'transactions-next', loadTransactions);
  pager('rfm', 'rfm-prev', 'rfm-next', loadRfm);

  async function switchTab(name) {
    if (!['inbox', 'tasks', 'ledger'].includes(name)) name = 'inbox';
    state.tab = name;
    document.querySelectorAll('[data-tab]').forEach(node => { if (node.dataset.tab === name) node.setAttribute('aria-current', 'page'); else node.removeAttribute('aria-current'); });
    for (const tab of ['inbox', 'tasks', 'ledger']) $('view-' + tab).classList.toggle('hidden', tab !== name);
    history.replaceState(null, '', '#' + name);
    if (name === 'tasks') await Promise.all([loadTasks(), loadSops()]);
    else if (name === 'ledger') await loadLedger();
  }
  document.querySelectorAll('[data-tab]').forEach(node => node.addEventListener('click', () => run(() => switchTab(node.dataset.tab), node)));
  for (const id of ['task-due', 'enroll-start', 'sale-time']) $(id).value = localTime();
  run(async () => { await Promise.all([loadAccounts(), loadInbox(), loadCustomers()]); await switchTab(location.hash.slice(1) || 'inbox'); });
})();
