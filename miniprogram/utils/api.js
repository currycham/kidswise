// 家长端 API：https://api.waywise.tech/api（接口说明见 agent/API.md）
const BASE = 'https://api.waywise.tech/api'

let loginPromise = null

function wxLogin() {
  return new Promise((resolve, reject) => wx.login({ success: resolve, fail: reject }))
}

function rawRequest(url, method, data, header) {
  return new Promise((resolve, reject) =>
    wx.request({ url, method, data, header, success: resolve, fail: reject })
  )
}

// wx.login 的 code 交给服务端换 openid；AppSecret 只在服务端
function login() {
  if (!loginPromise) {
    loginPromise = (async () => {
      const { code } = await wxLogin()
      const res = await rawRequest(`${BASE}/auth/login`, 'POST', { code })
      if (res.statusCode !== 200) throw new Error((res.data && res.data.detail) || '登录失败')
      wx.setStorageSync('token', res.data.token)
      return res.data
    })()
    loginPromise.catch(() => {}).then(() => { loginPromise = null })
  }
  return loginPromise
}

async function request(path, { method = 'GET', data } = {}, retried = false) {
  let token = wx.getStorageSync('token')
  if (!token) {
    await login()
    token = wx.getStorageSync('token')
  }
  const res = await rawRequest(`${BASE}${path}`, method, data, { Authorization: `Bearer ${token}` })
  if (res.statusCode === 401 && !retried) {
    wx.removeStorageSync('token')
    return request(path, { method, data }, true)
  }
  if (res.statusCode >= 400) {
    throw new Error((res.data && res.data.detail) || `请求失败（${res.statusCode}）`)
  }
  return res.data
}

// 当前查看的孩子：记在本地，默认第一个
async function familyChildren() {
  const { children } = await request('/children')
  if (!children.length) return { children, child: null }
  const saved = wx.getStorageSync('childId')
  const child = children.find((c) => c.id === saved) || children[0]
  wx.setStorageSync('childId', child.id)
  return { children, child }
}

async function currentChild() {
  return (await familyChildren()).child
}

// 切换要看的孩子：其他页面每次打开都会重新读当前孩子
function switchChild(childId) {
  wx.setStorageSync('childId', childId)
}

module.exports = {
  request,
  currentChild,
  familyChildren,
  switchChild,
  get: (path) => request(path),
  post: (path, data) => request(path, { method: 'POST', data }),
  patch: (path, data) => request(path, { method: 'PATCH', data }),
}
