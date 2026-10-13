<template>
  <el-card shadow="never">
    <template #header>
      <span class="card-title">讨论参与者</span>
      <span class="count">{{ participants.length }} / 3</span>
    </template>
    <div class="participant-grid">
      <div
        v-for="item in participants"
        :key="item.ai_id"
        class="participant-card"
        :class="{
          'speaking': item.isSpeaking,
          'banned': item.isBanned
        }"
      >
        <div class="avatar">
          {{ item.display_name.charAt(0) }}
          <span v-if="item.isSpeaking" class="speaking-dot"></span>
        </div>
        <div class="info">
          <div class="name">{{ item.display_name }}</div>
          <div class="channel">
            <el-tag size="small" :type="item.channel === 'official' ? 'success' : 'info'">
              {{ item.channel === 'official' ? '官方' : '中转' }}
            </el-tag>
          </div>
        </div>
        <div v-if="item.isBanned" class="ban-tag">
          <el-icon><Warning /></el-icon>
          禁言
        </div>
      </div>
    </div>
  </el-card>
</template>

<script setup>
import { Warning } from '@element-plus/icons-vue'

defineProps({
  participants: {
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
.count {
  float: right;
  font-size: 13px;
  color: #909399;
}
.participant-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.participant-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px;
  border-radius: 8px;
  background: #f8f9fc;
  border: 1px solid #eaeef5;
  transition: all 0.2s;
  position: relative;
}
.participant-card.speaking {
  border-color: #409eff;
  background: #ecf5ff;
  box-shadow: 0 0 0 2px rgba(64, 158, 255, 0.15);
}
.participant-card.banned {
  opacity: 0.45;
  filter: grayscale(0.6);
}
.avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: linear-gradient(135deg, #667eea, #764ba2);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 600;
  position: relative;
  flex-shrink: 0;
}
.speaking-dot {
  position: absolute;
  right: -2px;
  bottom: -2px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #67c23a;
  border: 2px solid #fff;
}
.info {
  flex: 1;
  min-width: 0;
}
.name {
  font-size: 13px;
  font-weight: 500;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.channel {
  margin-top: 4px;
}
.ban-tag {
  position: absolute;
  top: 4px;
  right: 4px;
  font-size: 11px;
  color: #f56c6c;
  display: flex;
  align-items: center;
  gap: 2px;
}
</style>
