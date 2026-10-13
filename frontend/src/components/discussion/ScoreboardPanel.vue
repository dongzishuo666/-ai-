<template>
  <div class="score-panel">
    <div class="panel-header">
      <span class="panel-title">累计计分榜</span>
      <el-select v-model="selectedRound" size="small" style="width: 120px">
        <el-option label="总榜" value="total" />
        <el-option
          v-for="n in maxRound"
          :key="n"
          :label="`第 ${n} 轮`"
          :value="n"
        />
      </el-select>
    </div>

    <el-table :data="displayData" size="small" stripe>
      <el-table-column type="index" label="排名" width="60" align="center" />
      <el-table-column prop="display_name" label="AI 模型" min-width="120" />
      <el-table-column prop="quality" label="质量" width="70" align="center" v-if="selectedRound !== 'total'" />
      <el-table-column prop="relevance" label="相关" width="70" align="center" v-if="selectedRound !== 'total'" />
      <el-table-column prop="contribution" label="贡献" width="70" align="center" v-if="selectedRound !== 'total'" />
      <el-table-column prop="compliance" label="合规" width="70" align="center" v-if="selectedRound !== 'total'" />
      <el-table-column
        :label="selectedRound === 'total' ? '总分' : '加权分'"
        width="80"
        align="center"
      >
        <template #default="{ row }">
          <span class="score-num">
            {{ selectedRound === 'total' ? row.total_score?.toFixed(1) : row.weighted_total?.toFixed(1) }}
          </span>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  leaderboard: {
    type: Array,
    default: () => []
  },
  roundScores: {
    type: Object,
    default: () => ({})
  },
  maxRound: {
    type: Number,
    default: 0
  }
})

const selectedRound = ref('total')

const displayData = computed(() => {
  if (selectedRound.value === 'total') {
    return [...props.leaderboard].sort((a, b) => (b.total_score || 0) - (a.total_score || 0))
  }
  const roundData = props.roundScores[selectedRound.value] || []
  return [...roundData].sort((a, b) => (b.weighted_total || 0) - (a.weighted_total || 0))
})
</script>

<style scoped>
.score-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.panel-title {
  font-weight: 600;
  font-size: 15px;
  color: #303133;
}
.score-num {
  font-weight: 600;
  color: #409eff;
}
</style>
