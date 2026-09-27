// Waywise Studio：主题 JSON 的一个表单（软件文档第七章）。
// 登录 → 从模板或空白建主题 → 预览（直接和 Mumi 说几句）→ 发布给孩子。
// 接口与页面同域：https://waywise.tech/api/...（见 agent/API.md）

const $ = (id) => document.getElementById(id)

const state = {
  children: [],
  childId: null,
  editing: null, // 正在编辑的已保存主题 id；null 表示新建
  turns: [], // 预览对话 [{role: 'child'|'mumi', text}]
}

// ---- 本地存储：只存登录令牌和上次选的孩子，读写都可能失败（隐私模式）----
function load(key) {
  try { return localStorage.getItem(key) } catch { return null }
}
function save(key, value) {
  try { value == null ? localStorage.removeItem(key) : localStorage.setItem(key, value) } catch {}
}

// ---- API ----
async function api(path, { method = 'GET', body } = {}) {
  const token = load('studio-token')
  const res = await fetch(`/api${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401) {
    logout()
    throw new Error('登录已过期，请重新输入登录码')
  }
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = Array.isArray(data.detail) ? data.detail[0]?.msg : data.detail
    throw new Error(detail || `请求失败（${res.status}）`)
  }
  return data
}

// ---- 视图切换 ----
function show(view) {
  for (const v of ['login-view', 'home-view', 'edit-view']) $(v).hidden = v !== view
  $('who').hidden = view === 'login-view'
}

// ---- 登录 ----
$('login-form').addEventListener('submit', async (e) => {
  e.preventDefault()
  $('login-error').textContent = ''
  try {
    const res = await fetch('/api/studio/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: $('code').value.trim() }),
    })
    const data = await res.json()
    if (!res.ok) throw new Error(Array.isArray(data.detail) ? '请输入 6 位数字' : data.detail)
    save('studio-token', data.token)
    await enter(data.children)
  } catch (err) {
    $('login-error').textContent = err.message
  }
})

function logout() {
  save('studio-token', null)
  show('login-view')
}
$('logout').addEventListener('click', logout)

async function enter(children) {
  state.children = children || (await api('/children')).children
  const sel = $('child-select')
  sel.innerHTML = ''
  for (const c of state.children) sel.add(new Option(c.name, c.id))
  if (!state.children.length) sel.add(new Option('（还没绑定设备）', ''))
  const saved = load('studio-child')
  state.childId = state.children.some((c) => c.id === saved) ? saved : state.children[0]?.id || null
  sel.value = state.childId || ''
  await renderHome()
}
$('child-select').addEventListener('change', (e) => {
  state.childId = e.target.value || null
  save('studio-child', state.childId)
  if (!$('edit-view').hidden) restartPreview()
})

// ---- 首页：我的主题 + 模板 ----
function card(theme, badge, onClick) {
  const el = document.createElement('div')
  el.className = 'card'
  const words = theme.words.slice(0, 5).map((w) => w.en).join(' · ')
  el.innerHTML = `<div class="title"></div><div class="sub"></div>${badge ? '<span class="badge"></span>' : ''}`
  el.querySelector('.title').textContent = theme.name
  el.querySelector('.sub').textContent = `${theme.words.length} 个词：${words}…`
  if (badge) el.querySelector('.badge').textContent = badge
  el.addEventListener('click', onClick)
  return el
}

async function renderHome() {
  show('home-view')
  const [{ themes }, { templates }] = await Promise.all([api('/studio/themes'), api('/studio/templates')])
  const current = state.children.find((c) => c.id === state.childId)?.theme_id
  $('my-themes').innerHTML = ''
  for (const t of themes) {
    $('my-themes').append(card(t, t.theme_id === current ? '正在学' : '', () => openEditor(t, t.theme_id)))
  }
  $('no-themes').hidden = themes.length > 0
  $('templates').innerHTML = ''
  for (const t of templates) $('templates').append(card(t, '', () => openEditor(t, null)))
}

$('new-blank').addEventListener('click', () =>
  openEditor({ name: '', level: 1, words: [], patterns: [], scene_hint: '' }, null)
)
$('back').addEventListener('click', () => renderHome().catch(showError))

// ---- 编辑器 ----
const CJK = /[㐀-鿿]/

// 「elephant 大象 2」「dump truck, 自卸车」「giraffe\t长颈鹿」都认
function parseWords(text) {
  const words = []
  const problems = []
  text.split('\n').forEach((raw, i) => {
    const line = raw.trim()
    if (!line) return
    const at = line.search(CJK)
    if (at <= 0) {
      problems.push(`第 ${i + 1} 行需要「英文 中文」`)
      return
    }
    const en = line.slice(0, at).replace(/[\s,，、:：\-–]+$/, '').trim()
    let zh = line.slice(at).trim()
    let difficulty = 1
    const m = zh.match(/\s+([123])$/)
    if (m) {
      difficulty = Number(m[1])
      zh = zh.slice(0, m.index).trim()
    }
    if (!/^[A-Za-z]/.test(en)) problems.push(`第 ${i + 1} 行的英文不对`)
    else words.push({ en, zh, difficulty })
  })
  return { words, problems }
}

function formatWords(words) {
  return words.map((w) => `${w.en} ${w.zh}${w.difficulty > 1 ? ` ${w.difficulty}` : ''}`).join('\n')
}

function draft() {
  const { words, problems } = parseWords($('f-words').value)
  return {
    problems,
    theme: {
      name: $('f-name').value.trim(),
      level: Number($('f-level').value),
      words,
      patterns: $('f-patterns').value.split('\n').map((s) => s.trim()).filter(Boolean),
      scene_hint: $('f-scene').value.trim(),
    },
  }
}

function openEditor(theme, savedId) {
  state.editing = savedId
  $('f-name').value = theme.name
  $('f-scene').value = theme.scene_hint || ''
  $('f-level').value = String(theme.level || 1)
  $('f-words').value = formatWords(theme.words)
  $('f-patterns').value = (theme.patterns || []).join('\n')
  $('delete').hidden = !savedId
  $('edit-error').textContent = ''
  $('edit-ok').textContent = ''
  updateWordsHint()
  show('edit-view')
  restartPreview()
}

function updateWordsHint() {
  const { words, problems } = parseWords($('f-words').value)
  $('words-hint').textContent = problems.length ? problems[0] : `${words.length} 个词${words.length > 15 ? '（建议 10–15 个，孩子更容易记住）' : ''}`
}
$('f-words').addEventListener('input', updateWordsHint)

function validDraft() {
  const { theme, problems } = draft()
  if (!theme.name) return [null, '先给主题起个名字']
  if (problems.length) return [null, problems[0]]
  if (theme.words.length < 3) return [null, '至少写 3 个词']
  return [theme, '']
}

function showError(err) {
  $('edit-error').textContent = err.message || String(err)
}

async function saveTheme() {
  const [theme, problem] = validDraft()
  if (!theme) throw new Error(problem)
  const saved = state.editing
    ? await api(`/studio/themes/${state.editing}`, { method: 'PUT', body: theme })
    : await api('/studio/themes', { method: 'POST', body: theme })
  state.editing = saved.theme_id
  $('delete').hidden = false
  return saved
}

$('save').addEventListener('click', async () => {
  $('edit-error').textContent = ''
  $('edit-ok').textContent = ''
  try {
    await saveTheme()
    $('edit-ok').textContent = '已保存'
  } catch (err) {
    showError(err)
  }
})

$('publish').addEventListener('click', async () => {
  $('edit-error').textContent = ''
  $('edit-ok').textContent = ''
  if (!state.childId) {
    showError(new Error('还没有绑定设备：先在小程序里绑定，再来发布'))
    return
  }
  try {
    const saved = await saveTheme()
    await api(`/studio/themes/${saved.theme_id}/apply`, { method: 'POST', body: { child_id: state.childId } })
    const child = state.children.find((c) => c.id === state.childId)
    if (child) child.theme_id = saved.theme_id
    $('edit-ok').textContent = `已发布：${child ? child.name : '孩子'}下一次和 Mumi 说话，就会开始学「${saved.name}」`
  } catch (err) {
    showError(err)
  }
})

$('delete').addEventListener('click', async () => {
  if (!state.editing) return
  $('delete').disabled = true
  try {
    await api(`/studio/themes/${state.editing}`, { method: 'DELETE' })
    await renderHome()
  } catch (err) {
    showError(err)
  }
  $('delete').disabled = false
})

// ---- 预览 ----
function renderChat(thinking) {
  const box = $('chat')
  box.innerHTML = ''
  for (const t of state.turns) {
    const b = document.createElement('div')
    b.className = `bubble ${t.role}`
    b.textContent = t.text
    box.append(b)
  }
  if (thinking) {
    const b = document.createElement('div')
    b.className = 'bubble mumi thinking'
    b.textContent = 'Mumi 在想…'
    box.append(b)
  }
  box.scrollTop = box.scrollHeight
}

async function ask(text) {
  const [theme, problem] = validDraft()
  if (!theme) {
    state.turns.push({ role: 'mumi', text: `（${problem}，就能预览了）` })
    renderChat()
    return
  }
  state.turns.push({ role: 'child', text })
  renderChat(true)
  $('chat-input').disabled = true
  try {
    const { reply } = await api('/studio/preview', {
      method: 'POST',
      body: { theme, child_id: state.childId, turns: state.turns.filter((t) => !t.text.startsWith('（')) },
    })
    state.turns.push({ role: 'mumi', text: reply })
  } catch (err) {
    state.turns.push({ role: 'mumi', text: `（${err.message}）` })
  }
  $('chat-input').disabled = false
  renderChat()
  $('chat-input').focus()
}

function restartPreview() {
  state.turns = []
  renderChat()
  ask('你好') // 「它会怎么开口」
}
$('preview-start').addEventListener('click', restartPreview)

$('chat-form').addEventListener('submit', (e) => {
  e.preventDefault()
  const text = $('chat-input').value.trim()
  if (!text) return
  $('chat-input').value = ''
  ask(text)
})

// ---- 启动 ----
if (load('studio-token')) enter().catch(() => show('login-view'))
else show('login-view')
