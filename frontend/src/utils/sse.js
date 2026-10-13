export class SSEClient {
  constructor(url, options = {}) {
    this.url = url
    this.eventSource = null
    this.listeners = {}
    this.closed = false
    this.autoReconnect = options.autoReconnect ?? true
    this.maxReconnectAttempts = options.maxReconnectAttempts ?? 3
    this.reconnectDelay = options.reconnectDelay ?? 3000
    this.reconnectAttempts = 0
    this._reconnectTimer = null
    this._onOpen = null
    this._onError = null
  }

  connect() {
    this.closed = false
    this._cleanup()

    this.eventSource = new EventSource(this.url)

    // 重连后重新注册所有已有监听器（否则重连后事件失效）
    Object.keys(this.listeners).forEach(type => {
      this.listeners[type].forEach(cb => {
        if (cb._handler) {
          this.eventSource.addEventListener(type, cb._handler)
        }
      })
    })

    this.eventSource.onopen = () => {
      this.reconnectAttempts = 0
      this._onOpen?.()
    }

    this.eventSource.onerror = () => {
      if (this.closed) return
      this._onError?.()
      if (this.autoReconnect) {
        this._handleReconnect()
      }
    }
  }

  on(eventType, callback) {
    if (!this.listeners[eventType]) {
      this.listeners[eventType] = []
    }
    this.listeners[eventType].push(callback)

    const handler = (event) => {
      try {
        const data = event.data ? JSON.parse(event.data) : {}
        callback(data)
      } catch (e) {
        callback(event.data)
      }
    }
    callback._handler = handler
    if (this.eventSource) {
      this.eventSource.addEventListener(eventType, handler)
    }
  }

  _handleReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      this._emit('error', { message: '连接失败，已超出重试次数' })
      return
    }
    this.reconnectAttempts++
    this._clearReconnectTimer()
    this._reconnectTimer = setTimeout(() => {
      if (!this.closed) {
        this.connect()
      }
    }, this.reconnectDelay)
  }

  _clearReconnectTimer() {
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer)
      this._reconnectTimer = null
    }
  }

  _emit(eventType, data) {
    this.listeners[eventType]?.forEach(cb => cb(data))
  }

  _cleanup() {
    if (this.eventSource) {
      // 移除所有事件监听
      Object.keys(this.listeners).forEach(type => {
        this.listeners[type].forEach(cb => {
          this.eventSource.removeEventListener(type, cb._handler)
        })
      })
      this.eventSource.close()
      this.eventSource = null
    }
  }

  close() {
    this.closed = true
    this._clearReconnectTimer()
    this._cleanup()
    this.listeners = {}
  }
}
