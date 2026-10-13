<template>
  <div class="system-page">
    <div class="page-header">
      <h2 class="page-title">系统状态</h2>
      <div class="header-actions">
        <el-switch
          v-model="autoRefresh"
          active-text="自动刷新"
          inactive-text="手动刷新"
          @change="handleAutoRefreshChange"
        />
        <el-button :loading="store.loading" @click="store.fetchAll">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>

    <div class="card-grid">
      <!-- 服务概览 -->
      <el-card shadow="never" class="stat-card">
        <template #header>
          <span class="card-title">服务概览</span>
        </template>
        <div class="stat-item">
          <span class="stat-label">运行状态</span>
          <el-tag :type="statusType" size="large">{{ statusText }}</el-tag>
        </div>
        <div class="stat-item">
          <span class="stat-label">版本号</span>
          <span class="stat-value">{{ store.health?.version || '-' }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">本次运行</span>
          <span class="stat-value">{{ formatUptime(store.health?.uptime_seconds) }}</span>
        </div>
        <div class="stat-item">
          <span class="stat-label">累计运行</span>
          <div class="stat-right">
            <span class="stat-value stat-accent">{{ formatUptime(serviceUptime) }}</span>
            <span class="stat-hint">
              自 {{ formatDate(firstBootAt) }} · {{ bootCount }} 次启动 · 在线率 {{ onlineRate }}%
            </span>
          </div>
        </div>
        <div class="stat-item">
          <span class="stat-label">服务器时间</span>
          <span class="stat-value">{{ formatTime(store.health?.server_time) }}</span>
        </div>
      </el-card>

      <!-- 资源占用 -->
      <el-card shadow="never" class="stat-card">
        <template #header>
          <span class="card-title">内存占用</span>
        </template>
        <div class="memory-display">
          <div class="memory-num">
            <span class="current">{{ store.health?.memory_usage_mb || 0 }}</span>
            <span class="total"> / {{ store.health?.memory_limit_mb || 2048 }} MB</span>
          </div>
          <el-progress
            :percentage="store.memoryPercent"
            :color="memoryColor"
            :stroke-width="16"
            show-text="false"
          />
          <div class="memory-tip">
            已使用 {{ store.memoryPercent }}%，适配 2GB 轻量服务器
          </div>
        </div>
      </el-card>

      <!-- 活跃任务 -->
      <el-card shadow="never" class="stat-card">
        <template #header>
          <span class="card-title">活跃任务</span>
        </template>
        <div class="task-grid">
          <div class="task-item">
            <div class="task-num">{{ store.health?.active_tasks?.doc_tasks || 0 }}</div>
            <div class="task-name">文档任务</div>
          </div>
          <div class="task-item">
            <div class="task-num">{{ store.health?.active_tasks?.discussions || 0 }}</div>
            <div class="task-name">讨论任务</div>
          </div>
          <div class="task-item">
            <div class="task-num">{{ store.health?.active_tasks?.articles || 0 }}</div>
            <div class="task-name">文章任务</div>
          </div>
        </div>
      </el-card>
    </div>

    <!-- AI 在线列表（卡片） -->
    <el-card shadow="never" class="ai-card">
      <template #header>
        <span class="card-title">AI 在线列表</span>
        <span class="ai-summary">
          在线 {{ store.onlineAICount }} / {{ store.totalAICount }} · 调用中 {{ busyAICount }}
        </span>
      </template>

      <div class="ai-grid">
        <div
          v-for="m in aiList"
          :key="m.ai_id"
          class="ai-tile"
          :class="{ 'ai-tile-off': m.status === 'offline' }"
        >
          <div class="ai-tile-top">
            <span class="ai-avatar" :style="{ background: avatarColor(m.ai_id) }">
              {{ (m.display_name || '?')[0] }}
            </span>
            <span class="ai-tile-name">{{ m.display_name }}</span>
          </div>
          <div class="ai-tile-tags">
            <el-tag size="small" :type="aiStatusType[m.status] || 'info'">
              {{ aiStatusText[m.status] || m.status }}
            </el-tag>
            <el-tag size="small" :type="m.busy ? 'warning' : 'info'" effect="plain">
              <el-icon v-if="m.busy" class="is-loading" style="margin-right:2px"><Loading /></el-icon>
              {{ m.busy ? `调用中${m.active_calls > 1 ? '×' + m.active_calls : ''}` : '休息中' }}
            </el-tag>
          </div>
          <div class="ai-tile-meta">
            <span v-if="m.busy" class="meta-busy">正在处理任务…</span>
            <span v-else-if="m.status !== 'offline' && m.latency_ms != null">延迟 {{ m.latency_ms }}ms</span>
            <span v-else class="meta-off">{{ m.error || '—' }}</span>
          </div>
        </div>
      </div>
    </el-card>

    <!-- AI 模型状态 -->
    <el-card shadow="never" class="ai-card">
      <template #header>
        <span class="card-title">AI 模型状态</span>
        <span class="ai-summary">
          在线 {{ store.onlineAICount }} / {{ store.totalAICount }}
        </span>
      </template>

      <el-table :data="store.aiStatus?.models || []" stripe>
        <el-table-column prop="display_name" label="模型名称" min-width="140">
          <template #default="{ row }">
            <span class="ai-name">{{ row.display_name }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="channel" label="渠道" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.channel === 'official' ? 'success' : 'info'">
              {{ row.channel === 'official' ? '官方' : '中转' }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column prop="status" label="在线状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="aiStatusType[row.status]">
              {{ aiStatusText[row.status] }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="调用状态" width="110" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row.busy ? 'warning' : 'info'" effect="plain">
              <el-icon v-if="row.busy" class="is-loading" style="margin-right:2px"><Loading /></el-icon>
              {{ row.busy ? `调用中${row.active_calls > 1 ? '×' + row.active_calls : ''}` : '休息中' }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column prop="latency_ms" label="延迟" width="100" align="center">
          <template #default="{ row }">
            <span v-if="row.latency_ms != null">{{ row.latency_ms }} ms</span>
            <span v-else>-</span>
          </template>
        </el-table-column>

        <el-table-column prop="last_check" label="最后检测" width="180">
          <template #default="{ row }">
            {{ formatTime(row.last_check) }}
          </template>
        </el-table-column>

        <el-table-column prop="error" label="备注" min-width="120" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.error" class="error-text">{{ row.error }}</span>
            <span v-else class="ok-text">正常</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 用户信用治理（模块F） -->
    <el-card shadow="never" class="stat-card" style="margin-top: 16px">
      <template #header>
        <span class="card-title">用户信用治理</span>
      </template>
      <div class="credit-display">
        <div class="credit-score">
          <span class="score-num" :class="creditLevel">{{ creditInfo?.credit?.credit_score ?? '-' }}</span>
          <span class="score-unit">/ 100</span>
          <el-tag :type="creditLevel === 'score-low' ? 'danger' : creditLevel === 'score-mid' ? 'warning' : 'success'" size="small">
            {{ creditLevel === 'score-low' ? '受限' : creditLevel === 'score-mid' ? '观察' : '良好' }}
          </el-tag>
        </div>
        <el-progress
          :percentage="Number(creditInfo?.credit?.credit_score ?? 0)"
          :color="creditLevel === 'score-low' ? '#f56c6c' : creditLevel === 'score-mid' ? '#e6a23c' : '#67c23a'"
          :stroke-width="14"
        />
        <div class="credit-tip">
          点踩会扣除信用分；低于 60 分将触发 AI 圆桌会议集体评议，可能限制功能。
        </div>
      </div>
      <div v-if="creditBehaviors.length" class="behavior-list">
        <div v-for="b in creditBehaviors" :key="b.id" class="behavior-item">
          <el-tag size="small" :type="b.action === 'appeal' ? 'primary' : 'warning'">
            {{ { thumbs_down: '点踩', malicious_report: '恶意举报', danger_request: '危险请求', appeal: '申诉' }[b.action] || b.action }}
          </el-tag>
          <span class="behavior-detail">{{ b.detail }}</span>
          <span class="behavior-delta">{{ b.credit_delta < 0 ? b.credit_delta : '+' + b.credit_delta }}</span>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { Refresh, Loading } from '@element-plus/icons-vue'
import { useSystemStore } from '@/stores/systemStore'
import { getCredit } from '@/api/extensions'

const store = useSystemStore()
const autoRefresh = ref(false)

// ---------- AI 在线列表 ----------
const aiList = computed(() => store.aiStatus?.models || [])
const busyAICount = computed(() => aiList.value.filter(m => m.busy).length)

const avatarColors = {
  deepseek: '#4f6ef7',
  gemini: '#ea4335',
  openai: '#10a37f',
  claude: '#d4a373',
  grok: '#1d9bf0',
  zhipu: '#2b6aff',
  wenxin: '#2932e1',
  kimi: '#ff6b6b',
  spark: '#ff8c00',
  hunyuan: '#00a4ff'
}
const avatarColor = (id) => avatarColors[id] || '#8b949e'

// ---------- 用户信用治理（模块F） ----------
const creditInfo = ref(null)
const creditBehaviors = computed(() => creditInfo.value?.recent_behaviors || [])
const creditLevel = computed(() => {
  const s = Number(creditInfo.value?.credit?.credit_score ?? 100)
  if (s < 60) return 'score-low'
  if (s < 80) return 'score-mid'
  return 'score-high'
})

async function loadCredit() {
  try {
    creditInfo.value = await getCredit()
  } catch { /* 忽略 */ }
}

const statusText = computed(() => {
  const map = {
    healthy: '运行正常',
    degraded: '性能降级',
    offline: '服务离线',
    unknown: '未检测'
  }
  return map[store.serviceStatus] || store.serviceStatus
})

const statusType = computed(() => {
  const map = {
    healthy: 'success',
    degraded: 'warning',
    offline: 'danger',
    unknown: 'info'
  }
  return map[store.serviceStatus] || 'info'
})

// ---------- 累计运行时长（跨重启累加，区别于本次进程运行时长） ----------
const serviceUptime = computed(() => store.health?.service?.total_uptime_seconds || 0)
const bootCount = computed(() => store.health?.service?.boot_count || 0)
const firstBootAt = computed(() => store.health?.service?.first_boot_at || '')
const onlineRate = computed(() => {
  const span = store.health?.service?.since_first_boot_seconds || 0
  if (!span) return 0
  return Math.round(serviceUptime.value / span * 100)
})
const formatDate = (s) => (s ? s.slice(0, 10) : '-')

const memoryColor = computed(() => {
  const p = store.memoryPercent
  if (p >= 85) return '#f56c6c'
  if (p >= 70) return '#e6a23c'
  return '#67c23a'
})

const aiStatusType = {
  online: 'success',
  degraded: 'warning',
  offline: 'danger'
}

const aiStatusText = {
  online: '在线',
  degraded: '降级',
  offline: '离线'
}

const formatUptime = (seconds) => {
  if (!seconds) return '-'
  if (seconds < 60) return `${seconds} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分`
  return `${Math.floor(seconds / 86400)} 天 ${Math.floor((seconds % 86400) / 3600)} 小时`
}

const formatTime = (timeStr) => {
  if (!timeStr) return '-'
  return new Date(timeStr).toLocaleString('zh-CN', { hour12: false })
}

const handleAutoRefreshChange = (val) => {
  if (val) {
    store.startAutoRefresh(10000)
  } else {
    store.stopAutoRefresh()
  }
}

onMounted(() => {
  store.fetchAll()
  loadCredit()
  // 默认开启自动刷新（10 秒），让"调用中/休息中"实时可见
  autoRefresh.value = true
  store.startAutoRefresh(10000)
})

onBeforeUnmount(() => {
  store.stopAutoRefresh()
})
</script>

<style scoped>
.system-page {
  max-width: 1200px;
  margin: 0 auto;
}
.credit-display {
  padding: 8px 0;
}
.credit-score {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 10px;
}
.score-num {
  font-size: 34px;
  font-weight: 700;
}
.score-num.score-low { color: #f56c6c; }
.score-num.score-mid { color: #e6a23c; }
.score-num.score-high { color: #67c23a; }
.score-unit {
  color: #909399;
  font-size: 13px;
}
.credit-tip {
  font-size: 12px;
  color: #909399;
  margin-top: 8px;
}
.behavior-list {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.behavior-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.behavior-detail {
  color: #606266;
  flex: 1;
}
.behavior-delta {
  color: #f56c6c;
  font-weight: 600;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}
.page-title {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 16px;
}
.card-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 20px;
}
.stat-card {
  min-height: 180px;
}
.card-title {
  font-weight: 600;
  font-size: 16px;
}
.stat-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid var(--border-color);
}
.stat-item:last-child {
  border-bottom: none;
}
.stat-label {
  font-size: 14px;
  color: var(--text-regular);
}
.stat-value {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}
.stat-right {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}
.stat-accent {
  color: #409eff;
}
.stat-hint {
  font-size: 12px;
  color: #a8abb2;
}
.memory-display {
  padding: 8px 0;
}
.memory-num {
  text-align: center;
  margin-bottom: 12px;
}
.memory-num .current {
  font-size: 28px;
  font-weight: 700;
  color: var(--text-primary);
}
.memory-num .total {
  font-size: 14px;
  color: var(--text-secondary);
}
.memory-tip {
  margin-top: 8px;
  text-align: center;
  font-size: 12px;
  color: var(--text-secondary);
}
.task-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  padding: 8px 0;
}
.task-item {
  text-align: center;
  padding: 12px 0;
  background: var(--bg-light);
  border-radius: 6px;
}
.task-num {
  font-size: 24px;
  font-weight: 700;
  color: var(--primary-color);
}
.task-name {
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-regular);
}
.ai-card {
  margin-bottom: 20px;
}
.ai-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
}
.ai-tile {
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: border-color .2s, transform .1s;
}
.ai-tile:hover {
  border-color: var(--primary-color);
  transform: translateY(-2px);
}
.ai-tile-off {
  opacity: .65;
}
.ai-tile-top {
  display: flex;
  align-items: center;
  gap: 10px;
}
.ai-avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  color: #fff;
  font-weight: 700;
  font-size: 14px;
  flex-shrink: 0;
}
.ai-tile-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}
.ai-tile-tags {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.ai-tile-meta {
  font-size: 12px;
  color: var(--text-secondary);
  min-height: 18px;
}
.meta-busy {
  color: var(--warning-color, #e6a23c);
}
.meta-off {
  color: var(--danger-color, #f56c6c);
}
.ai-summary {
  float: right;
  font-size: 13px;
  color: var(--text-regular);
  font-weight: normal;
}
.ai-name {
  font-weight: 500;
}
.error-text {
  color: var(--danger-color);
  font-size: 13px;
}
.ok-text {
  color: var(--success-color);
  font-size: 13px;
}
</style>
