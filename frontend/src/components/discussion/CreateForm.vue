<template>
  <el-card shadow="never">
    <template #header>
      <span class="card-title">创建讨论</span>
    </template>
    <el-form :model="form" label-width="80px" @submit.prevent>
      <el-form-item label="讨论主题">
        <el-input
          v-model="form.topic"
          placeholder="请输入讨论主题"
          maxlength="500"
          show-word-limit
        />
      </el-form-item>

      <!-- 参赛 AI：3 个席位，允许重复选择同一模型 -->
      <el-form-item label="参赛 AI">
        <div class="pick-block">
          <div
            v-for="(slot, i) in participantSlots"
            :key="slot.key"
            class="judge-row"
          >
            <span class="judge-label">{{ slot.label }}</span>
            <el-select
              v-model="form.participants[i]"
              size="small"
              class="judge-select"
            >
              <el-option
                v-for="m in modelList"
                :key="m.ai_id"
                :label="modelOptionLabel(m)"
                :value="m.ai_id"
              />
            </el-select>
          </div>
          <div class="pick-hint">
            三个席位各自独立选择，允许重复；同一模型占多个席位时会自动编号为「模型名 1 / 2」以便区分
          </div>
          <div class="pick-hint">
            选项里的「平时分」是该 AI 在历次讨论中由评价裁判打出的最终分均值，可作为选人参考
          </div>
          <div v-if="sameModelWarn" class="warn-hint">{{ sameModelWarn }}</div>
        </div>
      </el-form-item>

      <!-- 三位裁判 -->
      <el-form-item label="裁判阵容">
        <div class="judge-block">
          <div v-for="slot in judgeSlots" :key="slot.key" class="judge-row">
            <span class="judge-label">{{ slot.label }}</span>
            <el-select
              v-model="form.judges[slot.key]"
              size="small"
              placeholder="默认"
              clearable
              class="judge-select"
            >
              <el-option
                v-for="m in modelList"
                :key="m.ai_id"
                :label="m.display_name"
                :value="m.ai_id"
              />
            </el-select>
          </div>
          <div class="pick-hint">留空则使用系统默认裁判</div>
        </div>
      </el-form-item>

      <el-form-item label="讨论轮次">
        <el-select v-model="form.rounds" style="width: 100%">
          <el-option v-for="n in 10" :key="n" :label="`${n} 轮`" :value="n" />
        </el-select>
        <div v-if="form.until_consensus" class="pick-hint">
          共识模式下，第 3 轮起每轮结束由裁判判定：达成即结束，未达成则继续至该上限
        </div>
      </el-form-item>

      <el-form-item label="最长时长">
        <el-select v-model="form.max_duration_seconds" style="width: 100%">
          <el-option label="5 分钟" :value="300" />
          <el-option label="10 分钟" :value="600" />
          <el-option label="20 分钟" :value="1200" />
          <el-option label="30 分钟" :value="1800" />
        </el-select>
        <div class="pick-hint">
          超时后不再开启新一轮，已完成的轮次照常评分收尾（10 轮长讨论建议 20 分钟以上）
        </div>
      </el-form-item>

      <el-form-item label="讨论模式">
        <el-select v-model="form.mode" style="width: 100%">
          <el-option label="自由讨论" value="free" />
          <el-option label="结构化讨论" value="structured" />
        </el-select>
      </el-form-item>

      <el-form-item label="风格模式">
        <el-select v-model="form.style_mode" style="width: 100%">
          <el-option label="默认（自由风格）" value="free" />
          <el-option label="行动规划（必须给具体方案）" value="planning" />
          <el-option label="感性讨论（直觉与情感）" value="affective" />
          <el-option label="角色扮演（正方 / 反方 / 中立主持人）" value="roleplay" />
        </el-select>
      </el-form-item>

      <el-form-item label="共识模式">
        <el-switch
          v-model="form.until_consensus"
          active-text="达成共识提前结束"
        />
      </el-form-item>

      <el-form-item v-if="form.until_consensus" label="共识严格度">
        <el-radio-group v-model="form.consensus_strictness">
          <el-radio value="loose">宽松</el-radio>
          <el-radio value="medium">中等</el-radio>
          <el-radio value="strict">严格</el-radio>
        </el-radio-group>
        <div class="pick-hint">{{ strictnessHint }}</div>
      </el-form-item>

      <el-form-item label="认知记忆">
        <el-switch
          v-model="form.memory_enabled"
          active-text="加载历史认知框架"
        />
      </el-form-item>

      <el-form-item label="智能组队">
        <div class="teamup-row">
          <el-button
            size="small"
            :loading="isTeamup"
            :disabled="!form.topic.trim()"
            @click="handleTeamup(false)"
          >
            单AI推荐
          </el-button>
          <el-button
            size="small"
            :loading="isTeamup"
            :disabled="!form.topic.trim()"
            @click="handleTeamup(true)"
          >
            多AI协商
          </el-button>
          <span class="pick-hint">AI 自动挑选 3 位参赛 + 3 位裁判</span>
        </div>
      </el-form-item>

      <el-form-item>
        <el-button
          type="primary"
          style="width: 100%"
          :disabled="!canSubmit"
          :loading="isCreating"
          @click="handleSubmit"
        >
          开始讨论
        </el-button>
      </el-form-item>
    </el-form>

    <!-- 智能组队结果 -->
    <el-dialog v-model="teamupVisible" title="智能组队结果" width="560px">
      <div v-if="teamupData">
        <div class="res-section">
          <div class="res-title">参赛 AI（3）</div>
          <div class="chips">
            <el-tag
              v-for="p in teamupData.participants"
              :key="p"
              type="success"
              size="large"
            >
              {{ modelName(p) }}
            </el-tag>
          </div>
        </div>

        <div class="res-section">
          <div class="res-title">裁判阵容（3）</div>
          <el-descriptions :column="1" size="small" border>
            <el-descriptions-item
              v-for="slot in judgeSlots"
              :key="slot.key"
              :label="slot.label"
            >
              {{ modelName(teamupData.judges?.[slot.key]) }}
            </el-descriptions-item>
          </el-descriptions>
        </div>

        <div class="res-section">
          <div class="res-title">推荐设置</div>
          <div class="res-line">
            讨论模式：<el-tag size="small">{{ styleModeLabel(teamupData.mode) }}</el-tag>
            共识严格度：<el-tag size="small">{{ strictnessLabel(teamupData.strictness) }}</el-tag>
          </div>
        </div>

        <div v-if="teamupData.reasons?.length" class="res-section">
          <div class="res-title">各 AI 推荐理由</div>
          <div
            v-for="r in teamupData.reasons"
            :key="r.ai_id"
            class="reason-item"
          >
            <strong>{{ r.display_name }}：</strong>{{ r.reason || '（未给出）' }}
          </div>
        </div>

        <div v-if="teamupData.votes" class="res-section">
          <div class="res-title">票数统计</div>
          <div class="res-line">
            参赛：{{ voteText(teamupData.votes.participants) }}
          </div>
          <div class="res-line">模式：{{ voteText(teamupData.votes.mode) }}</div>
          <div class="res-line">
            严格度：{{ voteText(teamupData.votes.strictness) }}
          </div>
        </div>

        <div v-if="teamupData.reason" class="res-section">
          <div class="res-title">推荐理由</div>
          <div class="reason-item">{{ teamupData.reason }}</div>
        </div>
      </div>

      <template #footer>
        <el-button @click="teamupVisible = false">取消</el-button>
        <el-button type="primary" @click="applyTeamup">应用推荐</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import { reactive, ref, computed, onMounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useDiscussionStore } from '@/stores/discussionStore'
