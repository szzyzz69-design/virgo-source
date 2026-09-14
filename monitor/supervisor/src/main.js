import './style.css';

const app = document.querySelector('#app');
const esc = value => String(value ?? '').replace(
  /[&<>"']/g,
  character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]),
);
const when = value => value
  ? new Intl.DateTimeFormat('zh-CN', {dateStyle: 'short', timeStyle: 'short'}).format(new Date(value))
  : '暂无消息';
const labels = {waiting: '待回复', replied: '已回复', failed: '回复发送失败'};
const messageTimeText = message => message.direction === 'INBOUND'
  ? `接收时间：${when(message.receivedAt || message.createdAt)}`
  : `发送时间：${when(message.sentAt || message.createdAt)}`;

let csrfToken = '';
let areas = [];
let selection = null;
let items = [];
let cursor = null;
let expanded = false;
let listController = null;
let detail = null;
let detailMessages = [];
let before = null;
let nearBottom = true;
let unseenMessages = 0;
const expandedAreas = new Set();
const expandedAccounts = new Set();

async function api(path, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const headers = {'Content-Type': 'application/json', ...(options.headers || {})};
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && path !== '/login') {
    headers['X-CSRF-Token'] = csrfToken;
  }
  const response = await fetch('/supervisor/api' + path, {
    credentials: 'same-origin',
    ...options,
    method,
    headers,
  });
  if (response.status === 401) {
    login();
    throw new Error('登录已过期，请重新登录');
  }
  if (!response.ok) {
    let body = {};
    try { body = await response.json(); } catch {}
    throw new Error(body.message || body.detail || `请求失败 (${response.status})`);
  }
  return response.json();
}

function login(message = '') {
  csrfToken = '';
  app.innerHTML = `<main class="login"><form id="login"><div class="brand">Virgo</div><h1>客服监视</h1><p>第一版只读模式</p>${message ? `<div class="error">${esc(message)}</div>` : ''}<label>用户名<input name="username" autocomplete="username" required></label><label>密码<input name="password" type="password" autocomplete="current-password" required></label><button>登录</button></form></main>`;
  document.querySelector('#login').onsubmit = async event => {
    event.preventDefault();
    try {
      const result = await api('/login', {
        method: 'POST',
        body: JSON.stringify(Object.fromEntries(new FormData(event.currentTarget))),
      });
      csrfToken = result.csrfToken;
      await boot();
    } catch (error) {
      login(error.message);
    }
  };
}

function shell() {
  app.innerHTML = `<header><b>Virgo 客服监视</b><span>严格只读模式</span><button id="logout" class="ghost">退出</button></header><main class="layout"><aside><h2>地区、客服与接收号码</h2><div id="agents"></div></aside><section><div class="toolbar"><button id="back" class="mobile ghost">← 返回</button><input id="search" placeholder="搜索客户手机号或 Remark"><label><input id="waiting" type="checkbox"> 只看待回复</label><button id="toggle" class="ghost">展开全部</button></div><div id="content"><div class="empty">请从左侧选择客服账号或接收号码</div></div></section></main><div id="drawer"></div>`;
  document.querySelector('#logout').onclick = async () => {
    try { await api('/logout', {method: 'POST'}); } finally { login(); }
  };
  document.querySelector('#search').oninput = debounce(() => loadConversations(true), 350);
  document.querySelector('#waiting').onchange = () => loadConversations(true);
  document.querySelector('#toggle').onclick = () => {
    expanded = !expanded;
    document.querySelector('#toggle').textContent = expanded ? '收起' : '展开全部';
    loadConversations(true);
  };
  document.querySelector('#back').onclick = () => document.querySelector('.layout').classList.remove('show-content');
}

