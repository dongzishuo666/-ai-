<template>
  <el-card shadow="never" class="progress-card">
    <template #header>
      <span class="card-title">生成进度</span>
      <span v-if="status === 'running'" class="status running">生成中</span>
      <span v-else-if="status === 'done'" class="status done">已完成</span>
      <span v-else-if="status === 'failed'" class="status failed">失败</span>
    </template>
    <el-steps :active="activeStep" finish-status="success" align-center>
      <el-step title="生成大纲" :status="getStepStatus('outline')">
        <template #description>
          <span v-if="stages.outline.status === 'running'">处理中</span>
          <span v-else-if="stages.outline.status === 'done'">已完成</span>
          <span v-else>等待中</span>
        </template>
      </el-step>
      <el-step title="撰写初稿" :status="getStepStatus('draft')">
        <template #description>
          <span v-if="stages.draft.status === 'running'">处理中</span>
          <span v-else-if="stages.draft.status === 'done'">已完成</span>
          <span v-else>等待中</span>
        </template>
      </el-step>
      <el-step title="审核定稿" :status="getStepStatus('revision')">
        <template #description>
          <span v-if="stages.revision.status === 'running'">处理中</span>
          <span v-else-if="stages.revision.status === 'done'">已完成</span>
          <span v-else>等待中</span>
        </template>
      </el-step>
    </el-steps>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  stages: {
    type: Object,
    required: true
  },
  status: {
    type: String,
    default: 'idle'
  }
})

const activeStep = computed(() => {
  if (props.stages.revision.status !== 'pending') return 3
  if (props.stages.draft.status !== 'pending') return 2
  if (props.stages.outline.status !== 'pending') return 1
  return 0
})

const getStepStatus = (stage) => {
  const s = props.stages[stage].status
  if (s === 'running') return 'process'
  if (s === 'done') return 'finish'
  if (s === 'failed') return 'error'
  return 'wait'
}
</script>

<style scoped>
.card-title {
  font-weight: 600;
  font-size: 16px;
}
.progress-card :deep(.el-steps) {
  padding: 20px 0;
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
