const api = require('../../utils/api')

Page({
  data: { loading: true, error: '', r: null },

  onLoad() {
    this.load()
  },

  async load() {
    this.setData({ loading: true, error: '' })
    try {
      const child = await api.currentChild()
      if (!child) {
        this.setData({ loading: false, error: '还没有绑定设备' })
        return
      }
      const r = await api.get(`/children/${child.id}/weekly`)
      wx.setNavigationBarTitle({ title: `${r.child.name}的好奇心周报` })
      this.setData({ loading: false, r })
    } catch (e) {
      this.setData({ loading: false, error: e.message })
    }
  },

  // 周报里的建议 → 带着这句话去问顾问
  askAdvisor(e) {
    const idea = e.currentTarget.dataset.idea
    wx.navigateTo({ url: `/pages/advisor/advisor?q=${encodeURIComponent(`周报里建议「${idea}」，具体可以怎么做？`)}` })
  },
})
