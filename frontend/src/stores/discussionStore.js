import { defineStore } from 'pinia'
import {
  createDiscussion,
  getDiscussionDetail,
  pauseDiscussion,
  resumeDiscussion,
  terminateDiscussion,
  getDiscussionList,
  deleteDiscussion
} from '@/api/discussions'
import { SSEClient } from '@/utils/sse'
import {
  distillStream,
  judgeSummaryStream,
  cancelDistill as apiCancelDistill
} from '@/api/extensions'
import { memoryStore } from '@/utils/memoryStore'

// 后端 discussion_end 事件的 status 字段可能混入 end_reason
// （consensus / user_manual / pause_timeout / error），需归一到前端合法状态值
function normalizeStatus(raw) {
  if (raw === 'terminated' || raw === 'failed') return raw
  if (raw === 'user_manual' || raw === 'pause_timeout') return 'terminated'
  if (raw === 'error') return 'failed'
  return 'done' // done / consensus / max_rounds / 其它一律视作正常结束
}

// 详情接口的状态来自数据库，只需挡住非预期取值
function normalizeDetailStatus(raw) {
  const valid = [
    'idle', 'created', 'running', 'paused', 'done', 'terminated', 'failed'
  ]
  return valid.includes(raw) ? raw : 'done'
}

// 讨论是否已终结（此时可以安全丢弃当前会话、回到创建表单）
const FINISHED_STATUSES = new Set(['done', 'terminated', 'failed'])
function isFinishedStatus(status) {
  return FINISHED_STATUSES.has(status)
}

