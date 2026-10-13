<template>
  <el-dialog
    :model-value="visible"
    :title="title || 'AI 评分排行榜'"
    width="760px"
    top="6vh"
    @update:model-value="emit('update:visible', $event)"
  >
    <el-tabs v-model="tab">
      <!-- 本场榜单 -->
      <el-tab-pane label="本场榜单" name="task">
        <div v-if="overallTip" class="overall-tip">{{ overallTip }}</div>
        <div v-if="apiSummary" class="summary-tip">{{ apiSummary }}</div>
        <el-table v-loading="taskLoading" :data="taskScores" size="small" stripe>
          <el-table-column type="index" label="排名" width="58" align="center" />
          <el-table-column prop="display_name" label="AI" min-width="110" />
          <el-table-column prop="role" label="角色 / 环节" width="120">
            <template #default="{ row }">
              {{ row.role || '—' }}
            </template>
          </el-table-column>
          <el-table-column label="等级" width="72" align="center">
            <template #default="{ row }">
              <el-tag :type="gradeType(row.grade)" size="small" effect="dark">
                {{ row.grade || '—' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="最终分" width="86" align="center">
            <template #default="{ row }">
              <span class="score-num">{{ fmt(row.score) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="comment" label="评语" min-width="200" show-overflow-tooltip />
        </el-table>
        <el-empty
          v-if="!taskLoading && !taskScores.length"
          description="本场还没有最终分（讨论结束后由评价裁判给出 / 流水线结束后由质检员给出）"
          :image-size="70"
        />
        <div v-if="scoredBy" class="foot-tip">打分者：{{ scoredBy }}</div>
      </el-tab-pane>

      <!-- 历史总榜（平时分） -->
      <el-tab-pane label="历史总榜 · 平时分" name="history">
        <div class="filter-bar">
          <el-radio-group v-model="featureFilter" size="small" @change="loadHistory">
            <el-radio-button value="">全部功能</el-radio-button>
            <el-radio-button value="discussion">讨论</el-radio-button>
            <el-radio-button value="article">文章</el-radio-button>
            <el-radio-button value="doc">文档</el-radio-button>
          </el-radio-group>
          <span class="filter-tip">跨任务累计，可用来挑选本次用哪个 AI</span>
        </div>
        <el-table v-loading="historyLoading" :data="ranking" size="small" stripe>
          <el-table-column type="index" label="排名" width="58" align="center" />
          <el-table-column prop="display_name" label="AI" min-width="110" />
          <el-table-column label="平时分" width="90" align="center">
            <template #default="{ row }">
              <span class="score-num">{{ fmt(row.avg_score) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="rounds" label="场次" width="72" align="center" />
          <el-table-column label="最高" width="76" align="center">
            <template #default="{ row }">{{ fmt(row.best_score) }}</template>
          </el-table-column>
          <el-table-column label="最低" width="76" align="center">
            <template #default="{ row }">{{ fmt(row.worst_score) }}</template>
          </el-table-column>
          <el-table-column label="参考度" min-width="120">
            <template #default="{ row }">
              <span v-if="row.rounds >= 3" class="ok-tip">样本充足</span>
              <span v-else class="warn-tip">样本较少（{{ row.rounds }} 场）</span>
            </template>
          </el-table-column>
        </el-table>
        <el-empty
          v-if="!historyLoading && !ranking.length"
          description="还没有累计评分，完成一次讨论 / 生成后即可看到平时分"
          :image-size="70"
        />
      </el-tab-pane>
    </el-tabs>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { getScoreRanking, getTaskScores } from '@/api/scores'

const props = defineProps({
  visible: { type: Boolean, default: false },
  // discussion / article / doc
  feature: { type: String, default: 'discussion' },
  // 任务 id；为空则只显示历史总榜
  taskId: { type: [Number, String], default: null },
  // SSE 实时推来的本场榜单（优先使用，避免多一次请求）
  liveScores: { type: Array, default: () => [] },
  liveOverall: { type: [Number, String], default: null },
  scoredBy: { type: String, default: '' },
  title: { type: String, default: '' }
})

const emit = defineEmits(['update:visible'])

const tab = ref('task')
const taskScores = ref([])
const taskLoading = ref(false)
const ranking = ref([])
const historyLoading = ref(false)
const featureFilter = ref('')

const apiOverall = ref(null)
const apiSummary = ref('')

const overallTip = computed(() => {
  const live = props.liveOverall
  const v = (live === null || live === undefined || live === '')
    ? apiOverall.value
    : live
  if (v === null || v === undefined || v === '') return ''
  return `本次成品总体质量分：${Number(v).toFixed(1)}`
})

function fmt(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(1) : '—'
}

function gradeType(g) {
  return { S: 'success', A: 'primary', B: 'warning', C: 'danger' }[g] || 'info'
}

async function loadTask() {
  if (!props.feature || !props.taskId) {
    taskScores.value = []
    apiOverall.value = null
    apiSummary.value = ''
    return
  }
  taskLoading.value = true
  try {
    const res = await getTaskScores(props.feature, props.taskId)
    taskScores.value = res.scores || []
    apiOverall.value = res.overall ?? null
    apiSummary.value = res.summary || ''
  } catch (e) {
    taskScores.value = []
  } finally {
    taskLoading.value = false
  }
}

async function loadHistory() {
  historyLoading.value = true
  try {
    const res = await getScoreRanking(featureFilter.value || null)
    ranking.value = res.ranking || []
  } catch (e) {
    ranking.value = []
  } finally {
    historyLoading.value = false
  }
}

watch(
  () => props.visible,
  (v) => {
    if (!v) return
    // 先用实时数据兜个底，再用接口数据覆盖（接口是落库权威，且带总评分与总结）
    if (props.liveScores && props.liveScores.length) {
      taskScores.value = [...props.liveScores].sort((a, b) => (b.score || 0) - (a.score || 0))
    }
    loadTask()
    loadHistory()
  },
  { immediate: true }
)

// 实时榜单可能在弹窗打开后才推来（如流水线刚跑完）
watch(
  () => props.liveScores,
  (v) => {
    if (v && v.length) {
      taskScores.value = [...v].sort((a, b) => (b.score || 0) - (a.score || 0))
    }
  },
  { deep: true }
)
</script>

<style scoped>
.filter-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.filter-tip {
  font-size: 12px;
  color: #909399;
}
.score-num {
  font-weight: 600;
  color: #409eff;
}
.overall-tip {
  font-size: 13px;
  color: #606266;
  margin-bottom: 4px;
}
.summary-tip {
  font-size: 12px;
  color: #909399;
  line-height: 1.6;
  margin-bottom: 8px;
}
.foot-tip {
  margin-top: 8px;
  font-size: 12px;
  color: #909399;
}
.ok-tip {
  font-size: 12px;
  color: #67c23a;
}
.warn-tip {
  font-size: 12px;
  color: #e6a23c;
}
</style>
