const api = require('../../utils/api')

// n 天前的日期 YYYY-MM-DD（本地时区）；服务端会换算成那一周的周一
function dayBefore(n) {
  const d = new Date(Date.now() - n * 86400000)
  const pad = (x) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

Page({
  data: { loading: true, error: '', r: null, offset: 0 }, // offset：往前翻了几周，0 是本周

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
      const { offset } = this.data
      const q = offset ? `?week=${dayBefore(offset * 7)}` : ''
      const r = await api.get(`/children/${child.id}/weekly${q}`)
      wx.setNavigationBarTitle({ title: `${r.child.name}的好奇心周报` })
      this.setData({ loading: false, r })
    } catch (e) {
      this.setData({ loading: false, error: e.message })
    }
  },

  prevWeek() {
    if (this.data.loading) return
    this.setData({ offset: this.data.offset + 1 })
    this.load()
  },

  nextWeek() {
    if (this.data.loading || !this.data.offset) return
    this.setData({ offset: this.data.offset - 1 })
    this.load()
  },

  // 周报里的建议 → 带着这句话去问顾问
  askAdvisor(e) {
    const idea = e.currentTarget.dataset.idea
    wx.navigateTo({ url: `/pages/advisor/advisor?q=${encodeURIComponent(`周报里建议「${idea}」，具体可以怎么做？`)}` })
  },
})
