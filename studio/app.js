// Waywise Studio：家长自己的教育 agent（家长教育 Agent 方案）。
// 说一句想法 → 设计师起草探索计划 → 改一改 → 预览 Mumi 会怎么聊 → 发布给孩子。
// 接口与页面同域：https://waywise.tech/api/...（见 agent/API.md）

const $ = (id) => document.getElementById(id)

const state = {
  children: [],
  childId: null,
  editing: null, // 正在编辑的已保存计划 id；null 表示新建
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
const VIEWS = ['login-view', 'home-view', 'edit-view', 'profile-view', 'advisor-view', 'weekly-view']
function show(view) {
  for (const v of VIEWS) $(v).hidden = v !== view
  $('who').hidden = view === 'login-view'
  window.scrollTo(0, 0)
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

// 导出孩子的全部数据：对话、问过的问题、记下的事、英文词、周报、看过什么、安全记录
$('export-data').addEventListener('click', async () => {
  if (!state.childId) return alert('还没有绑定孩子。')
  try {
    const data = await api(`/children/${state.childId}/export`)
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `木米_${childName()}_${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    setTimeout(() => URL.revokeObjectURL(a.href), 1000)
  } catch (err) {
    alert(err.message)
  }
})

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
  else if (!$('home-view').hidden) renderHome().catch(() => {})
  else if (!$('advisor-view').hidden) openAdvisor().catch(() => {})
  else if (!$('weekly-view').hidden) openWeekly().catch(() => {})
})

// ---- 首页 ----
function card(theme, badge, onClick) {
  const el = document.createElement('div')
  el.className = 'card'
  const summary = theme.goal || `${theme.words.length} 个词：${theme.words.slice(0, 5).map((w) => w.en).join(' · ')}…`
  el.innerHTML = `<div class="title"></div><div class="domain"></div><div class="sub"></div>${badge ? '<span class="badge"></span>' : ''}`
  el.querySelector('.title').textContent = theme.name
  el.querySelector('.domain').textContent = theme.domain || '英语'
  el.querySelector('.sub').textContent = summary
  if (badge) el.querySelector('.badge').textContent = badge
  el.addEventListener('click', onClick)
  return el
}

const isExploration = (t) => (t.ideas && t.ideas.length) || (t.moves && t.moves.length)

async function renderHome() {
  show('home-view')
  const [{ themes }, { templates }, profile, questions, shared] = await Promise.all([
    api('/studio/themes'),
    api('/studio/templates'),
    api('/studio/profile'),
    state.childId ? api(`/studio/questions?child_id=${state.childId}`) : Promise.resolve({ questions: [] }),
    api(`/studio/shared${state.childId ? `?child_id=${state.childId}` : ''}`),
  ])
  const current = state.children.find((c) => c.id === state.childId)?.theme_id
  $('my-themes').innerHTML = ''
  for (const t of themes) $('my-themes').append(card(t, t.theme_id === current ? '正在进行' : '', () => openEditor(t, t.theme_id)))
  $('no-themes').hidden = themes.length > 0

  // 探索计划模板现在都在「别的家长在做什么」里；这里只剩英语主题模板
  $('templates-lang').innerHTML = ''
  for (const t of templates) if (!isExploration(t)) $('templates-lang').append(card(t, '', () => openEditor(t, null)))
  renderShared(shared.plans)

  const bits = [profile.values && `看重${profile.values}`, profile.avoid && `不要${profile.avoid}`].filter(Boolean)
  $('profile-summary').textContent = bits.length
    ? bits.join('；')
    : '还没填写。写下你们看重什么、不想要什么，Mumi 和设计师都会照着来。'
  $('open-profile-2').textContent = bits.length ? '修改' : '去填写'

  const ul = $('questions')
  ul.innerHTML = ''
  if (!questions.questions.length) {
    const li = document.createElement('li')
    li.className = 'muted small'
    li.textContent = state.childId ? '还没有记录到提问。' : '绑定设备后，这里会出现孩子问过的问题。'
    ul.append(li)
  }
  for (const q of questions.questions.slice(0, 15)) {
    const li = document.createElement('li')
    li.textContent = q.text
    li.title = '用这个问题开始一个探索计划'
    li.addEventListener('click', () => {
      $('ask-input').value = `他最近问「${q.text}」，想借这个问题陪他探索一下`
      $('ask-input').focus()
      window.scrollTo(0, 0)
    })
    ul.append(li)
  }
}

// ---- 别的家长在做什么：按孩子最近的问题挑几个，借用并改成自己孩子的 ----
const childName = () => state.children.find((c) => c.id === state.childId)?.name || '孩子'

const sharedReasons = new Map()

function renderShared(plans) {
  sharedReasons.clear()
  for (const p of plans) sharedReasons.set(p.id, p.reason)
  const box = $('shared')
  box.innerHTML = ''
  for (const p of plans) {
    const el = document.createElement('div')
    el.className = 'card'
    el.innerHTML = '<div class="title"></div><div class="domain"></div><div class="sub"></div><div class="reason"></div><div class="author"></div>'
    el.querySelector('.title').textContent = p.name
    el.querySelector('.domain').textContent = p.domain
    el.querySelector('.sub').textContent = p.goal
    el.querySelector('.reason').textContent = p.reason
    el.querySelector('.reason').hidden = !p.reason
    el.querySelector('.author').textContent = p.author
    el.addEventListener('click', () => openShared(p.id).catch(showError))
    box.append(el)
  }
  // 输入框下面：没想好的时候，给三个和孩子最相关的
  const chips = $('ask-suggest-chips')
  chips.innerHTML = ''
  for (const p of plans.slice(0, 3)) {
    const b = document.createElement('button')
    b.type = 'button'
    b.textContent = p.name
    b.title = p.reason || p.goal
    b.addEventListener('click', () => openShared(p.id).catch(showError))
    chips.append(b)
  }
  $('ask-suggest').hidden = !plans.length || $('ask-input').value.trim() !== ''
}

$('ask-input').addEventListener('input', () => {
  $('ask-suggest').hidden = $('ask-suggest-chips').children.length === 0 || $('ask-input').value.trim() !== ''
})

let openedShared = null

async function openShared(id) {
  const d = await api(`/studio/shared/${id}`)
  openedShared = d
  $('sd-domain').textContent = d.domain
  $('sd-name').textContent = d.name
  $('sd-author').textContent = d.author
  $('sd-reason').textContent = sharedReasons.get(id) || ''
  $('sd-why').textContent = d.why
  $('sd-reaction').textContent = d.reaction
  $('sd-reaction-block').hidden = !d.reaction
  $('sd-goal').textContent = d.plan.goal
  $('sd-moves').innerHTML = ''
  for (const m of d.plan.moves) {
    const li = document.createElement('li')
    li.textContent = `${m.type}：${m.prompt}`
    $('sd-moves').append(li)
  }
  $('sd-borrow').textContent = `借用，改成${childName()}的`
  $('sd-borrow').disabled = false
  $('sd-status').textContent = ''
  $('sd-error').textContent = ''
  $('shared-dialog').showModal()
}

$('sd-borrow').addEventListener('click', async () => {
  if (!openedShared) return
  $('sd-borrow').disabled = true
  $('sd-error').textContent = ''
  $('sd-status').textContent = `正在按${childName()}改写，大约十几秒…`
  try {
    const r = await api('/studio/borrow', { method: 'POST', body: { shared_id: openedShared.id, child_id: state.childId } })
    $('shared-dialog').close()
    openEditor(r.draft, null)
    $('edit-ok').textContent = `这是按${childName()}改过的版本（原计划：${r.based_on}）。改一改、在右边预览，满意了再发布。`
  } catch (err) {
    $('sd-error').textContent = err.message
  }
  $('sd-borrow').disabled = false
  $('sd-status').textContent = ''
})

$('sd-view').addEventListener('click', () => {
  if (!openedShared) return
  $('shared-dialog').close()
  openEditor(openedShared.plan, null)
})

$('new-blank').addEventListener('click', () =>
  openEditor({ name: '', level: 1, domain: '自然', goal: '', ideas: [], moves: [], words: [], patterns: [], scene_hint: '', avoid: '' }, null)
)
$('back').addEventListener('click', () => renderHome().catch(showError))

// ---- 设计师：一句话 → 探索计划 ----
async function draftFromIdea(idea) {
  const draft = await api('/studio/draft', { method: 'POST', body: { idea, child_id: state.childId } })
  openEditor(draft, null)
  $('edit-ok').textContent = '这是设计师起草的计划，改一改、在右边预览，满意了再发布。'
}

$('ask-form').addEventListener('submit', async (e) => {
  e.preventDefault()
  const idea = $('ask-input').value.trim()
  if (idea.length < 2) return
  $('ask-error').textContent = ''
  $('ask-btn').disabled = true
  $('ask-status').textContent = '设计师正在起草，大约十几秒…'
  try {
    await draftFromIdea(idea)
    $('ask-input').value = ''
  } catch (err) {
    $('ask-error').textContent = err.message
  }
  $('ask-btn').disabled = false
  $('ask-status').textContent = ''
})

// ---- 顾问 ----
const ADVISOR_CHIPS = [
  '这周他在想什么？有什么值得我留意的？',
  '他最近问题特别多，我有时答不上来，该怎么接？',
  '5 岁要不要开始学加减法？我不想让他刷题',
  '他总说「不」，我该怎么回应？',
]

function advisorBubble(role, text, planIdea) {
  const el = document.createElement('div')
  el.className = `msg ${role}`
  const body = document.createElement('div')
  body.textContent = text
  el.append(body)
  if (planIdea) addPlanButton(el, planIdea)
  $('advisor-log').append(el)
  el.scrollIntoView({ block: 'end' })
  return body
}

function addPlanButton(el, idea) {
  const btn = document.createElement('button')
  btn.className = 'plan-btn'
  btn.textContent = `把「${idea}」做成探索计划 ›`
  btn.addEventListener('click', async () => {
    btn.disabled = true
    btn.textContent = '设计师正在起草，大约十几秒…'
    try {
      await draftFromIdea(idea)
    } catch (err) {
      btn.disabled = false
      btn.textContent = `起草失败：${err.message}，再试一次`
    }
  })
  el.append(btn)
}

async function openAdvisor() {
  show('advisor-view')
  $('advisor-error').textContent = ''
  const q = state.childId ? `?child_id=${state.childId}` : ''
  const { messages } = await api(`/advisor/history${q}`)
  $('advisor-log').innerHTML = ''
  for (const m of messages) advisorBubble(m.role, m.text, m.plan_idea)
  $('advisor-chips').innerHTML = ''
  for (const c of ADVISOR_CHIPS) {
    const b = document.createElement('button')
    b.textContent = c
    b.addEventListener('click', () => askAdvisor(c))
    $('advisor-chips').append(b)
  }
  $('advisor-chips').hidden = messages.length > 0
}

// 流式读顾问的回答：服务端发 data: {"delta"} … 最后 data: {"done", text, plan_idea}
async function askAdvisor(text) {
  $('advisor-error').textContent = ''
  $('advisor-chips').hidden = true
  $('advisor-send').disabled = true
  advisorBubble('parent', text)
  const body = advisorBubble('advisor', '顾问在看孩子最近的聊天…')
  const bubble = body.parentElement
  try {
    const res = await fetch('/api/advisor/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${load('studio-token')}` },
      body: JSON.stringify({ text, child_id: state.childId, stream: true }),
    })
    if (res.status === 401) return logout()
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `请求失败（${res.status}）`)
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    let shown = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let i
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const line = buf.slice(0, i).replace(/^data: /, '')
        buf = buf.slice(i + 2)
        const evt = JSON.parse(line)
        if (evt.error) throw new Error(evt.error)
        if (evt.delta) {
          shown += evt.delta
          body.textContent = shown.replace(/\n?探索计划建议[：:][^\n]*$/, '')
          bubble.scrollIntoView({ block: 'end' })
        }
        if (evt.done) {
          body.textContent = evt.text
          if (evt.plan_idea) addPlanButton(bubble, evt.plan_idea)
        }
      }
    }
  } catch (err) {
    body.textContent = `（${err.message}）`
  }
  $('advisor-send').disabled = false
}

