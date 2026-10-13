/**
 * 记忆库（localStorage）
 * ------------------------------------------------------------
 * 工作流程文档要求的 ai_memory_list 记忆库：
 *   - 每条记录包含 id / timestamp / content / type / 可选 judgeModel
 *   - type: 'discussion'（讨论AI总结）/ 'judge'（裁判总结）
 *   - 仅在浏览器本地持久化，不与服务器同步
 */

const KEY = 'ai_memory_list'
const MAX_RECORDS = 200  // 防止 localStorage 溢出

function safeRead() {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? JSON.parse(raw) : []
  } catch (e) {
    console.warn('[memoryStore] 读取失败', e)
    return []
  }
}

function safeWrite(list) {
  try {
    localStorage.setItem(KEY, JSON.stringify(list))
  } catch (e) {
    console.warn('[memoryStore] 写入失败', e)
  }
}

function genId() {
  return `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

export const memoryStore = {
  /** 获取全部记忆 */
  list() {
    return safeRead()
  },

  /** 按类型过滤 */
  listByType(type) {
    return safeRead().filter((item) => item.type === type)
  },

  /** 按讨论ID获取（讨论总结/裁判总结带 discussionId 字段） */
  listByDiscussion(discussionId) {
    return safeRead().filter((item) => item.discussionId === discussionId)
  },

  /** 添加一条记忆 */
  add(record) {
    const list = safeRead()
    const item = {
      id: record.id || genId(),
      timestamp: record.timestamp || new Date().toISOString(),
      content: record.content || '',
      type: record.type || 'discussion',
      discussionId: record.discussionId ?? null,
      judgeModel: record.judgeModel ?? null,
      meta: record.meta ?? {},
    }
    list.unshift(item)
    if (list.length > MAX_RECORDS) list.length = MAX_RECORDS
    safeWrite(list)
    return item
  },

  /** 删除一条 */
  remove(id) {
    const list = safeRead().filter((it) => it.id !== id)
    safeWrite(list)
  },

  /** 清空 */
  clear() {
    safeWrite([])
  },
}

export default memoryStore