const api = require('../../utils/api')
const { STATUS_LABEL } = require('../../utils/format')

Page({
  data: { child: null, counts: { heard: 0, said: 0 }, said: [], heard: [], older: 0, loading: true, error: '', STATUS_LABEL },

  openWeekly() {
    wx.navigateTo({ url: '/pages/weekly/weekly' })
  },

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
        this.setData({ loading: false, child: null })
        return
      }
      const res = await api.get(`/children/${child.id}/progress`)
      const week = res.words.filter((w) => w.this_week)
      this.setData({
        loading: false,
        error: '',
        child,
        counts: res.counts,
        said: week.filter((w) => w.status === 'said'),
        heard: week.filter((w) => w.status === 'heard'),
        older: res.words.length - week.length,
      })
    } catch (e) {
      this.setData({ loading: false, error: e.message })
    }
  },
})
