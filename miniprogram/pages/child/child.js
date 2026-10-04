const api = require('../../utils/api')

const AGES = [2, 3, 4, 5, 6, 7, 8]

Page({
  data: { child: null, deviceId: '', name: '', ageIndex: 3, AGES, busy: false, error: '', vision: true, seen: [] },

  onShow() {
    this.load()
  },

  async load() {
    try {
      const child = await api.currentChild()
      if (child) {
        const ageIndex = Math.max(AGES.indexOf(child.age), 0)
        this.setData({ child, name: child.name === '小朋友' ? '' : child.name, ageIndex, error: '' })
        await this.loadVision(child.id)
      }
    } catch (e) {
      this.setData({ error: e.message })
    }
  },

  // 木米的眼睛：开关 + 最近 30 天看过什么（只有文字描述，没有照片）
  async loadVision(childId) {
    const v = await api.get(`/children/${childId}/vision`)
    const pad = (n) => String(n).padStart(2, '0')
    const seen = v.seen.map((e) => {
      const d = new Date(e.created_at * 1000)
      return { ...e, time: `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}` }
    })
    this.setData({ vision: v.enabled, seen })
  },

  async toggleVision(e) {
    const enabled = e.detail.value
    try {
      await api.put(`/children/${this.data.child.id}/vision`, { enabled })
      this.setData({ vision: enabled })
      wx.showToast({ title: enabled ? '已打开' : '已关掉', icon: 'success' })
    } catch (err) {
      this.setData({ vision: !enabled, error: err.message })
    }
  },

  // 删除孩子的全部数据：输入孩子的昵称确认，删除后不可恢复
  deleteData() {
    const name = this.data.child.name
    wx.showModal({
      title: '删除全部数据',
      content: '',
      editable: true,
      placeholderText: `输入「${name}」确认删除，删除后不可恢复`,
      confirmText: '删除',
      confirmColor: '#D9534F',
      success: async (res) => {
        if (!res.confirm) return
        try {
          await api.post(`/children/${this.data.child.id}/delete-data`, { confirm: (res.content || '').trim() })
          wx.showToast({ title: '已删除', icon: 'success' })
          await this.load()
        } catch (err) {
          wx.showToast({ title: err.message, icon: 'none' })
        }
      },
    })
  },

  // demo 阶段设备码直接印在纸上的二维码里，内容就是 device_id
  scan() {
    wx.scanCode({
      onlyFromCamera: false,
      success: (res) => this.setData({ deviceId: res.result.trim() }),
    })
  },

  onDeviceInput(e) {
    this.setData({ deviceId: e.detail.value.trim() })
  },

  onNameInput(e) {
    this.setData({ name: e.detail.value })
  },

  onAgeChange(e) {
    this.setData({ ageIndex: Number(e.detail.value) })
  },

  async bind() {
    if (!this.data.deviceId) return
    this.setData({ busy: true, error: '' })
    try {
      const { child_id } = await api.post('/devices', { device_id: this.data.deviceId })
      wx.setStorageSync('childId', child_id)
      wx.showToast({ title: '绑定成功', icon: 'success' })
      this.setData({ deviceId: '' })
      await this.load()
    } catch (e) {
      this.setData({ error: e.message })
    }
    this.setData({ busy: false })
  },

  async save() {
    const name = this.data.name.trim()
    if (!name) {
      this.setData({ error: '请填写孩子的昵称' })
      return
    }
    this.setData({ busy: true, error: '' })
    try {
      await api.patch(`/children/${this.data.child.id}`, { name, age: AGES[this.data.ageIndex] })
      wx.showToast({ title: '已保存', icon: 'success' })
      setTimeout(() => wx.switchTab({ url: '/pages/index/index' }), 600)
    } catch (e) {
      this.setData({ error: e.message })
    }
    this.setData({ busy: false })
  },
})
