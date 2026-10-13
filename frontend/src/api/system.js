import request from './request'

// 系统健康检查
export function getSystemHealth() {
  return request({
    url: '/system/health',
    method: 'GET'
  })
}

// AI 模型状态检测
export function getAIStatus() {
  return request({
    url: '/system/ai-status',
    method: 'GET'
  })
}

// 系统配置（前端用）
export function getSystemConfig() {
  return request({
    url: '/system/config',
    method: 'GET'
  })
}

// Token 消耗统计（累积 / 单次 / 按日趋势）
// params: { days: 0|1|7|30, task_type: 'discussion'|..., limit: 40 }
export function getTokenStats(params = {}) {
  return request({
    url: '/system/token-stats',
    method: 'GET',
    params
  })
}
