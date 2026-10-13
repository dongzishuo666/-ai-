import { defineStore } from 'pinia'
import { getSystemHealth, getAIStatus, getSystemConfig } from '@/api/system'

export const useSystemStore = defineStore('system', {
  state: () => ({
    health: null,
    aiStatus: null,
    config: null,
    loading: false,
    autoRefresh: false,
    _timer: null
  }),

  getters: {
    // 服务状态：healthy / degraded / offline / unknown
    serviceStatus: (state) => state.health?.status || 'unknown',
    
    isHealthy: (state) => state.health?.status === 'healthy',
    
    // AI 在线数
    onlineAICount: (state) => state.aiStatus?.summary?.online || 0,
    totalAICount: (state) => state.aiStatus?.summary?.total || 8,
    
    // 内存占用百分比
    memoryPercent: (state) => {
      if (!state.health?.memory_usage_mb) return 0
      const { memory_usage_mb, memory_limit_mb } = state.health
      return Math.round((memory_usage_mb / memory_limit_mb) * 100)
    }
  },

  actions: {
    // 拉取全部状态
    async fetchAll() {
      this.loading = true
      try {
        await Promise.allSettled([
          this.fetchHealth(),
          this.fetchAIStatus()
        ])
      } finally {
        this.loading = false
      }
    },

    async fetchHealth() {
      try {
        const res = await getSystemHealth()
        this.health = res
      } catch {
        this.health = { status: 'offline' }
      }
    },

    async fetchAIStatus() {
      try {
        const res = await getAIStatus()
        this.aiStatus = res
      } catch {
        // 失败保留旧数据
      }
    },

    async fetchConfig() {
      try {
        const res = await getSystemConfig()
        this.config = res
      } catch {}
    },

    // 开启自动刷新（默认 30 秒）
    startAutoRefresh(interval = 30000) {
      this.stopAutoRefresh()
      this.autoRefresh = true
      this._timer = setInterval(() => this.fetchAll(), interval)
    },

    stopAutoRefresh() {
      this.autoRefresh = false
      if (this._timer) {
        clearInterval(this._timer)
        this._timer = null
      }
    }
  }
})