$('advisor-form').addEventListener('submit', (e) => {
  e.preventDefault()
  const text = $('advisor-input').value.trim()
  if (!text) return
  $('advisor-input').value = ''
  askAdvisor(text)
})
$('advisor-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    $('advisor-form').requestSubmit()
  }
})
$('advisor-clear').addEventListener('click', async () => {
  const q = state.childId ? `?child_id=${state.childId}` : ''
  await api(`/advisor/history${q}`, { method: 'DELETE' }).catch(() => {})
  openAdvisor().catch(() => {})
})
$('open-advisor').addEventListener('click', () => openAdvisor().catch(() => {}))
$('open-advisor-2').addEventListener('click', () => openAdvisor().catch(() => {}))
$('advisor-back').addEventListener('click', () => renderHome().catch(() => {}))

// ---- 好奇心周报 ----
function block(title, build) {
  const el = document.createElement('div')
  el.className = 'weekly-block'
  const h = document.createElement('h3')
  h.textContent = title
  el.append(h)
  build(el)
  return el
}

async function openWeekly() {
  show('weekly-view')
  const box = $('weekly-body')
  if (!state.childId) {
    box.innerHTML = '<p class="muted">绑定设备后，每周会有一份孩子的好奇心周报。</p>'
    return
  }
  box.innerHTML = '<p class="muted">顾问正在读这一周的聊天，大约十几秒…</p>'
  try {
    const r = await api(`/children/${state.childId}/weekly`)
    $('weekly-title').textContent = `${r.child.name}的好奇心周报 · ${r.week_start} 这一周`
    box.innerHTML = ''
    const stats = document.createElement('div')
    stats.className = 'weekly-stats'
    for (const [n, label] of [[r.stats.sessions, '次聊天'], [r.stats.minutes, '分钟'], [r.stats.child_turns, '句话'], [r.stats.questions, '个问题']]) {
      const d = document.createElement('div')
      d.innerHTML = `<b></b><span class="muted small"></span>`
      d.querySelector('b').textContent = n
      d.querySelector('span').textContent = label
      stats.append(d)
    }
    box.append(stats)
    const summary = document.createElement('p')
    summary.textContent = r.summary
    box.append(summary)
    if (r.empty) return
    if (r.topics.length) box.append(block('他在问什么', (el) => {
      for (const t of r.topics) {
        const p = document.createElement('p')
        p.innerHTML = '<b></b> <span class="muted"></span>'
        p.querySelector('b').textContent = t.topic
        p.querySelector('span').textContent = t.questions.map((q) => `「${q}」`).join(' ')
        el.append(p)
      }
    }))
    if (r.highlights.length) box.append(block('值得记下来的原话', (el) => {
      for (const h of r.highlights) {
        const q = document.createElement('p')
        q.className = 'quote'
        q.textContent = `“${h.quote}”`
        const why = document.createElement('p')
        why.className = 'muted small'
        why.textContent = h.why
        el.append(q, why)
      }
    }))
    if (r.notice.length) box.append(block('可以留意的', (el) => {
      const ul = document.createElement('ul')
      for (const n of r.notice) {
        const li = document.createElement('li')
        li.textContent = n
        ul.append(li)
      }
      el.append(ul)
    }))
    if (r.next.length) box.append(block('下周可以怎么接着玩', (el) => {
      for (const n of r.next) {
        const p = document.createElement('p')
        p.textContent = n.idea
        el.append(p)
        if (n.plan_idea) addPlanButton(el, n.plan_idea)
      }
    }))
  } catch (err) {
    box.innerHTML = ''
    const p = document.createElement('p')
    p.className = 'error'
    p.textContent = err.message
    box.append(p)
  }
}
$('open-weekly').addEventListener('click', () => openWeekly().catch(() => {}))
$('weekly-back').addEventListener('click', () => renderHome().catch(() => {}))