import { teamupRecommend } from '@/api/extensions'
import { getAIStatus } from '@/api/system'
import { getScoreRanking } from '@/api/scores'

const store = useDiscussionStore()

const FALLBACK_IDS = [
  'gemini', 'openai', 'claude', 'grok',
  'deepseek', 'zhipu', 'wenxin', 'kimi', 'spark', 'hunyuan'
]

const judgeSlots = [
  { key: 'compliance', label: '实时监督裁判' },
  { key: 'order', label: '秩序裁判' },
  { key: 'evaluation', label: '最终评价裁判' }
]

// 3 个讨论席位：各自独立选择，允许同一模型占多个席位
const participantSlots = [
  { key: 'p1', label: '席位 1' },
  { key: 'p2', label: '席位 2' },
  { key: 'p3', label: '席位 3' }
]

const form = reactive({
  topic: '',
  rounds: 3,
  mode: 'free',
  style_mode: 'free',
  max_duration_seconds: 600,
  until_consensus: false,
  memory_enabled: false,
  consensus_strictness: 'medium',
  // 降成本：默认全部走官方直连的 DeepSeek；用户可逐席位改成任意模型
  participants: ['deepseek', 'deepseek', 'deepseek'],
  judges: { compliance: 'deepseek', order: 'deepseek', evaluation: 'deepseek' }
})

