<template>
  <el-card v-if="visible" shadow="never" class="quality-card">
    <template #header>
      <div class="card-head">
        <span class="card-title">最终质量检测榜</span>
        <div class="head-actions">
          <span v-if="overall !== null && overall !== undefined" class="overall">
            成品总评分
            <b>{{ Number(overall).toFixed(1) }}</b>
          </span>
          <el-button size="small" text type="primary" @click="emit('view-history')">
            查看历史总榜
          </el-button>
        </div>
      </div>
    </template>

    <el-table :data="sortedScores" size="small" stripe>
      <el-table-column type="index" label="排名" width="58" align="center" />
      <el-table-column label="环节" min-width="100">
        <template #default="{ row }">{{ row.role_label || row.role || '—' }}</template>
      </el-table-column>
      <el-table-column prop="display_name" label="AI" min-width="100" />
      <el-table-column label="等级" width="70" align="center">
        <template #default="{ row }">
          <el-tag :type="gradeType(row.grade)" size="small" effect="dark">
            {{ row.grade || '—' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="质检分" width="84" align="center">
        <template #default="{ row }">
          <span class="score-num">{{ fmt(row.score) }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="comment" label="评语" min-width="180" show-overflow-tooltip />
    </el-table>

    <div v-if="summary" class="summary">{{ summary }}</div>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  scores: { type: Array, default: () => [] },
  overall: { type: [Number, String], default: null },
  summary: { type: String, default: '' }
})

const emit = defineEmits(['view-history'])

const visible = computed(() => (props.scores || []).length > 0)
const sortedScores = computed(() =>
  [...props.scores].sort((a, b) => (b.score || 0) - (a.score || 0))
)

function fmt(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(1) : '—'
}

function gradeType(g) {
  return { S: 'success', A: 'primary', B: 'warning', C: 'danger' }[g] || 'info'
}
</script>

<style scoped>
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.card-title {
  font-weight: 600;
  font-size: 15px;
}
.head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.overall {
  font-size: 12px;
  color: #909399;
}
.overall b {
  color: #409eff;
  font-size: 14px;
}
.score-num {
  font-weight: 600;
  color: #409eff;
}
.summary {
  margin-top: 10px;
  font-size: 12px;
  color: #606266;
  line-height: 1.6;
}
</style>