// ---- 教育理念 ----
async function openProfile() {
  const p = await api('/studio/profile')
  $('p-values').value = p.values
  $('p-avoid').value = p.avoid
  $('p-language').value = p.language
  $('p-traits').value = p.child_traits
  $('profile-ok').textContent = ''
  $('profile-error').textContent = ''
  show('profile-view')
}
$('open-profile').addEventListener('click', () => openProfile().catch(() => {}))
$('open-profile-2').addEventListener('click', () => openProfile().catch(() => {}))
$('profile-back').addEventListener('click', () => renderHome().catch(() => {}))
$('profile-save').addEventListener('click', async () => {
  $('profile-error').textContent = ''
  try {
    await api('/studio/profile', {
      method: 'PUT',
      body: {
        values: $('p-values').value.trim(),
        avoid: $('p-avoid').value.trim(),
        language: $('p-language').value.trim(),
        child_traits: $('p-traits').value.trim(),
      },
    })
    $('profile-ok').textContent = '已保存。Mumi 从下一句话开始就会照着来。'
  } catch (err) {
    $('profile-error').textContent = err.message
  }
})

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
      problems.push(`英文词第 ${i + 1} 行需要「英文 中文」`)
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
    if (!/^[A-Za-z]/.test(en)) problems.push(`英文词第 ${i + 1} 行的英文不对`)
    else words.push({ en, zh, difficulty })
  })
  return { words, problems }
}

