import { defineStore } from 'pinia'
import { uploadDoc, processDoc, getDocDetail, saveDoc, getDocList, deleteDoc } from '@/api/docs'
import { getTaskScores } from '@/api/scores'
import { SSEClient } from '@/utils/sse'

export const useDocStore = defineStore('doc', {
  state: () => ({
    taskId: null,
    fileInfo: null, // {filename, doc_type, file_size, char_count}
    status: 'idle', // idle | uploading | running | done | failed
    steps: [
      { key: 'parse', name: '文档解析', status: 'pending' },
      { key: 'planner', name: '成品初稿', status: 'pending' },
      { key: 'optimizer', name: '审校优化', status: 'pending' },
      { key: 'finalizer', name: '终审定稿', status: 'pending' }
    ],
    userNeed: '',
    // 交付的成品正文（后端字段仍叫 final_prompt，语义上是成品而非 Prompt）
    finalResult: '',
    totalDuration: 0,
    errorMessage: '',
    sseClient: null,
    // 最终质量检测员：流水线结束后给三个环节 AI 的质检分
    qualityScores: [],
    qualityOverall: null,
    qualitySummary: '',

    // 列表与视图
    viewMode: 'create', // create | history
    list: [],
    listLoading: false,
    listPagination: {
      total: 0,
      page: 1,
      pageSize: 20
    },
    selectedId: null,
    isSaved: false
  }),

  actions: {
    // 上传文档
    async uploadFile(file) {
      this.status = 'uploading'
      this.errorMessage = ''
      try {
        const res = await uploadDoc(file)
        this.taskId = res.task_id
        this.fileInfo = {
          filename: res.filename,
          doc_type: res.doc_type,
          file_size: res.file_size,
          char_count: res.char_count
        }
        this.status = 'pending'
        return res
      } catch (error) {
        this.status = 'idle'
        this.errorMessage = error.message
        throw error
      }
    },

    // 提交处理
    // roles：{planner, optimizer, finalizer} 自选模型，留空则用默认角色分配
    async submitProcess(userNeed, roles = null) {
      this.userNeed = userNeed
      this._resetSteps()
      this.status = 'running'
      this.isSaved = false

      try {
        const res = await processDoc({
          task_id: this.taskId,
          user_need: userNeed,
          roles: roles || undefined
        })
        this._initSSE(res.stream_url)
      } catch (error) {
        this.status = 'failed'
        this.errorMessage = error.message
      }
    },

    _initSSE(streamUrl) {
      if (this.sseClient) {
        this.sseClient.close()
      }

      this.sseClient = new SSEClient(streamUrl)
      this.sseClient.connect()

      this.sseClient.on('stream_start', () => {
        this.status = 'running'
      })

      this.sseClient.on('parse_done', () => {
        this._setStepStatus('parse', 'done')
      })

      this.sseClient.on('step_start', (data) => {
        const stepKey = data.role
        this._setStepStatus(stepKey, 'running')
      })

      this.sseClient.on('step_done', (data) => {
        const stepKey = data.role
        this._setStepStatus(stepKey, 'done')
      })

      this.sseClient.on('step_failed', (data) => {
        const stepKey = data.role
        this._setStepStatus(stepKey, 'failed')
      })

      this.sseClient.on('final_result', (data) => {
        this.finalResult = data.result || data.prompt || ''
        this.totalDuration = data.total_duration_ms
      })

      // 最终质量检测员打分（流水线跑完后由质检 AI 给出）
      this.sseClient.on('quality_scores', (data) => {
        this.qualityScores = data.scores || []
        this.qualityOverall = data.overall ?? null
        this.qualitySummary = data.summary || ''
      })

      this.sseClient.on('done', () => {
        this.sseClient.closed = true
        this.status = 'done'
        this.sseClient.close()
      })

      this.sseClient.on('error', (data) => {
        this.sseClient.closed = true
        this.status = 'failed'
        this.errorMessage = data.message || '处理失败'
        this.sseClient.close()
      })
    },

    _setStepStatus(key, status) {
      const step = this.steps.find(s => s.key === key)
      if (step) {
        step.status = status
      }
    },

    _resetSteps() {
      this.steps.forEach(s => s.status = 'pending')
      this.finalResult = ''
      this.totalDuration = 0
      this.errorMessage = ''
    },

    // 保存结果
    async saveResult() {
      try {
        await saveDoc(this.taskId)
        this.isSaved = true
        return true
      } catch (error) {
        return false
      }
    },

    // 加载详情
    async loadDetail(id) {
      try {
        const res = await getDocDetail(id)
        this.taskId = res.task_id
        this.fileInfo = {
          filename: res.filename,
          doc_type: res.doc_type,
          file_size: res.file_size,
          char_count: res.char_count
        }
        this.userNeed = res.user_need
        this.finalResult = res.final_prompt || ''
        this.status = res.status
        this.isSaved = res.status === 'saved'
        this.totalDuration = res.total_duration_ms
        this.selectedId = id

        // 同步步骤状态
        this._resetSteps()
        if (res.steps && res.steps.length) {
          res.steps.forEach(step => {
            this._setStepStatus(step.role, step.status)
          })
        }
        if (res.status === 'done' || res.status === 'saved') {
          this._setStepStatus('parse', 'done')
        }
        // 回看历史文档任务时也要看到质检榜：从评分接口拉一次本场成绩
        await this.loadQualityScores(id)
      } catch (error) {
        console.error('加载详情失败', error)
      }
    },

    // 拉取本场质检榜（刷新页面 / 切换历史任务时用；跑流程时由 SSE 实时覆盖）
    async loadQualityScores(taskId) {
      if (!taskId) return
      try {
        const res = await getTaskScores('doc', taskId)
        const scores = res?.scores || []
        if (!scores.length) return
        this.qualityScores = scores
        if (res.overall !== null && res.overall !== undefined) {
          this.qualityOverall = res.overall
        }
        if (res.summary) this.qualitySummary = res.summary
      } catch (e) {
        // 评分接口失败不影响成品展示
      }
    },

    // 获取历史列表
    async fetchList(page = 1) {
      this.listLoading = true
      try {
        const res = await getDocList({
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

    // 删除任务
    async deleteTask(id) {
      await deleteDoc(id)
      this.fetchList(this.listPagination.page)
      if (this.selectedId === id) {
        this.selectedId = null
        this._resetAll()
      }
    },

    setViewMode(mode) {
      this.viewMode = mode
      if (mode === 'history' && this.list.length === 0) {
        this.fetchList(1)
      }
      if (mode === 'create') {
        this.selectedId = null
      }
    },

    _resetAll() {
      this.taskId = null
      this.fileInfo = null
      this.status = 'idle'
      this._resetSteps()
      this.userNeed = ''
      this.finalResult = ''
      this.totalDuration = 0
      this.errorMessage = ''
      this.isSaved = false
      this.qualityScores = []
      this.qualityOverall = null
      this.qualitySummary = ''
      if (this.sseClient) {
        this.sseClient.close()
        this.sseClient = null
      }
    },

    closeConnection() {
      if (this.sseClient) {
        this.sseClient.close()
        this.sseClient = null
      }
    }
  }
})
