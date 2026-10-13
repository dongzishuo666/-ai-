<template>
  <div class="token-page">
    <div class="page-header">
      <div class="header-left">
        <h2 class="page-title">Token 消耗面板</h2>
        <span class="page-sub">按模型统计累积用量，并回溯每次调用的单次消耗</span>
      </div>
      <div class="header-actions">
        <el-radio-group v-model="days" size="small" @change="load">
          <el-radio-button :value="1">今天</el-radio-button>
          <el-radio-button :value="7">7 天</el-radio-button>
          <el-radio-button :value="30">30 天</el-radio-button>
          <el-radio-button :value="0">全部</el-radio-button>
        </el-radio-group>
        <el-select
          v-model="taskType"
          size="small"
          style="width: 130px"
          placeholder="任务类型"
          @change="load"
        >
          <el-option label="全部任务" value="" />
          <el-option
            v-for="t in taskTypes"
            :key="t.value"
            :label="t.label"
            :value="t.value"
          />
        </el-select>
        <el-switch v-model="autoRefresh" size="small" @change="handleAutoChange" />
        <span class="auto-label">自动刷新</span>
        <el-button size="small" :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <!-- 概览 -->
    <div class="overview">
      <el-card shadow="never" class="ov-card">
        <div class="ov-label">调用总次数</div>
        <div class="ov-value">{{ fmtNum(summary.calls) }}</div>
        <div class="ov-foot">
          成功 {{ fmtNum(summary.success) }} · 失败 {{ fmtNum(summary.failed) }}
        </div>
      </el-card>
      <el-card shadow="never" class="ov-card">
        <div class="ov-label">累积 Token</div>
        <div class="ov-value accent">{{ fmtNum(summary.total_tokens) }}</div>
        <div class="ov-foot">
          输入 {{ fmtNum(summary.prompt_tokens) }} · 输出 {{ fmtNum(summary.completion_tokens) }}
        </div>
      </el-card>
      <el-card shadow="never" class="ov-card">
        <div class="ov-label">平均单次消耗</div>
        <div class="ov-value">{{ fmtNum(summary.avg_tokens) }}</div>
        <div class="ov-foot">单次峰值 {{ fmtNum(summary.max_tokens) }}</div>
      </el-card>
      <el-card shadow="never" class="ov-card">
        <div class="ov-label">失败率 / 平均耗时</div>
        <div class="ov-value" :class="summary.fail_rate > 20 ? 'danger' : ''">
          {{ summary.fail_rate }}%
        </div>
        <div class="ov-foot">{{ fmtNum(summary.avg_duration_ms) }} ms / 次</div>
      </el-card>
    </div>

    <div class="chart-grid">
      <!-- 各模型累积 -->
      <el-card shadow="never" class="chart-card">
        <template #header>
          <span class="card-title">各模型累积消耗</span>
          <span class="card-sub">输入 / 输出拆分，按总量降序</span>
        </template>

        <div v-if="byModel.length" class="model-bars">
          <div v-for="m in byModel" :key="m.ai_id" class="model-row">
            <div class="model-name">
              <span class="dot" :style="{ background: colorOf(m.ai_id) }"></span>
              <span class="name-text">{{ m.display_name }}</span>
              <el-tag size="small" effect="plain" type="info">
                {{ m.channel === 'relay' ? '中转' : '官方' }}
              </el-tag>
            </div>
            <div class="bar-track" :title="barTip(m)">
              <div
                class="bar-seg bar-in"
                :style="{ width: segPct(m, 'prompt') }"
              ></div>
              <div
                class="bar-seg bar-out"
                :style="{ width: segPct(m, 'completion') }"
              ></div>
            </div>
            <div class="model-num">
              <span class="num-main">{{ fmtNum(m.total_tokens) }}</span>
              <span class="num-share">{{ m.share }}%</span>
            </div>
          </div>
          <div class="legend">
            <span><i class="lg lg-in"></i>输入</span>
            <span><i class="lg lg-out"></i>输出</span>
          </div>
        </div>
        <el-empty v-else description="当前条件下没有调用记录" :image-size="70" />
      </el-card>

      <!-- 单次消耗 -->
      <el-card shadow="never" class="chart-card">
        <template #header>
          <span class="card-title">单次调用消耗</span>
          <span class="card-sub">最近 {{ recent.length }} 次（悬停查看明细）</span>
        </template>

        <div v-if="recent.length" class="single-chart">
          <div class="axis">
            <span class="axis-max">{{ fmtNum(recentMax) }}</span>
            <span class="axis-mid">{{ fmtNum(Math.round(recentMax / 2)) }}</span>
            <span class="axis-zero">0</span>
          </div>
          <div class="columns">
            <div
              v-for="c in recent"
              :key="c.id"
              class="col"
              :title="colTip(c)"
            >
              <div class="col-stack">
                <div
                  class="col-seg col-out"
                  :class="{ 'col-fail': c.status !== 'success' }"
                  :style="{ height: colPct(c, 'completion') }"
                ></div>
                <div
                  class="col-seg col-in"
                  :class="{ 'col-fail': c.status !== 'success' }"
                  :style="{ height: colPct(c, 'prompt') }"
                ></div>
              </div>
            </div>
          </div>
          <div class="col-foot">
            <span>{{ shortTime(recent[0].created_at) }}</span>
            <span class="foot-tip">
              单次均值 {{ fmtNum(summary.avg_tokens) }}
            </span>
            <span>{{ shortTime(recent[recent.length - 1].created_at) }}</span>
          </div>
        </div>
        <el-empty v-else description="当前条件下没有调用记录" :image-size="70" />
      </el-card>
    </div>

    <!-- 按日趋势 -->
    <el-card shadow="never" class="trend-card">
      <template #header>
        <span class="card-title">按日消耗趋势</span>
        <span class="card-sub">共 {{ daily.length }} 天</span>
      </template>

      <div v-if="daily.length" class="trend">
        <div class="trend-cols">
          <div
            v-for="d in daily"
            :key="d.day"
            class="tcol"
            :title="`${d.day} · ${fmtNum(d.total_tokens)} tokens · ${d.calls} 次调用`"
          >
            <div class="tcol-bar" :style="{ height: trendPct(d) }"></div>
          </div>
        </div>
        <div class="trend-foot">
          <span v-for="d in trendTicks" :key="d">{{ d.slice(5) }}</span>
        </div>
      </div>
      <el-empty v-else description="当前条件下没有调用记录" :image-size="70" />
    </el-card>

    <!-- 明细表 -->
    <el-card shadow="never" class="table-card">
      <template #header>
        <span class="card-title">模型明细</span>
      </template>
      <el-table :data="byModel" size="small" stripe>
        <el-table-column label="模型" min-width="150">
          <template #default="{ row }">
            <span class="dot" :style="{ background: colorOf(row.ai_id) }"></span>
            <span class="name-text">{{ row.display_name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="渠道" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.channel === 'relay' ? 'warning' : 'success'" effect="plain">
              {{ row.channel === 'relay' ? '中转' : '官方' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="calls" label="调用" width="80" align="right" />
        <el-table-column label="成功/失败" width="100" align="right">
          <template #default="{ row }">
            <span>{{ row.success }}</span>
            <span class="fail-num"> / {{ row.failed }}</span>
          </template>
        </el-table-column>
        <el-table-column label="输入" width="100" align="right">
          <template #default="{ row }">{{ fmtNum(row.prompt_tokens) }}</template>
        </el-table-column>
        <el-table-column label="输出" width="100" align="right">
          <template #default="{ row }">{{ fmtNum(row.completion_tokens) }}</template>
        </el-table-column>
        <el-table-column label="合计" width="110" align="right">
          <template #default="{ row }">
            <strong>{{ fmtNum(row.total_tokens) }}</strong>
          </template>
        </el-table-column>
        <el-table-column label="平均单次" width="100" align="right">
          <template #default="{ row }">{{ fmtNum(row.avg_tokens) }}</template>
        </el-table-column>
        <el-table-column label="峰值" width="90" align="right">
          <template #default="{ row }">{{ fmtNum(row.max_tokens) }}</template>
        </el-table-column>
        <el-table-column label="平均耗时" width="100" align="right">
          <template #default="{ row }">{{ fmtNum(row.avg_duration_ms) }} ms</template>
        </el-table-column>
        <el-table-column label="占比" min-width="150">
          <template #default="{ row }">
            <div class="share-cell">
              <el-progress
                :percentage="row.share"
                :stroke-width="8"
                :show-text="false"
                :color="colorOf(row.ai_id)"
              />
              <span class="share-num">{{ row.share }}%</span>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getTokenStats } from '@/api/system'

const TASK_LABELS = {
  doc_process: '文档处理',
  discussion: '多AI讨论',
  article_gen: '文章生成',
  judge: '裁判评分',
  system_check: '系统检测'
}

const PALETTE = [
  '#409eff', '#67c23a', '#e6a23c', '#f56c6c',
  '#909399', '#9b59b6', '#16a085', '#2980b9'
]

const loading = ref(false)
const data = ref(null)
const days = ref(7)
const taskType = ref('')
const autoRefresh = ref(false)
let timer = null

const summary = computed(() => data.value?.summary || {})
const byModel = computed(() => data.value?.by_model || [])
const daily = computed(() => data.value?.daily || [])
const recent = computed(() => data.value?.recent || [])
const taskTypes = computed(() =>
  (data.value?.range?.task_types || []).map(v => ({ value: v, label: TASK_LABELS[v] || v }))
)

const recentMax = computed(() =>
  recent.value.reduce((m, c) => Math.max(m, c.total_tokens || 0), 0)
)
const dailyMax = computed(() =>
  daily.value.reduce((m, d) => Math.max(m, d.total_tokens || 0), 0)
)
const modelMax = computed(() =>
  byModel.value.reduce((m, x) => Math.max(m, x.total_tokens || 0), 0)
)

// 趋势图横轴只标几个刻度，避免日期挤在一起
const trendTicks = computed(() => {
  const arr = daily.value
  if (arr.length <= 6) return arr.map(d => d.day)
  const step = Math.ceil(arr.length / 6)
  const out = []
  for (let i = 0; i < arr.length; i += step) out.push(arr[i].day)
  return out
})

function colorOf(aiId) {
  const list = byModel.value
  const idx = list.findIndex(m => m.ai_id === aiId)
  return PALETTE[(idx < 0 ? 0 : idx) % PALETTE.length]
}

function fmtNum(n) {
  const v = Number(n || 0)
  if (v >= 100000000) return (v / 100000000).toFixed(2) + '亿'
  if (v >= 10000) return (v / 10000).toFixed(v >= 1000000 ? 0 : 1) + '万'
  return v.toLocaleString('zh-CN')
}

function shortTime(s) {
  if (!s) return ''
  return `${s.slice(5, 10)} ${s.slice(11, 16)}`
}

// 横向堆叠条：两段宽度都以「最大模型总量」为基准，条长可直接横向比较
function segPct(m, kind) {
  const total = m.total_tokens || 0
  const base = modelMax.value || 1
  const v = kind === 'prompt' ? (m.prompt_tokens || 0) : (m.completion_tokens || 0)
  if (!total) return '0%'
  return (v / base * 100).toFixed(2) + '%'
}

// 纵向堆叠柱：输入在下、输出在上，高度按本次调用占峰值的比例
function colPct(c, kind) {
  const v = kind === 'prompt' ? (c.prompt_tokens || 0) : (c.completion_tokens || 0)
  const base = recentMax.value || 1
  return (v / base * 100).toFixed(2) + '%'
}

function trendPct(d) {
  const base = dailyMax.value || 1
  return Math.max(2, (d.total_tokens || 0) / base * 100).toFixed(2) + '%'
}

function barTip(m) {
  return `${m.display_name}：合计 ${fmtNum(m.total_tokens)}（输入 ${fmtNum(m.prompt_tokens)} / 输出 ${fmtNum(m.completion_tokens)}），${m.calls} 次调用`
}

function colTip(c) {
  return `${shortTime(c.created_at)} · ${c.display_name} · ${TASK_LABELS[c.task_type] || c.task_type}\n` +
    `合计 ${fmtNum(c.total_tokens)}（输入 ${fmtNum(c.prompt_tokens)} / 输出 ${fmtNum(c.completion_tokens)}）\n` +
    `${c.status === 'success' ? '成功' : '失败'} · ${c.duration_ms ?? '-'} ms`
}

async function load() {
  loading.value = true
  try {
    data.value = await getTokenStats({
      days: days.value,
      task_type: taskType.value || undefined,
      limit: 40
    })
  } catch (e) {
    data.value = data.value || null
  } finally {
    loading.value = false
  }
}

function handleAutoChange(val) {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
  if (val) timer = setInterval(load, 30000)
}

onMounted(load)
onBeforeUnmount(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.token-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.page-title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: #303133;
}
.page-sub {
  display: block;
  margin-top: 4px;
  font-size: 13px;
  color: #909399;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.auto-label {
  font-size: 13px;
  color: #606266;
  margin-left: -4px;
}

/* 概览卡 */
.overview {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}
.ov-card {
  border-radius: 8px;
}
.ov-label {
  font-size: 13px;
  color: #909399;
}
.ov-value {
  margin-top: 6px;
  font-size: 26px;
  font-weight: 600;
  color: #303133;
  line-height: 1.2;
}
.ov-value.accent {
  color: #409eff;
}
.ov-value.danger {
  color: #f56c6c;
}
.ov-foot {
  margin-top: 6px;
  font-size: 12px;
  color: #a8abb2;
}

.chart-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.card-title {
  font-weight: 600;
  color: #303133;
}
.card-sub {
  margin-left: 10px;
  font-size: 12px;
  color: #a8abb2;
}

/* 各模型累积：横向堆叠条 */
.model-bars {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.model-row {
  display: grid;
  grid-template-columns: 140px 1fr 130px;
  align-items: center;
  gap: 12px;
}
.model-name {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.name-text {
  font-size: 14px;
  color: #303133;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 4px;
}
.bar-track {
  height: 18px;
  background: #f5f7fa;
  border-radius: 9px;
  overflow: hidden;
  display: flex;
}
.bar-seg {
  height: 100%;
  transition: width 0.3s;
}
.bar-in {
  background: #409eff;
}
.bar-out {
  background: #67c23a;
}
.model-num {
  display: flex;
  align-items: baseline;
  justify-content: flex-end;
  gap: 8px;
}
.num-main {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}
.num-share {
  font-size: 12px;
  color: #a8abb2;
}
.legend {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: #909399;
  padding-top: 4px;
}
.lg {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 2px;
  margin-right: 5px;
}
.lg-in { background: #409eff; }
.lg-out { background: #67c23a; }

/* 单次消耗：纵向柱 */
.single-chart {
  display: flex;
  gap: 10px;
  height: 240px;
}
.axis {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  height: 200px;
  font-size: 11px;
  color: #c0c4cc;
  width: 48px;
  text-align: right;
}
.columns {
  flex: 1;
  display: flex;
  align-items: flex-end;
  gap: 2px;
  height: 200px;
  border-bottom: 1px solid #ebeef5;
  border-left: 1px solid #ebeef5;
}
.col {
  flex: 1;
  height: 100%;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}
.col-stack {
  width: 78%;
  height: 100%;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
}
.col-seg {
  width: 100%;
  transition: opacity 0.15s;
}
.col-in { background: #409eff; }
.col-out { background: #67c23a; }
.col-fail { background: #f56c6c; }
.col:hover .col-seg { opacity: 0.65; }
.col-foot {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: #c0c4cc;
  padding-top: 6px;
}
.foot-tip { color: #a8abb2; }

/* 趋势 */
.trend-cols {
  display: flex;
  align-items: flex-end;
  gap: 4px;
  height: 160px;
  border-bottom: 1px solid #ebeef5;
}
.tcol {
  flex: 1;
  height: 100%;
  display: flex;
  align-items: flex-end;
}
.tcol-bar {
  width: 100%;
  background: linear-gradient(180deg, #66b1ff, #409eff);
  border-radius: 3px 3px 0 0;
  transition: opacity 0.15s;
}
.tcol:hover .tcol-bar { opacity: 0.7; }
.trend-foot {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: #c0c4cc;
  padding-top: 6px;
}

/* 表格 */
.share-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}
.share-cell :deep(.el-progress) {
  flex: 1;
}
.share-num {
  font-size: 12px;
  color: #909399;
  width: 42px;
}
.fail-num {
  color: #f56c6c;
}

@media (max-width: 1200px) {
  .overview { grid-template-columns: repeat(2, 1fr); }
  .chart-grid { grid-template-columns: 1fr; }
}
</style>