const modelList = ref([])
// 各 AI 的平时分（历史最终分均值）：{ ai_id: { avg, rounds } }
const avgScoreMap = ref({})
const isCreating = computed(() => store.status === 'created')
const isTeamup = ref(false)
const teamupVisible = ref(false)
const teamupData = ref(null)

const canSubmit = computed(
  () => form.topic.trim() && form.participants.every((p) => !!p)
)

// 一AI多职的风险提示：全同席位缺多样性；裁判兼任讨论者存在同源偏差
const sameModelWarn = computed(() => {
  const ps = form.participants.filter(Boolean)
  if (ps.length === 3 && new Set(ps).size === 1) {
    return `三个席位都是${modelName(ps[0])}：观点多样性会明显下降，建议至少换成两位不同模型`
  }
  const overlap = [...new Set(
    Object.values(form.judges).filter((j) => j && ps.includes(j))
  )]
  if (overlap.length) {
    return `${overlap.map(modelName).join('、')} 同时担任讨论者与裁判：已启用身份隔离（裁判不得因同源偏袒自己），但评分仍可能有同源偏差`
  }
  return ''
})

const strictnessHint = computed(() => ({
  loose: '宽松：核心方向无对立即可判定为达成共识',
  medium: '中等：主要分歧已被回应或部分收敛即可判定',
  strict: '严格：必须在核心结论上形成明确一致意见'
}[form.consensus_strictness] || ''))

const modelName = (id) =>
  modelList.value.find((m) => m.ai_id === id)?.display_name || id || '默认'

// 选项标签带上历史平时分：用户开讨论前就能看到每个 AI 的历史表现
const modelOptionLabel = (m) => {
  const s = avgScoreMap.value[m.ai_id]
  if (!s || s.avg === null || s.avg === undefined) return m.display_name
  return `${m.display_name}（平时分 ${Number(s.avg).toFixed(1)} · ${s.rounds} 场）`
}

const styleModeLabel = (m) =>
  ({
    free: '自由风格',
    planning: '行动规划',
    affective: '感性讨论',
    roleplay: '角色扮演'
  }[m] || m || '-')

const strictnessLabel = (s) =>
  ({ loose: '宽松', medium: '中等', strict: '严格' }[s] || s || '-')