function renderAgents() {
  document.querySelector('#agents').innerHTML = areas.map((area, areaIndex) => {
    const areaKey = area.area ?? '__none__';
    const areaOpen = expandedAreas.has(areaKey);
    const accounts = area.accounts.map(account => {
      const accountOpen = expandedAccounts.has(account.id);
      const sims = account.simCards.map(sim => `<button data-account="${esc(account.id)}" data-area-index="${areaIndex}" data-sim="${esc(sim.id)}" class="pick sim"><strong>${esc(sim.phoneNumber || '号码未填写')}${sim.note ? ` · ${esc(sim.note)}` : ''}</strong><small>今日客户 ${sim.todayCustomers} · 待回复 ${sim.waiting}</small></button>`).join('') || '<p class="muted">未绑定 SIM</p>';
      return `<div class="agent"><button data-account="${esc(account.id)}" data-area-index="${areaIndex}" class="pick account-pick"><strong>${accountOpen ? '▼' : '▶'} ${esc(account.username)}</strong><small>今日客户 ${account.todayCustomers} · 待回复 ${account.waiting}</small></button><div class="account-sims ${accountOpen ? '' : 'hidden'}">${sims}</div></div>`;
    }).join('');
    return `<div class="area-node"><button class="area-toggle" data-area-index="${areaIndex}"><strong>${areaOpen ? '▼' : '▶'} ${esc(area.area || '未分地区')}</strong><small>今日客户 ${area.todayCustomers} · 待回复 ${area.waiting}</small></button><div class="area-children ${areaOpen ? '' : 'hidden'}">${accounts}</div></div>`;
  }).join('') || '<div class="empty">暂无启用客服账号</div>';

  document.querySelectorAll('.area-toggle').forEach(button => {
    button.onclick = () => {
      const area = areas[Number(button.dataset.areaIndex)];
      const key = area.area ?? '__none__';
      expandedAreas.has(key) ? expandedAreas.delete(key) : expandedAreas.add(key);
      renderAgents();
    };
  });
  document.querySelectorAll('.account-pick').forEach(button => {
    button.classList.toggle('active', button.dataset.account === selection?.account && !selection?.sim);
    button.onclick = () => {
      const area = areas[Number(button.dataset.areaIndex)];
      expandedAreas.add(area.area ?? '__none__');
      expandedAccounts.has(button.dataset.account)
        ? expandedAccounts.delete(button.dataset.account)
        : expandedAccounts.add(button.dataset.account);
      selection = {account: button.dataset.account, sim: null};
      renderAgents();
      document.querySelector('.layout').classList.add('show-content');
      loadConversations(true);
    };
  });
  document.querySelectorAll('.sim').forEach(button => {
    button.classList.toggle('active', button.dataset.account === selection?.account && button.dataset.sim === selection?.sim);
    button.onclick = () => {
      const area = areas[Number(button.dataset.areaIndex)];
      expandedAreas.add(area.area ?? '__none__');
      expandedAccounts.add(button.dataset.account);
      selection = {account: button.dataset.account, sim: button.dataset.sim};
      renderAgents();
      document.querySelector('.layout').classList.add('show-content');
      loadConversations(true);
    };
  });
}

async function refreshAgents() {
  areas = await api('/agents');
  renderAgents();
}

async function loadConversations(reset = false) {
  if (!selection) return;
  if (listController) listController.abort();
  listController = new AbortController();
  if (reset) { items = []; cursor = null; }
  const query = new URLSearchParams({
    account_id: selection.account,
    status: document.querySelector('#waiting').checked ? 'waiting' : 'all',
    search: document.querySelector('#search').value,
    limit: expanded ? '20' : '5',
  });
  if (selection.sim) query.set('sim_card_id', selection.sim);
  if (!reset && cursor) query.set('cursor', cursor);
  if (reset) document.querySelector('#content').innerHTML = '<div class="empty">加载中…</div>';
  try {
    const data = await api('/conversations?' + query, {signal: listController.signal});
    items = reset ? data.items : [...items, ...data.items];
    cursor = data.nextCursor;
    renderItems();
  } catch (error) {
    if (error.name !== 'AbortError') {
      document.querySelector('#content').innerHTML = `<div class="error">${esc(error.message)}</div>`;
    }
  }
}

