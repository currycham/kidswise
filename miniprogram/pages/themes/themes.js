const api = require('../../utils/api')

Page({
  data: { child: null, themes: [], loading: true, error: '', loginCode: '' },

  onShow() {
    this.load()
  },

  async load() {
    try {
      const [child, res] = await Promise.all([api.currentChild(), api.get('/themes')])
      const themes = res.themes.map((t) => ({ ...t, sampleText: t.sample.join(' · ') }))
      this.setData({ loading: false, error: '', child, themes })
    } catch (e) {
      this.setData({ loading: false, error: e.message })
    }
  },

  async getLoginCode() {
    try {
      const { code } = await api.post('/studio/login-code')
      this.setData({ loginCode: code, error: '' })
      wx.setClipboardData({ data: 'waywise.tech/studio', success: () => wx.hideToast() })
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
      content: `下次${child.name}和 Mumi 聊天时，Mumi 会开始教这个主题的词。`,
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
