<template>
  <div class="chat-view">
    <!-- 左侧：会话列表 -->
    <div class="session-panel">
      <el-button type="primary" class="new-btn" @click="handleNewSession">
        新建对话
      </el-button>
      <div
        v-for="s in sessions"
        :key="s.id"
        class="session-item"
        :class="{ active: s.id === currentSessionId }"
        @click="handleSelectSession(s.id)"
      >
        <div class="session-title">{{ s.title }}</div>
        <div class="session-meta">{{ sessionModelLabel(s) }}</div>
      </div>
    </div>

    <!-- 右侧：对话区 -->
    <div class="chat-panel">
      <div class="chat-header">
        <span v-if="session">{{ session.title }}（{{ chatModeText }}）</span>
        <span v-else>即时对话</span>
        <div class="header-actions">
          <span class="model-picker">
            <span class="picker-label">模型</span>
            <el-select
              v-model="selectedModel"
              size="small"
              class="model-select"
              placeholder="选择模型"
              @change="handleModelChange"
            >
              <el-option label="自动（按模式挑模型）" value="" />
              <el-option
                v-for="m in modelList"
                :key="m.ai_id"
                :label="m.display_name"
                :value="m.ai_id"
              />
            </el-select>
          </span>
          <span v-if="selectedModel" class="solo-tip">
            单独对话模式：原样收发，不套提示词
          </span>
          <el-switch
            v-model="supervisorEnabled"
            active-text="监督员"
            :disabled="!!selectedModel"
            @change="handleNewSession"
          />
          <el-switch
            v-model="memoryEnabled"
            active-text="记忆"
            :disabled="!!selectedModel"
            @change="handleNewSession"
          />
        </div>
      </div>

      <div ref="msgBox" class="message-list">
        <div
          v-for="m in messages"
          :key="m.id"
          class="message-row"
          :class="m.role"
        >
          <div class="bubble">
            <div v-if="m.role === 'ai'" class="bubble-head">
              {{ m.ai_id }}
              <el-tag v-if="m.is_anomaly" type="danger" size="small">异常已重写</el-tag>
            </div>
            <div class="bubble-body">{{ m.content }}</div>
          </div>
        </div>
        <el-empty v-if="messages.length === 0" description="发起新对话吧" />
      </div>

      <div class="input-area">
        <el-input
          v-model="inputText"
          type="textarea"
          :rows="2"
          placeholder="输入消息，Enter 发送（Shift+Enter 换行）"
          @keydown.enter.exact.prevent="handleSend"
        />
        <el-button
          type="primary"
          :loading="isStreaming"
          :disabled="!inputText.trim() || !currentSessionId"
          @click="handleSend"
        >
          发送
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref, nextTick, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { getAIStatus } from '@/api/system'
import {
  createChatSession,
  getChatSessionList,
  getChatSessionDetail,
  updateChatSessionModel,
  sendChatMessageStream
} from '@/api/extensions'

const sessions = ref([])
const currentSessionId = ref(null)
const session = computed(() => sessions.value.find(s => s.id === currentSessionId.value))
const messages = ref([])
const inputText = ref('')
const isStreaming = ref(false)
const supervisorEnabled = ref(true)
const memoryEnabled = ref(true)
const msgBox = ref(null)
const modelList = ref([])
// 默认与 DeepSeek 单独对话；置空则走原来的「按模式自动挑模型」
const selectedModel = ref('deepseek')

const modeLabel = (m) => ({
  quick: '快速问答',
  serial: '串行批判',
  parallel: '并行竞答'
}[m] || m)

// 头部模式描述：指定模型=单独对话，否则走原自动挑模型逻辑
const chatModeText = computed(() => {
  if (selectedModel.value) return `单独对话 · ${modelName(selectedModel.value)}`
  return `${modeLabel(session.value?.mode || 'quick')}（自动挑模型）`
})

const modelName = (id) => {
  if (!id) return '自动'
  const hit = modelList.value.find(m => m.ai_id === id)
  return hit ? hit.display_name : id
}

const sessionModelLabel = (s) =>
  s.ai_id ? `单独对话 · ${modelName(s.ai_id)}` : modeLabel(s.mode)

async function loadModels() {
  try {
    const res = await getAIStatus()
    const all = res.models || []
    modelList.value = all.filter(
      m => m.status === 'online' || m.status === 'degraded')
  } catch (e) {
    modelList.value = []
  }
}

async function handleModelChange(val) {
  if (!currentSessionId.value) return
  const aiId = val || null
  try {
    await updateChatSessionModel(currentSessionId.value, aiId)
    await loadSessions()
    ElMessage.success(aiId ? `本对话已切换为 ${modelName(aiId)}` : '已切换为自动挑模型')
  } catch (e) {
    ElMessage.error(e?.message || '切换模型失败')
  }
}