function renderItems() {
  document.querySelector('#content').innerHTML = items.map(conversation => {
    const message = conversation.lastMessage;
    const title = conversation.customerRemark
      ? `${esc(conversation.customerRemark)} <small>（${esc(conversation.customerPhoneNumber)}）</small>`
      : esc(conversation.customerPhoneNumber);
    const preview = message
      ? `${message.direction === 'INBOUND' ? '客户' : '客服'}：${esc(message.text || '[无文本内容]')}`
      : '暂无消息';
    const state = `<span class="badge ${conversation.replyStatus}">${labels[conversation.replyStatus]}</span>`;
    return `<article class="conversation" data-id="${esc(conversation.id)}" data-account="${esc(conversation.accountId)}"><div class="title"><strong>${title}</strong><time>${when(conversation.lastMessageAt)}</time></div><div class="conversation-preview"><span>${preview}</span>${state}</div><p>接收：${esc(conversation.servicePhoneNumber || '号码未填写')}${conversation.note ? ` · ${esc(conversation.note)}` : ''} · ${esc(conversation.accountUsername)}</p></article>`;
  }).join('') || '<div class="empty">没有符合条件的会话</div>';
  if (cursor && expanded) {
    document.querySelector('#content').insertAdjacentHTML('beforeend', '<button id="more" class="more">加载更多</button>');
    document.querySelector('#more').onclick = () => loadConversations(false);
  }
  document.querySelectorAll('.conversation').forEach(card => {
    card.onclick = () => openDetail(card.dataset.id, card.dataset.account);
  });
}

async function openDetail(id, accountId) {
  if (!accountId) return;
  history.pushState({}, '', `/supervisor/conversations/${encodeURIComponent(id)}?account_id=${encodeURIComponent(accountId)}`);
  document.querySelector('#drawer').innerHTML = '<div class="drawer"><div class="empty">加载中…</div></div>';
  detail = null;
  detailMessages = [];
  try {
    const accountQuery = `account_id=${encodeURIComponent(accountId)}`;
    const [conversation, messages] = await Promise.all([
      api(`/conversations/${encodeURIComponent(id)}?${accountQuery}`),
      api(`/conversations/${encodeURIComponent(id)}/messages?${accountQuery}&limit=50`),
    ]);
    detail = conversation;
    detailMessages = messages.items;
    before = messages.nextBefore;
    renderDetail();
  } catch (error) {
    document.querySelector('#drawer').innerHTML = `<div class="drawer"><div class="error">${esc(error.message)}</div></div>`;
  }
}

const detailTitle = () => detail?.customerRemark
  ? `${detail.customerRemark}（${detail.customerPhoneNumber}）`
  : detail?.customerPhoneNumber || '';

function renderDetail() {
  document.querySelector('#drawer').innerHTML = `<div class="scrim" id="close"></div><div class="drawer chat-drawer"><div class="detail-head"><button id="close2" class="ghost">← 返回</button><div class="detail-title"><h2 id="detail-title">${esc(detailTitle())}</h2><p>Responsible phone: ${esc(detail.servicePhoneNumber || '号码未填写')}${detail.note ? ` · ${esc(detail.note)}` : ''} · ${esc(detail.accountUsername)}</p></div><span class="readonly-badge">只读</span><button id="reload" class="ghost">刷新</button></div>${before ? '<button id="older" class="more">加载更早消息</button>' : ''}<button id="new-message" class="new-message hidden">有新消息</button><div class="messages" id="messages"></div></div>`;
  const close = () => {
    detail = null;
    document.querySelector('#drawer').innerHTML = '';
    history.pushState({}, '', '/supervisor/');
  };
  document.querySelector('#close').onclick = close;
  document.querySelector('#close2').onclick = close;
  document.querySelector('#reload').onclick = () => refreshDetail(true);
  document.querySelector('#older')?.addEventListener('click', loadOlder);
  document.querySelector('#new-message').onclick = () => scrollToBottom(true);
  renderMessages(true);
}

