const api = require('../../utils/api')
const { STATUS_LABEL, clock } = require('../../utils/format')

const ALERT_LABEL = { hurt: '说自己受伤了', lost: '说找不到大人', distress: '说了需要关心的话', unsafe_topic: '问到了不适合的话题' }

Page({
  data: { loading: true, error: '', child: null, children: [], today: null, STATUS_LABEL },

  onShow() {
    this.load()
  },

  onPullDownRefresh() {
    this.load().then(() => wx.stopPullDownRefresh())
  },

  async load() {
    try {
      const { children, child } = await api.familyChildren()
      if (!child) {
        this.setData({ loading: false, error: '', child: null, children: [] })
        return
      }
      const today = await api.get(`/children/${child.id}/today`)
      today.quoteTime = today.quote ? clock(today.quote.at) : ''
      today.alerts = today.alerts.map((a) => ({ ...a, label: ALERT_LABEL[a.category] || '需要留意', time: clock(a.created_at) }))
      wx.setNavigationBarTitle({ title: `${child.name}的今天` })
      this.setData({ loading: false, error: '', child, children, today })
    } catch (e) {
      this.setData({ loading: false, error: e.message })
    }
  },

  // 家里有几个孩子时，在首页顶部切换
  switchChild() {
    const others = this.data.children
    wx.showActionSheet({
      itemList: others.map((c) => `${c.name}（${c.age} 岁）`),
      success: ({ tapIndex }) => {
        const picked = others[tapIndex]
        if (!picked || picked.id === this.data.child.id) return
        api.switchChild(picked.id)
        this.setData({ loading: true })
        this.load()
      },
    })
  },

  goBind() {
    wx.navigateTo({ url: '/pages/child/child' })
  },

  goAdvisor() {
    wx.navigateTo({ url: '/pages/advisor/advisor' })
  },

  goHistory() {
    wx.switchTab({ url: '/pages/history/history' })
  },
})