const voteText = (obj) => {
  if (!obj) return '-'
  const entries = Object.entries(obj)
  if (!entries.length) return '-'
  return entries
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${modelName(k)} × ${v}`)
    .join('、')
}

// 开启「直到共识」时，最少需要 3 轮才能开始判定
watch(
  () => form.until_consensus,
  (on) => {
    if (on && form.rounds < 3) form.rounds = 3
  }
)

const loadModels = async () => {
  try {
    const res = await getAIStatus()
    const list = (res?.models || []).map((m) => ({
      ai_id: m.ai_id,
      display_name: m.display_name || m.ai_id,
      status: m.status
    }))
    modelList.value = list.length
      ? list
      : FALLBACK_IDS.map((id) => ({ ai_id: id, display_name: id, status: '' }))
  } catch {
    modelList.value = FALLBACK_IDS.map((id) => ({
      ai_id: id, display_name: id, status: ''
    }))
  }
}

const loadAvgScores = async () => {
  try {
    const res = await getScoreRanking('discussion')
    const map = {}
    for (const r of res?.ranking || []) {
      map[r.ai_id] = { avg: r.avg_score, rounds: r.rounds }
    }
    avgScoreMap.value = map
  } catch {
    avgScoreMap.value = {}
  }
}

onMounted(() => {
  loadModels()
  loadAvgScores()
})

const handleSubmit = async () => {
  if (!canSubmit.value) return
  const judges = {}
  for (const s of judgeSlots) {
    if (form.judges[s.key]) judges[s.key] = form.judges[s.key]
  }
  try {
    await store.submitCreate({
      topic: form.topic.trim(),
      rounds: form.rounds,
      mode: form.mode,
      style_mode: form.style_mode,
      max_duration_seconds: form.max_duration_seconds,
      until_consensus: form.until_consensus,
      memory_enabled: form.memory_enabled,
      consensus_strictness: form.consensus_strictness,
      participants: [...form.participants],
      judges: Object.keys(judges).length ? judges : undefined
    })
  } catch (e) {
    // 后端中文提示已由 request.js 统一弹出；
    // 此处捕获是为了避免 unhandledrejection，并让表单留在页面上供重试
    console.error('创建讨论失败', e)
  }
}

const handleTeamup = async (multi) => {
  if (!form.topic.trim()) return
  isTeamup.value = true
  try {
    const res = await teamupRecommend(form.topic.trim(), multi)
    teamupData.value = res
    teamupVisible.value = true
  } catch {
    ElMessage.error('智能组队失败，请稍后重试')
  } finally {
    isTeamup.value = false
  }
}

const applyTeamup = () => {
  const d = teamupData.value
  if (!d) return

  if (Array.isArray(d.participants) && d.participants.length) {
    form.participants = d.participants.slice(0, 3)
  }
  if (d.judges) {
    for (const s of judgeSlots) {
      form.judges[s.key] = d.judges[s.key] || ''
    }
  }
  if (['free', 'planning', 'affective', 'roleplay'].includes(d.mode)) {
    form.style_mode = d.mode
  }
  if (['loose', 'medium', 'strict'].includes(d.strictness)) {
    form.consensus_strictness = d.strictness
  }
  if (form.until_consensus && form.rounds < 3) form.rounds = 3

  teamupVisible.value = false
  ElMessage.success(
    `已应用推荐（${d.source === 'multi' ? '多AI协商' : '单AI推荐'}）`
  )
}
</script>

<style scoped>
.card-title {
  font-weight: 600;
  font-size: 16px;
}
.pick-block {
  width: 100%;
}
.pick-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.pick-hint {
  margin-top: 6px;
  font-size: 12px;
  color: #909399;
  line-height: 1.5;
}
.warn-hint {
  margin-top: 6px;
  font-size: 12px;
  color: #e6a23c;
  line-height: 1.5;
}
.teamup-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.judge-block {
  width: 100%;
}
.judge-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.judge-label {
  width: 96px;
  font-size: 13px;
  color: #606266;
}
.judge-select {
  flex: 1;
  max-width: 220px;
}
.res-section {
  margin-bottom: 16px;
}
.res-title {
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 8px;
  color: #303133;
}
.res-line {
  font-size: 13px;
  color: #606266;
  margin-bottom: 4px;
}
.chips {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.reason-item {
  font-size: 13px;
  color: #606266;
  line-height: 1.6;
  margin-bottom: 6px;
}
</style>
