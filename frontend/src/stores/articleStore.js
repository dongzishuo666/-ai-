import { defineStore } from 'pinia'
import { createArticle, getArticleDetail, getArticleList, deleteArticle as deleteArticleApi } from '@/api/articles'
import { getTaskScores } from '@/api/scores'
import { SSEClient } from '@/utils/sse'

export const useArticleStore = defineStore('article', {
  state: () => ({
    articleId: null,
    status: 'idle', // idle | creating | running | done | failed
    currentStage: null,
    stages: {
      outline: { status: 'pending', wordCount: 0, duration: 0 },
      draft: { status: 'pending', wordCount: 0, duration: 0 },
      revision: { status: 'pending', wordCount: 0, duration: 0 }
    },
    outline: '',
    draft: '',
    finalArticle: '',
    revisionNotes: '',
    totalDuration: 0,
    // 最终质量检测员：流水线结束后给三个环节 AI 的质检分
    qualityScores: [],
    qualityOverall: null,
    qualitySummary: '',
    errorMessage: '',
    sseClient: null,

    // 列表与视图状态（第二批新增）
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

  actions: {
    async submitCreate(formData) {
      this._resetState()
      this.status = 'creating'

      try {
        const res = await createArticle(formData)
        this.articleId = res.article_id
        this.status = 'running'
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

      this.sseClient.on('stage_start', (data) => {
        this.currentStage = data.stage
        this.stages[data.stage].status = 'running'
      })

      this.sseClient.on('stage_done', (data) => {
        this.stages[data.stage].status = data.status
        this.stages[data.stage].wordCount = data.word_count || 0
        this.stages[data.stage].duration = data.duration_ms || 0
      })

      this.sseClient.on('outline_ready', (data) => {
        this.outline = data.outline
      })

      this.sseClient.on('final_result', (data) => {
        this.finalArticle = data.article
        this.revisionNotes = data.revision_notes
        this.totalDuration = data.total_duration_ms
      })

      // 最终质量检测员打分（流水线跑完后由质检 AI 给出）
      this.sseClient.on('quality_scores', (data) => {
        this.qualityScores = data.scores || []
        this.qualityOverall = data.overall ?? null
        this.qualitySummary = data.summary || ''
      })

      this.sseClient.on('done', () => {
        this.sseClient.closed = true // 修正1：标记为主动关闭，阻止重连
        this.status = 'done'
        this.sseClient.close()
      })

      this.sseClient.on('error', (data) => {
        this.sseClient.closed = true // 修正1：标记为异常结束，阻止重连
        this.status = 'failed'
        this.errorMessage = data.message || '生成失败'
        this.sseClient.close()
      })
    },

    async loadDetail(id) {
      try {
        const res = await getArticleDetail(id)
        this.articleId = res.article_id
        this.status = res.status
        this.outline = res.stages?.outline?.content || ''
        this.draft = res.stages?.draft?.content || ''
        this.finalArticle = res.stages?.revision?.content || ''
        this.revisionNotes = res.stages?.revision?.revision_notes || ''
        this.totalDuration = res.total_duration_ms
        this.selectedId = id
        // 回看历史文章时也要看到质检榜：从评分接口拉一次本场成绩
        await this.loadQualityScores(id)
      } catch (error) {
        console.error('加载详情失败', error)
      }
    },

    // 拉取本场质检榜（刷新页面 / 切换历史文章时用；跑流程时由 SSE 实时覆盖）
    async loadQualityScores(articleId) {
      if (!articleId) return
      try {
        const res = await getTaskScores('article', articleId)
        const scores = res?.scores || []
        if (!scores.length) return
        this.qualityScores = scores
        if (res.overall !== null && res.overall !== undefined) {
          this.qualityOverall = res.overall
        }
        if (res.summary) this.qualitySummary = res.summary
      } catch (e) {
        // 评分接口失败不影响正文展示
      }
    },

    _resetState() {
      this.articleId = null
      this.status = 'idle'
      this.currentStage = null
      this.stages = {
        outline: { status: 'pending', wordCount: 0, duration: 0 },
        draft: { status: 'pending', wordCount: 0, duration: 0 },
        revision: { status: 'pending', wordCount: 0, duration: 0 }
      }
      this.outline = ''
      this.draft = ''
      this.finalArticle = ''
      this.revisionNotes = ''
      this.totalDuration = 0
      this.errorMessage = ''
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
    },

    // ---- 以下为第二批新增 ----
    async fetchList(page = 1) {
      this.listLoading = true
      try {
        const res = await getArticleList({
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

    async removeArticle(id) {
      await deleteArticleApi(id)
      this.fetchList(this.listPagination.page)
      if (this.selectedId === id) {
        this.selectedId = null
        this._resetState()
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
    }
  }
})