// 「地球在转 | 地球在慢慢转圈圈」
function parseIdeas(text) {
  return text.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => {
    const [idea, ...rest] = l.split(/\s*[|｜]\s*/)
    return { idea: idea.slice(0, 60), kid_words: rest.join('，').slice(0, 80) }
  })
}

// 「猜一猜：你觉得……」；没写类型就算「聊一聊」
function parseMoves(text) {
  return text.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => {
    const m = l.match(/^([^：:]{1,10})[：:]\s*(.+)$/)
    return m ? { type: m[1].trim(), prompt: m[2].trim().slice(0, 100) } : { type: '聊一聊', prompt: l.slice(0, 100) }
  })
}

const lines = (text) => text.split('\n').map((s) => s.trim()).filter(Boolean)

function draft() {
  const { words, problems } = parseWords($('f-words').value)
  return {
    problems,
    theme: {
      name: $('f-name').value.trim(),
      domain: $('f-domain').value,
      level: Number($('f-level').value),
      goal: $('f-goal').value.trim(),
      ideas: parseIdeas($('f-ideas').value),
      moves: parseMoves($('f-moves').value),
      words,
      patterns: lines($('f-patterns').value),
      scene_hint: $('f-scene').value.trim(),
      avoid: $('f-avoid').value.trim(),
    },
  }
}

