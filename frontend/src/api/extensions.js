/**
 * 扩展功能 API（扩展V4：即时对话 / 智能组队 / 弹劾 / 认知蒸馏 / 信用治理）
 */
import request from './request'

// 流式接口用原生 fetch，必须复用 axios 实例的 baseURL，
// 否则一旦配置 VITE_API_BASE，这些请求会打到不存在的 /api 路径（404）
const API_BASE = request.defaults.baseURL || '/api'

// ---------- 模块A：即时对话 ----------

export function createChatSession(data) {
  return request.post('/chat/sessions', data)
}

export function updateChatSessionModel(sessionId, aiId) {
  return request.patch(`/chat/sessions/${sessionId}/model`, { ai_id: aiId })
}

export function getChatSessionList() {
  return request.get('/chat/sessions')
}

export function getChatSessionDetail(sessionId) {
  return request.get(`/chat/sessions/${sessionId}`)
}

export function deleteChatSession(sessionId) {
  return request.delete(`/chat/sessions/${sessionId}`)
}

/**
 * 发送即时对话消息（SSE 流）。
 * 直接使用 EventSource 不可行（POST），这里用 fetch + ReadableStream。
 */
export async function sendChatMessageStream(sessionId, content, handlers) {
  const resp = await fetch(`${API_BASE}/chat/sessions/${sessionId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content })
  })
  if (!resp.ok || !resp.body) {
    throw new Error(`请求失败: ${resp.status}`)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // 按 SSE 帧解析（\n\n 分隔）
    let idx
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      let event = 'message'
      let data = {}
      for (const line of frame.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7).trim()
        else if (line.startsWith('data: ')) {
          try { data = JSON.parse(line.slice(6)) } catch { /* 忽略 */ }
        }
      }
      if (handlers[event]) handlers[event](data)
      if (event === 'done' && handlers.onDone) handlers.onDone(data)
    }
  }
}

// ---------- 模块C：智能组队 ----------

export function teamupRecommend(topic, multi = false) {
  return request.post('/teamup/recommend', { topic, multi })
}

// ---------- 模块B：弹劾 ----------

export function impeachJudge(discussionId, data) {
  return request.post(`/discussions/${discussionId}/impeach`, data)
}

export function getImpeachments(discussionId) {
  return request.get(`/discussions/${discussionId}/impeachments`)
}

// ---------- 模块E：认知蒸馏 ----------

/**
 * 认知蒸馏完整流程（13部分总结 + 裁判总结 + 框架演化）。
 * 返回 { controller, promise }：可通过 controller.abort() 取消。
 */
export async function distillStream(discussionId, handlers) {
  const controller = new AbortController()
  const promise = _runDistill(discussionId, handlers, controller)
  return { controller, promise }
}

async function _runDistill(discussionId, handlers, controller) {
  const resp = await fetch(`${API_BASE}/discussions/${discussionId}/distill`, {
    method: 'POST',
    signal: controller.signal,
  })
  if (!resp.ok || !resp.body) {
    throw new Error(`请求失败: ${resp.status}`)
  }
  await _consumeSSE(resp.body, handlers)
}

/**
 * 仅生成裁判总结（SSE 流）。
 */
export async function judgeSummaryStream(discussionId, handlers) {
  const controller = new AbortController()
  const promise = _runJudgeSummary(discussionId, handlers, controller)
  return { controller, promise }
}

async function _runJudgeSummary(discussionId, handlers, controller) {
  const resp = await fetch(`${API_BASE}/discussions/${discussionId}/judge-summary`, {
    method: 'POST',
    signal: controller.signal,
  })
  if (!resp.ok || !resp.body) {
    throw new Error(`请求失败: ${resp.status}`)
  }
  await _consumeSSE(resp.body, handlers)
}

/**
 * 请求后端停止蒸馏（取消标志）。
 */
export function cancelDistill(discussionId) {
  return request.post(`/discussions/${discussionId}/distill/cancel`)
}

async function _consumeSSE(body, handlers) {
  const reader = body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      let event = 'message'
      let data = {}
      for (const line of frame.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7).trim()
        else if (line.startsWith('data: ')) {
          try { data = JSON.parse(line.slice(6)) } catch { /* 忽略 */ }
        }
      }
      if (handlers[event]) handlers[event](data)
      if (event === 'done' && handlers.onDone) handlers.onDone(data)
    }
  }
}

export function getFrames() {
  return request.get('/frames')
}

// ---------- 模块F：信用治理 ----------

export function getCredit() {
  return request.get('/credit')
}

export function thumbsDown(target, detail = '') {
  return request.post('/credit/thumbs-down', { target, detail })
}

export function appeal(content) {
  return request.post('/credit/appeal', { content })
}
