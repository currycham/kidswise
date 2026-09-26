const STATUS_LABEL = { new: '新词', heard: '听过', said: '会说' }

function pad(n) {
  return n < 10 ? `0${n}` : `${n}`
}

// 服务端时间戳是秒
function clock(ts) {
  const d = new Date(ts * 1000)
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function today() {
  const d = new Date()
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

module.exports = { STATUS_LABEL, clock, today }
