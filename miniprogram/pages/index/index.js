const api = require('../../utils/api')
const { STATUS_LABEL, clock } = require('../../utils/format')

const ALERT_LABEL = { hurt: '说自己受伤了', lost: '说找不到大人', distress: '说了需要关心的话', unsafe_topic: '问到了不适合的话题' }

Page({
  data: { loading: true, error: '', child: null, today: null, STATUS_LABEL },

  onShow() {
    this.load()
  },

  onPullDownRefresh() {
    this.load().then(() => wx.stopPullDownRefresh())
  },

  async load() {
    try {
      const child = await api.currentChild()
      if (!child) {
        this.setData({ loading: false, error: '', child: null })
        return
      }
      const today = await api.get(`/children/${child.id}/today`)
      today.quoteTime = today.quote ? clock(today.quote.at) : ''
      today.alerts = today.alerts.map((a) => ({ ...a, label: ALERT_LABEL[a.category] || '需要留意', time: clock(a.created_at) }))
      wx.setNavigationBarTitle({ title: `${child.name}的今天` })
      this.setData({ loading: false, error: '', child, today })
    } catch (e) {
      this.setData({ loading: false, error: e.message })
    }
  },

  goBind() {
    wx.navigateTo({ url: '/pages/child/child' })
  },

  goHistory() {
    wx.switchTab({ url: '/pages/history/history' })
  },
})