async function loadSessions() {
  const res = await getChatSessionList()
  sessions.value = res.items || []
}

async function handleNewSession() {
  // 开关变化时也走这里：直接新建一个会话
  const title = `对话 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`
  const res = await createChatSession({
    title,
    mode: 'quick',
    supervisor_enabled: supervisorEnabled.value,
    memory_enabled: memoryEnabled.value,
    ai_id: selectedModel.value || null
  })
  currentSessionId.value = res.session.id
  messages.value = []
  await loadSessions()
  ElMessage.success('新对话已创建')
}

async function handleSelectSession(id) {
  currentSessionId.value = id
  const res = await getChatSessionDetail(id)
  messages.value = res.messages || []
  selectedModel.value = res.session?.ai_id || ''
  scrollToBottom()
}

async function handleSend() {
  const text = inputText.value.trim()
  if (!text || !currentSessionId.value) return
  inputText.value = ''
  messages.value.push({
    id: `local-${Date.now()}`, role: 'user', content: text
  })
  scrollToBottom()

  isStreaming.value = true
  try {
    await sendChatMessageStream(currentSessionId.value, text, {
      chat_user: () => {},
      chat_ai: (d) => {
        messages.value.push({
          id: d.message_id, role: 'ai', ai_id: d.display_name || d.ai_id,
          content: d.content
        })
        scrollToBottom()
      },
      chat_anomaly: (d) => {
        ElMessage.warning(`监督员发现异常：${d.reason}，正在要求重写…`)
      },
      chat_rewrite: (d) => {
        messages.value.push({
          id: `rw-${Date.now()}`, role: 'ai', ai_id: d.ai_id,
          content: `【修正后发言】\n${d.content}`
        })
        scrollToBottom()
      },
      chat_ai_error: (d) => {
        ElMessage.error(`${d.ai_id} 回答失败：${d.message}`)
      },
      danger_blocked: (d) => {
        messages.value.push({
          id: `sys-${Date.now()}`, role: 'system',
          content: `⚠️ ${d.message}`
        })
        scrollToBottom()
      },
      onDone: () => {}
    })
  } catch (e) {
    ElMessage.error(e.message || '发送失败')
  } finally {
    isStreaming.value = false
  }
}

function scrollToBottom() {
  nextTick(() => {
    if (msgBox.value) msgBox.value.scrollTop = msgBox.value.scrollHeight
  })
}

onMounted(async () => {
  await loadModels()
  await loadSessions()
  if (sessions.value.length === 0) {
    await handleNewSession()
  } else {
    await handleSelectSession(sessions.value[0].id)
  }
})
</script>

<style scoped>
.chat-view {
  display: flex;
  gap: 16px;
  height: calc(100vh - 140px);
  min-height: 500px;
}
.session-panel {
  width: 220px;
  flex-shrink: 0;
  background: #fff;
  border-radius: 8px;
  border: 1px solid #e4e7ed;
  padding: 12px;
  overflow-y: auto;
}
.new-btn {
  width: 100%;
  margin-bottom: 12px;
}
.session-item {
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 6px;
}
.session-item:hover {
  background: #f5f7fa;
}
.session-item.active {
  background: #ecf5ff;
}
.session-title {
  font-size: 13px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.session-meta {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}
.chat-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 8px;
  border: 1px solid #e4e7ed;
  overflow: hidden;
}
.chat-header {
  padding: 12px 16px;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 600;
}
.header-actions {
  display: flex;
  gap: 12px;
  font-weight: 400;
  align-items: center;
}
.model-picker {
  display: flex;
  align-items: center;
  gap: 6px;
}
.picker-label {
  font-size: 12px;
  color: #909399;
}
.model-select {
  width: 170px;
}
.solo-tip {
  font-size: 12px;
  color: #e6a23c;
}
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  background: #fafafa;
}
.message-row {
  display: flex;
  margin-bottom: 12px;
}
.message-row.user {
  justify-content: flex-end;
}
.bubble {
  max-width: 72%;
  padding: 10px 14px;
  border-radius: 10px;
  background: #fff;
  border: 1px solid #e4e7ed;
}
.message-row.user .bubble {
  background: #409eff;
  color: #fff;
  border: none;
}
.message-row.system .bubble {
  background: #fdf6ec;
  border-color: #faecd8;
  max-width: 100%;
}
.bubble-head {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.bubble-body {
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.input-area {
  display: flex;
  gap: 12px;
  padding: 12px 16px;
  border-top: 1px solid #e4e7ed;
  align-items: flex-end;
}
</style>
