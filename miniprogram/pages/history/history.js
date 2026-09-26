const api = require('../../utils/api')
const { clock, today } = require('../../utils/format')

Page({
  data: { day: today(), maxDay: today(), sessions: [], child: null, loading: true, error: '' },

  onShow() {
    this.load()
  },

  onDayChange(e) {
    this.setData({ day: e.detail.value })
    this.load()
  },

  async load() {
    this.setData({ loading: true })
    try {
      const child = await api.currentChild()
      if (!child) {
        this.setData({ loading: false, child: null, sessions: [] })
        return
      }
      const res = await api.get(`/children/${child.id}/sessions?day=${this.data.day}`)
      const sessions = res.sessions.map((s) => ({
        id: s.id,
        time: `${clock(s.started_at)} – ${clock(s.last_at)}`,
        messages: s.messages.map((m) => ({ ...m, time: clock(m.at) })),
      }))
      this.setData({ loading: false, error: '', child, sessions })
    } catch (e) {
      this.setData({ loading: false, error: e.message })
    }
  },
})
