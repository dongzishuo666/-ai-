<template>
  <el-card shadow="never" class="progress-card">
    <template #header>
      <span class="card-title">处理进度</span>
      <span v-if="status === 'running'" class="status running">处理中</span>
      <span v-else-if="status === 'done'" class="status done">已完成</span>
      <span v-else-if="status === 'failed'" class="status failed">失败</span>
    </template>
    <el-steps :active="activeStep" finish-status="success" direction="vertical" align-center>
      <el-step
        v-for="(step, index) in steps"
        :key="step.key"
        :title="step.name"
        :status="getStepStatus(step.status)"
      >
        <template #description>
          <span v-if="step.status === 'running'">处理中</span>
          <span v-else-if="step.status === 'done'">已完成</span>
          <span v-else-if="step.status === 'failed'">失败</span>
          <span v-else>等待中</span>
        </template>
      </el-step>
    </el-steps>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  steps: {
    type: Array,
    required: true
  },
  status: {
    type: String,
    default: 'idle'
  }
})

const activeStep = computed(() => {
  const doneCount = props.steps.filter(s => s.status === 'done' || s.status === 'failed').length
  const runningIndex = props.steps.findIndex(s => s.status === 'running')
  return runningIndex >= 0 ? runningIndex : doneCount
})

const getStepStatus = (status) => {
  if (status === 'running') return 'process'
  if (status === 'done') return 'finish'
  if (status === 'failed') return 'error'
  return 'wait'
}
</script>

<style scoped>
.card-title {
  font-weight: 600;
  font-size: 16px;
}
.progress-card :deep(.el-steps) {
  padding: 10px 0;
}
.status {
  float: right;
  font-size: 14px;
  font-weight: 500;
}
.status.running { color: #409eff; }
.status.done { color: #67c23a; }
.status.failed { color: #f56c6c; }
</style>