export const useDiscussionStore = defineStore('discussion', {
  state: () => ({
    // 讨论核心信息
    discussionId: null,
    topic: '',
    status: 'idle', // idle | created | running | paused | done | terminated | failed
    totalRounds: 3,
    currentRound: 0,
    mode: 'free',

    // 参与者与裁判
    participants: [], // 8 个讨论者
    judges: [], // 3 个裁判
    bannedList: [], // 当前被禁言的 AI ID 列表

    // 消息与评分
    messages: [],
    roundScores: {}, // { [轮次]: [ 各 AI 维度分 + 加权分 ] }
    leaderboard: [], // 累计计分榜
    judgeScoring: null, // 秩序裁判评分中：{ round, judge, message }；null = 未在评分

    // 终局结果
    finalRanking: [],
    evaluation: null, // 含 efficiency_score / consensus_level / action_plan 等全字段
    // 最终积分榜：评价裁判给每个席位的最终分（长期可查，并累积成该 AI 的平时分）
    finalScores: [],
    // 结束原因（后端 end_reason）：区分「暂停超时自动终止」与「手动终止」
    endReason: null,

    // 共识判定（「直到共识」模式）
    consensusChecks: [],    // 每轮判定记录 {round, reached, reason, judge}
    consensusReached: false,
    consensusInfo: null,

    // 局中总结：发散期（前 N 轮）结束后由评价裁判产出，收敛期据此收敛
    // {round, judge, agreed_points, core_disagreements, convergence_directions, guidance}
    midtermSummary: null,

    // 蒸馏状态
    distillPhase: 'idle',     // idle | individual | judging | done | cancelled | skipped
    distillAbort: null,       // AbortController
    distillSeq: 0,            // 单调递增序号，用于消息 key

    // 错误
    errorMessage: '',

    // SSE 与重连控制
    sseClient: null,
    reconnectAttempts: 0,
    maxReconnectAttempts: 3,
    // 待清理的定时器句柄。讨论页被 keep-alive 缓存后不会卸载，
    // 若不显式清理，回调会在用户已离开页面后照常触发、操作已失效的状态。
    _reconnectTimer: null,
    _distillTimer: null,

    // 历史列表
    viewMode: 'create', // create | history
    list: [],
    listLoading: false,
    listPagination: {
      total: 0,
      page: 1,
      pageSize: 20
    },
    selectedId: null
  }),

  getters: {
    // 终止原因文案：terminated 状态下展示，区分暂停超时与手动终止
    endReasonText() {
      switch (this.endReason) {
        case 'pause_timeout':
          return '暂停超时，讨论已自动终止'
        case 'user_manual':
          return '你已手动终止本次讨论'
        default:
          return '讨论已终止'
      }
    }
  },

  actions: {
    // 创建讨论 → 立即建立 SSE
    async submitCreate(formData) {
      this._resetState()
      this.status = 'created'

      try {
        const res = await createDiscussion(formData)
        this.discussionId = res.discussion_id
        this.topic = res.topic
        this.totalRounds = res.total_rounds
        this.mode = res.mode
        this.participants = res.participants || []
        this.judges = res.judges || []
        
        this._initSSE(res.stream_url)
      } catch (error) {
        this.status = 'failed'
        this.errorMessage = error.message
        throw error
      }
    },

    // 初始化 SSE 连接 + 全量事件监听
    _initSSE(streamUrl) {
      if (this.sseClient) {
        this.sseClient.close()
      }

      this.sseClient = new SSEClient(streamUrl)
      // 禁用通用重连，业务层自定义重连逻辑
      this.sseClient.maxReconnectAttempts = 0
      this.sseClient.connect()

      // 1. 流启动
      this.sseClient.on('stream_start', (data) => {
        this.status = 'running'
        this.reconnectAttempts = 0
        if (data.discussion_id) this.discussionId = data.discussion_id
        if (data.total_rounds) this.totalRounds = data.total_rounds
      })

      // 2. 轮次开始
      this.sseClient.on('round_start', (data) => {
        this.currentRound = data.round
        this.bannedList = data.banned_participants || []
        this.participants.forEach(p => {
          p.isBanned = this.bannedList.includes(p.ai_id)
        })
      })

      // 3. AI 发言
      this.sseClient.on('ai_message', (data) => {
        this.messages.push({
          ...data,
          compliance_status: 'pending'
        })
        this._setSpeakingStatus(data.ai_id, true)
      })

      // 4. 合规判定结果
      this.sseClient.on('compliance_result', (data) => {
        const msg = this.messages.find(m => m.message_id === data.message_id)
        if (msg) {
          msg.compliance_status = data.status
          msg.compliance_reason = data.reason
          msg.violation_type = data.violation_type
        }
        this._setSpeakingStatus(data.ai_id, false)
      })

      // 5. 轮次批量评分
      this.sseClient.on('round_score', (data) => {
        this.roundScores[data.round] = data.scores
      })

      // 6. 计分榜更新
      this.sseClient.on('scoreboard_update', (data) => {
        this.leaderboard = data.leaderboard || []
        this.bannedList = data.banned || []
        // 按席位匹配：同源席位各自独立，不会一起被标记成禁言
        this.participants.forEach(p => {
          p.isBanned = this.bannedList.some(
            b => (p.seat_id != null && b === p.seat_id) || b === p.ai_id
          )
        })
      })

      // 7. 禁言/解除禁言
      this.sseClient.on('ban_action', (data) => {
        if (data.action === 'banned') {
          if (!this.bannedList.includes(data.ai_id)) {
            this.bannedList.push(data.ai_id)
          }
          const p = this.participants.find(item => item.ai_id === data.ai_id)
          if (p) {
            p.isBanned = true
            p.banReason = data.reason
          }
        } else if (data.action === 'unbanned') {
          this.bannedList = this.bannedList.filter(id => id !== data.ai_id)
          const p = this.participants.find(item => item.ai_id === data.ai_id)
          if (p) {
            p.isBanned = false
            p.banReason = ''
          }
        }
      })

      // 7.5 秩序裁判评分中（串行长耗时环节，用于前端给出进度提示，避免被当成卡死）
      this.sseClient.on('judge_scoring', (data) => {
        this.judgeScoring = {
          round: data.round,
          judge: data.judge,
          message: data.message || '秩序裁判正在为本轮发言评分…'
        }
      })

      // 7.6 弹劾通过 → 裁判换人：更新裁判团名单，并在消息流插一条系统提示
      this.sseClient.on('judge_replaced', (data) => {
        const j = (this.judges || []).find((x) => x.judge_id === data.judge_type)
        if (j) {
          j.model = data.new_ai_id
          j.model_display = data.new_display_name
        }
        this.messages.push({
          message_id: `judge_replaced_${data.impeachment_id}`,
          kind: 'judge_replaced',
          content: data.message
            || `${data.judge_role || '裁判'}已由 ${data.old_display_name} 替换为 ${data.new_display_name}`,
          round: this.currentRound || 0,
          old_ai_id: data.old_ai_id,
          new_ai_id: data.new_ai_id
        })
      })

      // 8. 轮次结束
      this.sseClient.on('round_end', () => {
        // 评分已完成，清掉进度提示
        this.judgeScoring = null
      })

      // 8.4 局中总结：发散期结束后由评价裁判产出，之后进入收敛期
      this.sseClient.on('midterm_summary', (data) => {
        this.midtermSummary = data
      })

      // 8.5 共识判定（第 3 轮起每轮由裁判判定一次）
      this.sseClient.on('consensus_check', (data) => {
        const idx = this.consensusChecks.findIndex(c => c.round === data.round)
        const record = {
          round: data.round,
          reached: !!data.reached,
          reason: data.reason || '',
          judge: data.judge || ''
        }
        if (idx >= 0) this.consensusChecks.splice(idx, 1, record)
        else this.consensusChecks.push(record)
      })

      // 8.6 达成共识 → 提前结束
      this.sseClient.on('consensus_reached', (data) => {
        this.consensusReached = true
        this.consensusInfo = data
      })

      // 9. 暂停状态
      this.sseClient.on('discussion_paused', () => {
        this.status = 'paused'
      })

      // 10. 恢复状态
      this.sseClient.on('discussion_resumed', () => {
        this.status = 'running'
      })

      // 11. 讨论结束（后端把 end_reason 混进 status 字段，需归一到合法状态值）
      this.sseClient.on('discussion_end', (data) => {
        this.status = normalizeStatus(data.status)
        // 透传结束原因：暂停超时 ≠ 手动终止，前端据此区分展示
        this.endReason = data.end_reason || null
        if (data.end_reason === 'error') {
          this.errorMessage = this.errorMessage || '讨论执行过程中发生内部错误'
        }
        this.finalRanking = data.final_ranking || []
      })

      // 12. 终局评价（含 action_plan）
      this.sseClient.on('judge_evaluation', (data) => {
        this.evaluation = data
      })

      // 12.4 最终积分榜：评价裁判给出的最终分（可一直查看，并累积为平时分）
      this.sseClient.on('final_scores', (data) => {
        this.finalScores = data.scores || []
      })

      // 12.5 行动方案（后端将 action_plan 单独作为独立事件推送）
      this.sseClient.on('action_plan', (data) => {
        this.evaluation = { ...(this.evaluation || {}), action_plan: data.content || '' }
      })

      // 13. 错误事件 → 触发重连
      this.sseClient.on('error', (data) => {
        this.errorMessage = data.message || '连接错误'
        if (!this.sseClient.closed) {
          this._handleReconnect()
        }
      })

      // 连接异常断开 → 触发重连
      this.sseClient.eventSource.onerror = () => {
        if (this.sseClient.closed) return
        this._handleReconnect()
      }

      // 14. 流正常结束
      this.sseClient.on('done', () => {
        this.sseClient.closed = true
        this.sseClient.close()
      })
    },

    // 断线重连：先拉全量详情刷新，再重建 SSE
    async _handleReconnect() {
      if (this.reconnectAttempts >= this.maxReconnectAttempts) {
        this.status = 'failed'
        this.errorMessage = '连接失败，已超出重试次数'
        return
      }

      this.reconnectAttempts++

      try {
        // 先彻底关掉旧连接：服务端在生成器 finally 里才 unregister，
        // 若不等它回收就重连，新连接会被 409「该讨论已有活跃的 SSE 连接」拒绝
        this.closeConnection()
        // 再拉取完整历史刷新 UI
        await this.loadDetail(this.discussionId)
        // 延迟 3 秒重建连接
        this._clearTimers()
        this._reconnectTimer = setTimeout(() => {
          if (this.status !== 'terminated' && this.status !== 'done') {
            const streamUrl = `/api/discussions/${this.discussionId}/stream`
            this._initSSE(streamUrl)
          }
        }, 3000)
      } catch (e) {
        this._reconnectTimer = setTimeout(() => this._handleReconnect(), 3000)
      }
    },

    // 设置 AI 发言中状态
    _setSpeakingStatus(aiId, speaking) {
      const p = this.participants.find(item => item.ai_id === aiId)
      if (p) p.isSpeaking = speaking
    },

    // 暂停
    async pause() {
      try {
        await pauseDiscussion(this.discussionId)
      } catch (error) {
        console.error('暂停失败', error)
      }
    },

    // 恢复
    async resume() {
      try {
        await resumeDiscussion(this.discussionId)
      } catch (error) {
        console.error('恢复失败', error)
      }
    },

    // 终止
    async terminate() {
      try {
        await terminateDiscussion(this.discussionId)
        this.status = 'terminated'
        this.closeConnection()
      } catch (error) {
        console.error('终止失败', error)
      }
    },

    // 加载详情（重连 / 历史查看）
    async loadDetail(id) {
      try {
        const res = await getDiscussionDetail(id)
        this.discussionId = res.discussion_id
        this.topic = res.topic
        this.status = normalizeDetailStatus(res.status)
        this.totalRounds = res.total_rounds
        this.currentRound = res.rounds_completed
        this.mode = res.mode
        this.participants = res.participants || []
        this.judges = res.judges || []
        this.messages = res.messages || []
        this.finalRanking = res.final_ranking || []
        this.evaluation = res.judge_evaluation || null
        // 后端将行动方案作为顶层字段返回，合并进 evaluation 供终局面板渲染
        if (res.action_plan) {
          this.evaluation = { ...(this.evaluation || {}), action_plan: res.action_plan }
        }
        // 局中总结：历史讨论从详情字段恢复（后端存的是 JSON 文本）
        if (res.midterm_summary) {
          try {
            this.midtermSummary = typeof res.midterm_summary === 'string'
              ? JSON.parse(res.midterm_summary)
              : res.midterm_summary
          } catch (e) {
            this.midtermSummary = null
          }
        } else {
          this.midtermSummary = null
        }
        this.selectedId = id

        // 同步禁言状态（按席位 id，同源席位不互相牵连）
        this.bannedList = this.participants
          .filter(p => p.is_banned)
          .map(p => p.seat_id != null ? p.seat_id : p.ai_id)
        this.participants.forEach(p => {
          p.isBanned = p.is_banned
        })

        // 还原轮次评分：优先用详情接口聚合好的 round_scores
        // （messages 按裁定不含评分字段，旧逻辑永远走不通，评分表恒为空）
        if (res.round_scores && Object.keys(res.round_scores).length) {
          this.roundScores = res.round_scores
        } else if (res.messages?.length) {
          const scoresMap = {}
          res.messages.forEach(msg => {
            if (msg.scores && msg.weighted_total) {
              if (!scoresMap[msg.round]) scoresMap[msg.round] = []
              scoresMap[msg.round].push({
                ai_id: msg.ai_id,
                display_name: msg.display_name,
                ...msg.scores,
                weighted_total: msg.weighted_total
              })
            }
          })
          this.roundScores = scoresMap
        }

        // 构建累计计分榜
        if (res.participants) {
          this.leaderboard = [...res.participants]
            .map(p => ({
              ai_id: p.ai_id,
              display_name: p.display_name,
              total_score: p.total_score || 0,
              final_rank: p.final_rank
            }))
            .sort((a, b) => b.total_score - a.total_score)
        }
      } catch (error) {
        console.error('加载详情失败', error)
        throw error
      }
    },

    // 获取历史列表
    async fetchList(page = 1) {
      this.listLoading = true
      try {
        const res = await getDiscussionList({
          page,
          page_size: this.listPagination.pageSize
        })
        this.list = res.items
        this.listPagination.total = res.total
        this.listPagination.page = res.page
      } finally {
        this.listLoading = false
      }
    },

    // 删除讨论
    async deleteDiscussion(id) {
      await deleteDiscussion(id)
      this.fetchList(this.listPagination.page)
      if (this.selectedId === id) {
        this.selectedId = null
        this._resetState()
      }
    },

    // 切换视图模式
    setViewMode(mode) {
      this.viewMode = mode
      if (mode === 'history' && this.list.length === 0) {
        this.fetchList(1)
      }
      if (mode === 'create') {
        this.selectedId = null
        // 上一场已终结时，回到「新建讨论」应当重新显示创建表单
        if (isFinishedStatus(this.status)) {
          this._resetState()
        }
      }
    },

    // 新建讨论：清空当前会话，回到创建表单
    newDiscussion() {
      this._resetState()
      this.viewMode = 'create'
    },

    // 重置全部状态
    _resetState() {
      this.discussionId = null
      this.topic = ''
      this.status = 'idle'
      this.totalRounds = 3
      this.currentRound = 0
      this.mode = 'free'
      this.participants = []
      this.judges = []
      this.bannedList = []
      this.messages = []
      this.roundScores = {}
      this.leaderboard = []
      this.judgeScoring = null
      this.finalRanking = []
      this.evaluation = null
      this.consensusChecks = []
      this.consensusReached = false
      this.consensusInfo = null
      this.midtermSummary = null
      this.endReason = null
      this.errorMessage = ''
      this._clearTimers()
      this.reconnectAttempts = 0
      this.distillPhase = 'idle'
      this.distillSeq = 0
      // 先中止正在进行的蒸馏流，再清空句柄（顺序不可颠倒，否则旧流会污染新会话）
      if (this.distillAbort) {
        try { this.distillAbort.abort() } catch (e) { /* noop */ }
      }
      this.distillAbort = null
      if (this.sseClient) {
        this.sseClient.close()
        this.sseClient = null
      }
    },

    closeConnection() {
      this._clearTimers()
      if (this.sseClient) {
        this.sseClient.closed = true
        this.sseClient.close()
        this.sseClient = null
      }
    },

    // 清理待执行定时器（重连 / 蒸馏收尾）
    _clearTimers() {
      if (this._reconnectTimer) {
        clearTimeout(this._reconnectTimer)
        this._reconnectTimer = null
      }
      if (this._distillTimer) {
        clearTimeout(this._distillTimer)
        this._distillTimer = null
      }
    },

    // 页面被 keep-alive 恢复时调用：讨论仍在跑但连接已断则自动重连。
    // 没有它，从讨论页切走再切回会永久停在最后一次收到的状态。
    ensureConnection() {
      if (!this.discussionId) return
      if (['done', 'terminated', 'failed'].includes(this.status)) return
      if (this.sseClient) return
      this._initSSE(`/api/discussions/${this.discussionId}/stream`)
    },

    // ============================================================
    // 蒸馏流程（13部分总结 + 裁判总结）
    // ============================================================

    _pushDistillMessage(kind, payload) {
      this.distillSeq += 1
      this.messages.push({
        kind,                      // 消息子类型（与正常 AI 发言区分）
        ...payload,
        _seq: this.distillSeq,
        // 让 MessageFlow 用 _seq + kind 作为 key
        message_id: `distill_${kind}_${this.distillSeq}`,
      })
    },

    async startDistill() {
      if (!this.discussionId) return
      if (this.distillPhase !== 'idle') return
      this.distillPhase = 'individual'

      const handlers = {
        distill_summary: (d) => {
          this._pushDistillMessage('distill_summary', d)
          // 后端已写入 memories 表（带回 memory_id）。仅当后端落库失败时兜底写本地，
          // 否则同一份总结会在记忆库里出现两条。
          if (!d.memory_id) {
            memoryStore.add({
              content: d.summary,
              type: 'discussion',
              discussionId: this.discussionId,
              meta: { ai_id: d.ai_id, display_name: d.display_name },
            })
          }
        },
        distill_summary_failed: (d) => {
          this._pushDistillMessage('distill_summary_failed', d)
        },
        distill_judge: (d) => {
          this.distillPhase = 'done'
          this._pushDistillMessage('distill_judge', d)
          // 同上：后端已落库，本地仅在失败时兜底
          if (!d.memory_id) {
            memoryStore.add({
              content: d.summary,
              type: 'judge',
              discussionId: this.discussionId,
              judgeModel: d.judge_ai,
              meta: { judge_ai: d.judge_ai },
            })
          }
        },
        distill_judge_failed: (d) => {
          this._pushDistillMessage('distill_judge_failed', d)
        },
        frame_evolved: (d) => {
          this._pushDistillMessage('frame_evolved', d)
        },
        distill_skip: (d) => {
          this.distillPhase = 'skipped'
          this._pushDistillMessage('distill_skip', d)
        },
        distill_cancelled: () => {
          this.distillPhase = 'cancelled'
          this._pushDistillMessage('distill_cancelled', {})
        },
        done: () => {
          if (this.distillPhase !== 'cancelled' && this.distillPhase !== 'skipped') {
            this.distillPhase = 'done'
          }
          this.distillAbort = null
        },
      }

      try {
        const { controller, promise } = await distillStream(this.discussionId, handlers)
        this.distillAbort = controller
        await promise
      } catch (e) {
        if (e?.name !== 'AbortError') {
          console.error('蒸馏失败', e)
          this.distillPhase = 'idle'
          this._pushDistillMessage('distill_error', { error: e.message || String(e) })
        }
      } finally {
        this.distillAbort = null
        // 退出非终止态时回到 idle，便于下次发起
        if (this.distillPhase === 'done' || this.distillPhase === 'cancelled' || this.distillPhase === 'skipped') {
          this._distillTimer = setTimeout(() => { this.distillPhase = 'idle' }, 600)
        }
      }
    },

    async startJudgeSummary() {
      if (!this.discussionId) return
      if (this.distillPhase !== 'idle') return
      this.distillPhase = 'judging'

      const handlers = {
        distill_judge: (d) => {
          this.distillPhase = 'done'
          this._pushDistillMessage('distill_judge', d)
          // 同上：后端已落库，本地仅在失败时兜底
          if (!d.memory_id) {
            memoryStore.add({
              content: d.summary,
              type: 'judge',
              discussionId: this.discussionId,
              judgeModel: d.judge_ai,
              meta: { judge_ai: d.judge_ai },
            })
          }
        },
        distill_judge_failed: (d) => {
          this.distillPhase = 'idle'
          this._pushDistillMessage('distill_judge_failed', d)
        },
        distill_skip: (d) => {
          this.distillPhase = 'skipped'
          this._pushDistillMessage('distill_skip', d)
        },
        distill_cancelled: () => {
          this.distillPhase = 'cancelled'
          this._pushDistillMessage('distill_cancelled', {})
        },
        done: () => {
          if (!['cancelled', 'skipped'].includes(this.distillPhase)) {
            this.distillPhase = 'done'
          }
          this.distillAbort = null
        },
      }

      try {
        const { controller, promise } = await judgeSummaryStream(this.discussionId, handlers)
        this.distillAbort = controller
        await promise
      } catch (e) {
        if (e?.name !== 'AbortError') {
          console.error('裁判总结失败', e)
          this.distillPhase = 'idle'
        }
      } finally {
        this.distillAbort = null
        if (['done', 'cancelled', 'skipped'].includes(this.distillPhase)) {
          this._distillTimer = setTimeout(() => { this.distillPhase = 'idle' }, 600)
        }
      }
    },

    async cancelDistill() {
      if (this.distillPhase === 'idle') return
      try {
        await apiCancelDistill(this.discussionId)
      } catch (e) {
        console.warn('后端取消请求失败（可能已完成）', e)
      }
      if (this.distillAbort) {
        try { this.distillAbort.abort() } catch (e) { /* noop */ }
      }
    },
  }
})
