const api = require('../../utils/api')

const LEVELS = { 1: '适合 3–4 岁', 2: '适合 5 岁', 3: '适合 6 岁以上' }
const MAX_TURNS = 12 // 服务端预览最多收 12 句

// 一句话起草（?idea=）或借用别的家长的计划（?shared=）→ 预览木米会怎么聊 → 发布给孩子
// 电脑端 studio 的手机简化版：不逐字段编辑，不满意就换个说法再起草
Page({
  data: {
    child: null, idea: '', sharedId: '', basedOn: '',
    draft: null, levelText: '', drafting: true, error: '',
    turns: [], thinking: false, childLine: '', scrollTo: '',
    publishing: false,
  },

  onLoad(q) {
    this.setData({ idea: q.idea ? decodeURIComponent(q.idea) : '', sharedId: q.shared || '' })
    this.makeDraft()
  },

  async makeDraft() {
    const { idea, sharedId } = this.data
    if (!sharedId && idea.trim().length < 2) {
      this.setData({ drafting: false, error: '先写一句想法' })
      return
    }
    this.setData({ drafting: true, error: '', draft: null, turns: [] })
    try {
      const child = await api.currentChild()
      const childId = child ? child.id : null
      let draft, basedOn = ''
      if (sharedId) {
        const res = await api.post('/studio/borrow', { shared_id: sharedId, child_id: childId })
        draft = res.draft
        basedOn = res.based_on
      } else {
        draft = await api.post('/studio/draft', { idea: idea.trim(), child_id: childId })
      }
      this.setData({ child, draft, basedOn, levelText: LEVELS[draft.level] || '', drafting: false })
      wx.setNavigationBarTitle({ title: draft.name })
      this.ask('你好') // 先看它会怎么开口
    } catch (e) {
      this.setData({ drafting: false, error: e.message })
    }
  },

  onIdea(e) {
    this.setData({ idea: e.detail.value })
  },

  redraft() {
    if (this.data.drafting) return
    this.makeDraft()
  },

  onChildLine(e) {
    this.setData({ childLine: e.detail.value })
  },

  sendChildLine() {
    const text = this.data.childLine.trim()
    if (!text || this.data.thinking) return
    this.setData({ childLine: '' })
    this.ask(text)
  },

  restartPreview() {
    if (this.data.thinking) return
    this.setData({ turns: [] })
    this.ask('你好')
  },

  // 替孩子说一句，看木米按这个计划怎么接
  async ask(text) {
    const { draft, child } = this.data
    const turns = [...this.data.turns, { role: 'child', text }].slice(-(MAX_TURNS - 1))
    this.setData({ turns, thinking: true, scrollTo: '' })
    let reply
    try {
      const res = await api.post('/studio/preview', { theme: draft, child_id: child ? child.id : null, turns })
      reply = res.reply
    } catch (e) {
      reply = `（${e.message}）`
    }
    const all = [...turns, { role: 'mumi', text: reply }]
    this.setData({ turns: all, thinking: false, scrollTo: `t${all.length - 1}` })
  },

  publish() {
    const { draft, child, publishing } = this.data
    if (publishing) return
    if (!child) {
      wx.showToast({ title: '绑定设备后才能发布', icon: 'none' })
      return
    }
    wx.showModal({
      title: `发布给${child.name}？`,
      content: `下次${child.name}和木米聊天时，木米就会按「${draft.name}」陪他玩。之前的计划会被替换。`,
      confirmText: '发布',
      success: async ({ confirm }) => {
        if (!confirm) return
        this.setData({ publishing: true })
        try {
          const saved = await api.post('/studio/themes', draft)
          await api.post(`/studio/themes/${saved.theme_id}/apply`, { child_id: child.id })
          wx.showToast({ title: '已发布', icon: 'success' })
          setTimeout(() => wx.navigateBack(), 700)
        } catch (e) {
          this.setData({ publishing: false })
          wx.showToast({ title: e.message, icon: 'none' })
        }
      },
    })
  },
})
