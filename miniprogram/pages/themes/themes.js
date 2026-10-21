const api = require('../../utils/api')

Page({
  data: {
    child: null, themes: [], shared: [], loading: true, error: '', loginCode: '', idea: '',
    examples: ['想让他对数字有感觉，但不要做题', '下周去动物园，想提前聊聊', '他最近总说「不」，想让他学会表达感受'],
  },

  onShow() {
    this.load()
  },

  async load() {
    try {
      const [child, res] = await Promise.all([api.currentChild(), api.get('/themes')])
      // 探索计划显示目标，英语主题显示几个示例词
      const themes = res.themes.map((t) => ({ ...t, sampleText: t.goal || `${t.sample.join(' · ')} …` }))
      this.setData({ loading: false, error: '', child, themes })
      this.loadShared(child)
    } catch (e) {
      this.setData({ loading: false, error: e.message })
    }
  },

  // 别的家长的计划只是推荐，拿不到不影响这一页
  async loadShared(child) {
    try {
      const q = child ? `?child_id=${child.id}&limit=3` : '?limit=3'
      const { plans } = await api.get(`/studio/shared${q}`)
      this.setData({ shared: plans })
    } catch (e) {
      this.setData({ shared: [] })
    }
  },

  onIdea(e) {
    this.setData({ idea: e.detail.value })
  },

  useExample(e) {
    this.setData({ idea: e.currentTarget.dataset.text })
  },

  draft() {
    const idea = this.data.idea.trim()
    if (idea.length < 2) {
      wx.showToast({ title: '先写一句想法', icon: 'none' })
      return
    }
    wx.navigateTo({ url: `/pages/plan/plan?idea=${encodeURIComponent(idea)}` })
  },

  borrow(e) {
    wx.navigateTo({ url: `/pages/plan/plan?shared=${e.currentTarget.dataset.id}` })
  },

  async getLoginCode() {
    try {
      const { code } = await api.post('/studio/login-code')
      this.setData({ loginCode: code, error: '' })
      wx.setClipboardData({ data: 'kidswise.waywise.tech/studio', success: () => wx.hideToast() })
    } catch (e) {
      this.setData({ error: e.message })
    }
  },

  choose(e) {
    const theme = e.currentTarget.dataset.theme
    const { child } = this.data
    if (!child || theme.id === child.theme_id) return
    wx.showModal({
      title: `换成「${theme.name}」？`,
      content: `下次${child.name}和木米聊天时，木米会按这个陪他玩。`,
      confirmText: '换',
      success: async ({ confirm }) => {
        if (!confirm) return
        try {
          const updated = await api.patch(`/children/${child.id}`, { theme_id: theme.id })
          this.setData({ child: updated })
          wx.showToast({ title: '已切换', icon: 'success' })
        } catch (err) {
          this.setData({ error: err.message })
        }
      },
    })
  },
})