function renderMessages(initial = false) {
  const container = document.querySelector('#messages');
  if (!container) return;
  const previousHeight = container.scrollHeight;
  const previousTop = container.scrollTop;
  container.innerHTML = detailMessages.map(message => `<div class="daymsg ${message.direction === 'INBOUND' ? 'in' : 'out'}"><b>${message.direction === 'INBOUND' ? '客户' : '客服'}</b><div>${esc(message.text || '[无文本内容]')}</div><small>${messageTimeText(message)}${message.direction === 'OUTBOUND' ? ` · 状态：${esc(message.state)}` : ''}${message.deliveredAt ? ` · 送达时间：${when(message.deliveredAt)}` : ''}</small>${message.errorMessage ? `<em>${esc(message.errorMessage)}</em>` : ''}</div>`).join('') || '<div class="empty">该会话暂无消息</div>';
  container.onscroll = () => {
    nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 80;
    if (nearBottom) {
      unseenMessages = 0;
      document.querySelector('#new-message')?.classList.add('hidden');
    }
  };
  if (initial || nearBottom) requestAnimationFrame(() => scrollToBottom(false));
  else container.scrollTop = previousTop + (container.scrollHeight - previousHeight);
}

function scrollToBottom(smooth) {
  const container = document.querySelector('#messages');
  if (!container) return;
  container.scrollTo({top: container.scrollHeight, behavior: smooth ? 'smooth' : 'auto'});
  unseenMessages = 0;
  nearBottom = true;
  document.querySelector('#new-message')?.classList.add('hidden');
}

async function refreshDetail(showNotice = false) {
  if (!detail) return;
  const id = detail.id;
  const oldIds = new Set(detailMessages.map(message => message.id));
  try {
    if (showNotice) toast('正在刷新…');
    const accountQuery = `account_id=${encodeURIComponent(detail.accountId)}`;
    const [conversation, messages] = await Promise.all([
      api(`/conversations/${encodeURIComponent(id)}?${accountQuery}`),
      api(`/conversations/${encodeURIComponent(id)}/messages?${accountQuery}&limit=50`),
    ]);
    if (!detail || detail.id !== id) return;
    detail = conversation;
    document.querySelector('#detail-title').textContent = detailTitle();
    const older = detailMessages.filter(message => !messages.items.some(item => item.id === message.id));
    detailMessages = [...older, ...messages.items].sort(
      (left, right) => left.createdAt - right.createdAt || left.id.localeCompare(right.id),
    );
    const added = messages.items.filter(message => !oldIds.has(message.id)).length;
    if (added && !nearBottom) {
      unseenMessages += added;
      const button = document.querySelector('#new-message');
      button.textContent = `有 ${unseenMessages} 条新消息`;
      button.classList.remove('hidden');
    }
    renderMessages(false);
  } catch (error) {
    if (showNotice) toast(error.message, true);
  }
}

async function loadOlder() {
  if (!detail || !before) return;
  try {
    const data = await api(`/conversations/${encodeURIComponent(detail.id)}/messages?account_id=${encodeURIComponent(detail.accountId)}&limit=50&before=${before}`);
    before = data.nextBefore;
    detailMessages = [...data.items, ...detailMessages];
    renderMessages(false);
    if (!before) document.querySelector('#older')?.remove();
  } catch (error) {
    toast(error.message, true);
  }
}

function toast(message, error = false) {
  let element = document.querySelector('#toast');
  if (!element) {
    element = document.createElement('div');
    element.id = 'toast';
    document.body.appendChild(element);
  }
  element.className = error ? 'toast toast-error' : 'toast';
  element.textContent = message;
  clearTimeout(element.timer);
  element.timer = setTimeout(() => element.remove(), 3500);
}

function debounce(callback, delay) {
  let timer;
  return () => {
    clearTimeout(timer);
    timer = setTimeout(callback, delay);
  };
}

async function boot() {
  try {
    const me = await api('/me');
    csrfToken = me.csrfToken;
    if (!document.querySelector('.layout')) shell();
    await refreshAgents();
    const match = location.pathname.match(/\/supervisor\/conversations\/([^/]+)/);
    const accountId = new URLSearchParams(location.search).get('account_id');
    if (match && accountId && !detail) await openDetail(decodeURIComponent(match[1]), accountId);
  } catch (error) {
    if (!document.querySelector('#login')) login(error.message);
  }
}

setInterval(() => {
  if (!document.hidden && document.querySelector('.layout')) {
    if (selection) loadConversations(true);
    refreshAgents().catch(() => {});
  }
}, 10000);
setInterval(() => {
  if (!document.hidden && detail) refreshDetail(false);
}, 5000);
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) {
    if (selection) loadConversations(true);
    if (detail) refreshDetail(false);
  }
});

boot();
