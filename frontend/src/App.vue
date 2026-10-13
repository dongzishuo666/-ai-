<template>
  <div class="app-container">
    <el-header class="app-header">
      <div class="logo">多AI协同工作平台</div>
      <el-menu
        :default-active="activeMenu"
        mode="horizontal"
        router
        @select="handleMenuSelect"
      >
        <el-menu-item index="/docs">文档成品生成</el-menu-item>
        <el-menu-item index="/discussion">多AI讨论</el-menu-item>
        <el-menu-item index="/chat">即时对话</el-menu-item>
        <el-menu-item index="/articles">AI文章生成</el-menu-item>
        <el-menu-item index="/memory">记忆库</el-menu-item>
        <el-menu-item index="/tokens">Token 消耗</el-menu-item>
      </el-menu>
      <div class="header-right">
        <el-tooltip :content="statusTooltip" placement="bottom">
          <div class="status-badge" :class="statusClass" @click="goToSystem">
            <span class="status-dot"></span>
            <span class="status-text">{{ statusShort }}</span>
          </div>
        </el-tooltip>
      </div>
    </el-header>
    <el-main class="app-main">
      <router-view v-slot="{ Component }">
        <keep-alive>
          <component :is="Component" />
        </keep-alive>
      </router-view>
    </el-main>
  </div>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSystemStore } from '@/stores/systemStore'

const route = useRoute()
const router = useRouter()
const systemStore = useSystemStore()

const activeMenu = computed(() => route.path)

const statusClass = computed(() => {
  const s = systemStore.serviceStatus
  if (s === 'healthy') return 'status-ok'
  if (s === 'degraded') return 'status-warn'
  return 'status-error'
})

const statusShort = computed(() => {
  const s = systemStore.serviceStatus
  if (s === 'healthy') return '正常'
  if (s === 'degraded') return '降级'
  if (s === 'offline') return '离线'
  return '检测中'
})

const statusTooltip = computed(() => {
  if (!systemStore.health) return '点击查看系统状态'
  return `服务${statusShort.value} · 内存 ${systemStore.memoryPercent}% · AI在线 ${systemStore.onlineAICount}/${systemStore.totalAICount}`
})

const goToSystem = () => {
  router.push('/system')
}

onMounted(() => {
  systemStore.fetchHealth()
  systemStore.fetchAIStatus()
  // 后台轻量刷新（60秒一次）
  systemStore.startAutoRefresh(60000)
})

onBeforeUnmount(() => {
  systemStore.stopAutoRefresh()
})
</script>

<style scoped>
.app-container {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #f5f7fa;
}
.app-header {
  display: flex;
  align-items: center;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  padding: 0 24px;
}
.logo {
  font-size: 18px;
  font-weight: 600;
  margin-right: 48px;
  color: #303133;
}
.app-main {
  flex: 1;
  overflow: auto;
  padding: 24px;
}
.header-right {
  margin-left: auto;
  display: flex;
  align-items: center;
}
.status-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: 20px;
  cursor: pointer;
  transition: all 0.2s;
  font-size: 13px;
}
.status-badge:hover {
  opacity: 0.85;
}
.status-badge.status-ok {
  background: #f0f9eb;
  color: #67c23a;
}
.status-badge.status-warn {
  background: #fdf6ec;
  color: #e6a23c;
}
.status-badge.status-error {
  background: #fef0f0;
  color: #f56c6c;
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: currentColor;
}
.status-text {
  font-weight: 500;
}
</style>
