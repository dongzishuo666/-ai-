<template>
  <el-card shadow="never">
    <template #header>
      <span class="card-title">讨论控制</span>
      <span class="status-tag" :class="statusClass">{{ statusText }}</span>
    </template>
    <div class="control-btns">
      <el-button
        type="warning"
        :disabled="!canPause"
        @click="handlePause"
      >
        暂停
      </el-button>
      <el-button
        type="success"
        :disabled="!canResume"
        @click="handleResume"
      >
        恢复
      </el-button>
      <el-button
        type="danger"
        :disabled="!canTerminate"
        @click="handleTerminate"
      >
        终止
      </el-button>
    </div>
    <div v-if="currentRound > 0" class="round-info">
      当前：第 {{ currentRound }} / {{ totalRounds }} 轮
    </div>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import { useDiscussionStore } from '@/stores/discussionStore'
import { ElMessageBox } from 'element-plus'

const store = useDiscussionStore()

const status = computed(() => store.status)
const currentRound = computed(() => store.currentRound)
const totalRounds = computed(() => store.totalRounds)

const statusText = computed(() => {
  const map = {
    idle: '未开始',
    created: '创建中',
    running: '进行中',
    paused: '已暂停',
    done: '已结束',
    terminated: '已终止',
    failed: '失败'
  }
  return map[status.value] || status.value
})

const statusClass = computed(() => {
  const map = {
    running: 'running',
    paused: 'paused',
    done: 'done',
    terminated: 'terminated',
    failed: 'failed'
  }
  return map[status.value] || ''
})

const canPause = computed(() => status.value === 'running')
const canResume = computed(() => status.value === 'paused')
const canTerminate = computed(() => 
  ['running', 'paused'].includes(status.value)
)

const handlePause = () => store.pause()

const handleResume = () => store.resume()

const handleTerminate = () => {
  ElMessageBox.confirm('确定终止当前讨论吗？终止后无法恢复。', '提示', {
    confirmButtonText: '确定终止',
    cancelButtonText: '取消',
    type: 'warning'
  }).then(() => {
    store.terminate()
  }).catch(() => {})
}
</script>

<style scoped>
.card-title {
  font-weight: 600;
  font-size: 16px;
}
.status-tag {
  float: right;
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 500;
}
.status-tag.running { background: #e1f3d8; color: #67c23a; }
.status-tag.paused { background: #fdf6ec; color: #e6a23c; }
.status-tag.done { background: #ecf5ff; color: #409eff; }
.status-tag.terminated { background: #fef0f0; color: #f56c6c; }
.status-tag.failed { background: #fef0f0; color: #f56c6c; }
.control-btns {
  display: flex;
  gap: 8px;
}
.control-btns .el-button {
  flex: 1;
}
.round-info {
  margin-top: 12px;
  text-align: center;
  font-size: 13px;
  color: #606266;
  padding-top: 10px;
  border-top: 1px solid #ebeef5;
}
</style>
