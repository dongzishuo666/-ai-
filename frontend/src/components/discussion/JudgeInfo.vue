<template>
  <el-card shadow="never">
    <template #header>
      <span class="card-title">裁判团</span>
    </template>
    <div class="judge-list">
      <div v-for="judge in judges" :key="judge.judge_id" class="judge-item">
        <div class="judge-icon">
          <el-icon :size="16"><Trophy /></el-icon>
        </div>
        <div class="judge-detail">
          <div class="judge-name">
            {{ judge.display_name }}
            <!-- 弹劾换人后这里会跟着变，能直接看出当前是谁在判 -->
            <span v-if="judge.model_display || judge.model" class="judge-model">
              {{ judge.model_display || judge.model }}
            </span>
          </div>
          <div class="judge-duty">{{ judge.duty }}</div>
        </div>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { Trophy } from '@element-plus/icons-vue'

defineProps({
  judges: {
    type: Array,
    default: () => []
  }
})
</script>

<style scoped>
.card-title {
  font-weight: 600;
  font-size: 16px;
}
.judge-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.judge-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  background: #fff7e6;
  border-radius: 6px;
}
.judge-icon {
  color: #e6a23c;
  flex-shrink: 0;
}
.judge-detail {
  flex: 1;
  min-width: 0;
}
.judge-name {
  font-size: 13px;
  font-weight: 500;
  color: #303133;
  display: flex;
  align-items: center;
  gap: 6px;
}
.judge-model {
  font-size: 11px;
  font-weight: 400;
  color: #409eff;
  background: #ecf5ff;
  border-radius: 4px;
  padding: 1px 5px;
}
.judge-duty {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}
</style>
