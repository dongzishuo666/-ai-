<template>
  <div class="consensus-panel">
    <el-alert
      v-if="store.consensusReached"
      type="success"
      :closable="false"
      class="mb-12"
    >
      <template #title>
        已达成共识 —— 第 {{ store.consensusInfo?.round || '-' }} 轮后提前结束讨论
      </template>
      <div v-if="store.consensusInfo?.reason" class="reason">
        {{ store.consensusInfo.reason }}
      </div>
    </el-alert>

    <div v-if="checks.length">
      <div class="panel-title">每轮判定记录</div>
      <el-table :data="checks" size="small" border>
        <el-table-column prop="round" label="轮次" width="70" align="center">
          <template #default="{ row }">第 {{ row.round }} 轮</template>
        </el-table-column>
        <el-table-column label="判定结果" width="110" align="center">
          <template #default="{ row }">
            <el-tag :type="row.reached ? 'success' : 'info'" size="small">
              {{ row.reached ? '已达成共识' : '未达成共识' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="判定理由" min-width="220">
          <template #default="{ row }">
            <span class="reason">{{ row.reason || '（未给出）' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="judge" label="判定裁判" width="120" align="center" />
      </el-table>
    </div>

    <el-empty
      v-else
      description="暂无共识判定记录（需开启「直到共识」且讨论进行到第 3 轮以后）"
      :image-size="80"
    />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useDiscussionStore } from '@/stores/discussionStore'

const store = useDiscussionStore()

const checks = computed(() =>
  [...(store.consensusChecks || [])].sort((a, b) => a.round - b.round)
)
</script>

<style scoped>
.consensus-panel {
  padding: 4px 0;
}
.mb-12 {
  margin-bottom: 12px;
}
.panel-title {
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 8px;
  color: #303133;
}
.reason {
  font-size: 13px;
  color: #606266;
  line-height: 1.6;
  white-space: pre-wrap;
}
</style>
