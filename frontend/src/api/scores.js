/**
 * 评分榜单 API
 * feature: discussion（讨论终局分） / article（文章质检分） / doc（文档质检分）
 */
import request from './request'

// 某场任务的最终榜（讨论终榜 / 质检榜）
export function getTaskScores(feature, taskId) {
  return request.get(`/scores/task/${feature}/${taskId}`)
}

// 历史总榜：每个 AI 的平时分（平均分 / 场次 / 最高 / 最低）
export function getScoreRanking(feature, limit = 50) {
  return request.get('/scores/ranking', { params: { feature, limit } })
}

// 单个 AI 的近期得分明细
export function getAiScoreHistory(aiId, feature, limit = 10) {
  return request.get(`/scores/ai/${aiId}`, { params: { feature, limit } })
}

// 最近完成的任务（可在榜单里切换查看某一场）
export function getRecentScoreTasks(feature, limit = 10) {
  return request.get('/scores/recent', { params: { feature, limit } })
}