function openEditor(theme, savedId) {
  state.editing = savedId
  $('f-name').value = theme.name || ''
  $('f-domain').value = theme.domain || '英语'
  $('f-level').value = String(theme.level || 1)
  $('f-goal').value = theme.goal || ''
  $('f-ideas').value = (theme.ideas || []).map((i) => (i.kid_words ? `${i.idea} | ${i.kid_words}` : i.idea)).join('\n')
  $('f-moves').value = (theme.moves || []).map((m) => `${m.type}：${m.prompt}`).join('\n')
  $('f-words').value = (theme.words || []).map((w) => `${w.en} ${w.zh}${w.difficulty > 1 ? ` ${w.difficulty}` : ''}`).join('\n')
  $('f-patterns').value = (theme.patterns || []).join('\n')
  $('f-scene').value = theme.scene_hint || ''
  $('f-avoid').value = theme.avoid || ''
  $('delete').hidden = !savedId
  $('edit-error').textContent = ''
  $('edit-ok').textContent = ''
  updateWordsHint()
  show('edit-view')
  restartPreview()
}

function updateWordsHint() {
  const { words, problems } = parseWords($('f-words').value)
  $('words-hint').textContent = problems.length ? problems[0] : words.length ? `${words.length} 个英文词` : ''
}
$('f-words').addEventListener('input', updateWordsHint)

function validDraft() {
  const { theme, problems } = draft()
  if (!theme.name) return [null, '先给它起个名字']
  if (problems.length) return [null, problems[0]]
  if (!theme.ideas.length && !theme.moves.length && theme.words.length < 3) {
    return [null, '写几个要点或探索动作；如果是英语主题，至少写 3 个英文词']
  }
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
    $('edit-ok').textContent = `已发布：${child ? child.name : '孩子'}下一次和 Mumi 说话，就会开始「${saved.name}」`
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
