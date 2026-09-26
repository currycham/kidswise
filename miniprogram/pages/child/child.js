const api = require('../../utils/api')

const AGES = [2, 3, 4, 5, 6, 7, 8]

Page({
  data: { child: null, deviceId: '', name: '', ageIndex: 3, AGES, busy: false, error: '' },

  onShow() {
    this.load()
  },

  async load() {
    try {
      const child = await api.currentChild()
      if (child) {
        const ageIndex = Math.max(AGES.indexOf(child.age), 0)
        this.setData({ child, name: child.name === '小朋友' ? '' : child.name, ageIndex, error: '' })
      }
    } catch (e) {
      this.setData({ error: e.message })
    }
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
