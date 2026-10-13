/**
 * 记忆库 API
 * ------------------------------------------------------------
 * 记忆由后端持久化（memories 表）：裁判总结在讨论结束时由后端直接落库，
 * 前端只负责检索与展示，不再写 localStorage。
 */
import request from './request'

/** 列表 + 检索：keyword / memory_type / source_type / source_id / page / page_size */
export function getMemoryList(params) {
  return request.get('/memories', { params })
}

/** 概览统计：总数、分类计数、近 7 天、总字数、主题数 */
export function getMemoryStats() {
  return request.get('/memories/stats')
}

export function getMemoryDetail(id) {
  return request.get(`/memories/${id}`)
}

export function deleteMemory(id) {
  return request.delete(`/memories/${id}`)
}

/** 清空记忆（不传 memoryType 则清空全部） */
export function clearMemories(memoryType) {
  return request.delete('/memories', {
    params: memoryType ? { memory_type: memoryType } : {}
  })
}

export function createMemory(data) {
  return request.post('/memories', data)
}
