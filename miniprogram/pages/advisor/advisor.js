const api = require('../../utils/api')

const CHIPS = [
  '这周他在想什么？有什么值得我留意的？',
  '他最近问题特别多，我有时答不上来，该怎么接？',
  '5 岁要不要开始学加减法？我不想让他刷题',
  '他总说「不」，我该怎么回应？',
]

Page({
  data: { messages: [], input: '', busy: false, chips: CHIPS, childId: null, scrollTo: '' },

  async onLoad(options) {
    try {
      const child = await api.currentChild()
      this.setData({ childId: child ? child.id : null })
      const q = child ? `?child_id=${child.id}` : ''
      const { messages } = await api.get(`/advisor/history${q}`)
      this.setData({ messages: messages.map((m, i) => ({ ...m, id: `m${i}` })) })
      this.scrollToEnd()
    } catch (e) {
      wx.showToast({ title: e.message, icon: 'none' })
    }
    if (options.q) this.ask(decodeURIComponent(options.q))
  },

  onInput(e) {
    this.setData({ input: e.detail.value })
  },

  send() {
    const text = this.data.input.trim()
    if (!text || this.data.busy) return
    this.setData({ input: '' })
    this.ask(text)
  },

  tapChip(e) {
    this.ask(e.currentTarget.dataset.text)
  },

  scrollToEnd() {
    const n = this.data.messages.length
    if (n) this.setData({ scrollTo: `m${n - 1}` })
  },

  push(msg) {
    const messages = this.data.messages.concat({ ...msg, id: `m${this.data.messages.length}` })
    this.setData({ messages })
    this.scrollToEnd()
  },

  // 小程序里一次性返回（网页端是流式）
  async ask(text) {
    this.setData({ busy: true })
    this.push({ role: 'parent', text })
    this.push({ role: 'advisor', text: '顾问在看孩子最近的聊天…', thinking: true })
    try {
      const r = await api.post('/advisor/chat', { text, child_id: this.data.childId })
      const messages = this.data.messages.slice(0, -1)
      this.setData({ messages })
      this.push({ role: 'advisor', text: r.text, plan_idea: r.plan_idea })
    } catch (e) {
      const messages = this.data.messages.slice(0, -1)
      this.setData({ messages })
      this.push({ role: 'advisor', text: `（${e.message}）` })
    }
    this.setData({ busy: false })
  },

  // 探索计划在电脑上做：复制这句想法，到 waywise.tech/studio 粘贴给设计师
  copyIdea(e) {
    wx.setClipboardData({
      data: e.currentTarget.dataset.idea,
      success: () => wx.showModal({
        title: '已复制这个想法',
        content: '在电脑上打开 waywise.tech/studio，粘贴到首页的输入框，设计师会把它做成探索计划。',
        showCancel: false,
      }),
    })
  },
})
