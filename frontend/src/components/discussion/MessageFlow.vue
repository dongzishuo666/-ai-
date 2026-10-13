<template>
  <el-card shadow="never" class="message-card">
    <template #header>
      <span class="card-title">讨论区</span>
      <span v-if="topic" class="topic-text">{{ topic }}</span>
    </template>
    <div ref="flowRef" class="message-flow">
      <div v-if="messages.length === 0" class="empty-tip">
        <el-empty description="等待讨论开始..." :image-size="80" />
      </div>

      <template v-else>
        <template v-for="(msg, index) in messages" :key="msg.message_id || ('m_' + index)">
          <!-- 常规 AI 发言 -->
          <div
            v-if="!msg.kind"
            class="message-item"
            :class="`compliance-${msg.compliance_status}`"
          >
            <div class="msg-header">
              <span class="msg-name">{{ msg.display_name }}</span>
              <span class="msg-round">第 {{ msg.round }} 轮</span>
              <el-tag
                v-if="msg.compliance_status && msg.compliance_status !== 'pending'"
                size="small"
                :type="complianceType[msg.compliance_status]"
              >
                {{ complianceText[msg.compliance_status] }}
              </el-tag>
            </div>
            <div class="msg-content">{{ msg.content }}</div>
            <div v-if="msg.compliance_reason" class="msg-violation">
              ⚠️ {{ msg.compliance_reason }}
            </div>
          </div>

          <!-- 蒸馏分隔标记：开始总结 -->
          <div v-else-if="msg.kind === 'distill_summary' ||
                            msg.kind === 'distill_summary_failed' ||
                            msg.kind === 'distill_judge' ||
                            msg.kind === 'distill_judge_failed' ||
                            msg.kind === 'frame_evolved'"
               class="distill-card"
               :class="distillCardClass(msg.kind)">
            <div class="distill-head" @click="toggle(msg.message_id)">
              <span class="distill-icon">{{ distillIcon(msg.kind) }}</span>
              <span class="distill-title">{{ distillTitle(msg) }}</span>
              <el-tag
                v-if="msg.kind === 'distill_summary_failed' || msg.kind === 'distill_judge_failed'"
                size="small"
                type="danger"
              >失败</el-tag>
              <span class="distill-toggle">{{ isExpanded(msg.message_id) ? '收起' : '展开' }}</span>
            </div>
            <div v-show="isExpanded(msg.message_id)" class="distill-body">
              <!-- 裁判总结：后端已切成四节，这里渲染成结构化卡片 -->
              <div v-if="judgeSections(msg).length" class="judge-sections">
                <div
                  v-for="(s, i) in judgeSections(msg)"
                  :key="s.key"
                  class="judge-sec"
                  :style="{ borderLeftColor: s.color }"
                >
                  <div class="judge-sec-head">
                    <span class="judge-sec-idx" :style="{ background: s.color }">
                      {{ i + 1 }}
                    </span>
                    <span class="judge-sec-title">{{ s.title }}</span>
                  </div>
                  <div class="judge-sec-body">{{ s.content }}</div>
                </div>
                <div v-if="msg.memory_id" class="judge-memory-tip">
                  已存入记忆库（#{{ msg.memory_id }}），可在「记忆库」页检索查看
                </div>
              </div>
              <pre v-else-if="msg.summary">{{ msg.summary }}</pre>
              <div v-else-if="msg.kind === 'frame_evolved'" class="frame-evolved">
                认知框架已演化至第 {{ msg.generation }} 代
                <span v-if="msg.frame_id">（frame #{{ msg.frame_id }}）</span>
              </div>
              <div v-else-if="msg.error" class="distill-error">
                ⚠️ {{ msg.error }}
              </div>
            </div>
          </div>

          <!-- 蒸馏流程信息条：跳过 / 取消 / 错误 -->
          <div
            v-else-if="msg.kind === 'distill_skip' ||
                       msg.kind === 'distill_cancelled' ||
                       msg.kind === 'distill_error'"
            class="distill-info"
            :class="`distill-info-${msg.kind.split('_')[1]}`"
          >
            <el-icon :size="14"><InfoFilled /></el-icon>
            <span>{{ distillInfoText(msg) }}</span>
          </div>

          <!-- 弹劾通过：裁判换人系统提示 -->
          <div v-else-if="msg.kind === 'judge_replaced'" class="system-notice">
            <el-icon :size="14"><Switch /></el-icon>
            <span>{{ msg.content }}</span>
          </div>
        </template>
      </template>
    </div>
  </el-card>
</template>

<script setup>
import { ref, watch, nextTick } from 'vue'
import { InfoFilled, Switch } from '@element-plus/icons-vue'

const props = defineProps({
  messages: {
    type: Array,
    default: () => []
  },
  topic: {
    type: String,
    default: ''
  }
})

const flowRef = ref(null)
const expanded = ref(new Set())

function toggle(id) {
  if (expanded.value.has(id)) expanded.value.delete(id)
  else expanded.value.add(id)
  // 触发响应式更新
  expanded.value = new Set(expanded.value)
}
function isExpanded(id) {
  return expanded.value.has(id)
}

const complianceType = {
  passed: 'success',
  warning: 'warning',
  violation: 'danger',
  pending: 'info'
}

const complianceText = {
  passed: '合规',
  warning: '警告',
  violation: '违规',
  pending: '审核中'
}

// 默认展开所有蒸馏卡片（新内容优先展示）
watch(() => props.messages, (newMsgs) => {
  if (!newMsgs?.length) return
  for (const m of newMsgs) {
    if (m.kind && (m.kind === 'distill_summary' || m.kind === 'distill_judge')) {
      expanded.value.add(m.message_id)
    }
  }
  expanded.value = new Set(expanded.value)
}, { deep: true })

// 自动滚动到底部
watch(() => props.messages.length, () => {
  nextTick(() => {
    if (flowRef.value) {
      flowRef.value.scrollTop = flowRef.value.scrollHeight
    }
  })
})

function distillCardClass(kind) {
  if (kind === 'distill_summary') return 'distill-summary'
  if (kind === 'distill_summary_failed') return 'distill-failed'
  if (kind === 'distill_judge') return 'distill-judge'
  if (kind === 'distill_judge_failed') return 'distill-failed'
  if (kind === 'frame_evolved') return 'distill-frame'
  return ''
}

function distillIcon(kind) {
  if (kind === 'distill_summary') return '📝'
  if (kind === 'distill_judge') return '⚖️'
  if (kind === 'frame_evolved') return '🧠'
  if (kind === 'distill_summary_failed') return '⚠️'
  if (kind === 'distill_judge_failed') return '⚠️'
  return '📌'
}

function distillTitle(msg) {
  if (msg.kind === 'distill_summary') {
    return `${msg.display_name || msg.ai_id} 的 13 部分总结`
  }
  if (msg.kind === 'distill_judge') {
    return `裁判总结（${msg.judge_ai || '未知裁判'}）`
  }
  if (msg.kind === 'frame_evolved') {
    return '认知框架已演化'
  }
  if (msg.kind === 'distill_summary_failed') {
    return `${msg.display_name || msg.ai_id} 生成失败`
  }
  if (msg.kind === 'distill_judge_failed') {
    return `裁判总结失败（${msg.judge_ai || ''}）`
  }
  return ''
}

// 裁判总结四节（与后端 memory_service.JUDGE_SECTIONS 对齐）
const JUDGE_SECTIONS = [
  { key: 'conclusion', title: '最终结论', color: '#409eff' },
  { key: 'contributions', title: '各方贡献评估', color: '#67c23a' },
  { key: 'disputes', title: '争议点裁决', color: '#e6a23c' },
  { key: 'suggestions', title: '后续建议', color: '#8e7cff' },
]

// 只有裁判总结且后端成功切分出四节时才走结构化渲染，其余退化为纯文本
function judgeSections(msg) {
  if (!msg || msg.kind !== 'distill_judge' || !msg.sections) return []
  return JUDGE_SECTIONS.filter((x) => msg.sections[x.key])
    .map((x) => ({ ...x, content: msg.sections[x.key] }))
}

function distillInfoText(msg) {
  if (msg.kind === 'distill_skip') return msg.message || '已跳过'
  if (msg.kind === 'distill_cancelled') return '蒸馏已取消'
  if (msg.kind === 'distill_error') return `蒸馏出错：${msg.error || ''}`
  return ''
}
</script>

<style scoped>
.card-title {
  font-weight: 600;
  font-size: 16px;
}
.topic-text {
  margin-left: 12px;
  font-size: 13px;
  color: #606266;
  font-weight: normal;
}
.message-card {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.message-card :deep(.el-card__body) {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 16px;
}
.message-flow {
  flex: 1;
  overflow-y: auto;
  padding-right: 8px;
}
.message-item {
  margin-bottom: 16px;
  padding: 12px 14px;
  background: #f8f9fc;
  border-radius: 8px;
  border-left: 3px solid #dcdfe6;
}
.system-notice {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 10px 0;
  padding: 7px 12px;
  font-size: 13px;
  color: #e6a23c;
  background: #fdf6ec;
  border-left: 3px solid #e6a23c;
  border-radius: 6px;
}
.message-item.compliance-passed {
  border-left-color: #67c23a;
}
.message-item.compliance-warning {
  border-left-color: #e6a23c;
  background: #fdf6ec;
}
.message-item.compliance-violation {
  border-left-color: #f56c6c;
  background: #fef0f0;
}
.msg-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
.msg-name {
  font-weight: 600;
  font-size: 14px;
  color: #303133;
}
.msg-round {
  font-size: 12px;
  color: #909399;
}
.msg-content {
  font-size: 14px;
  line-height: 1.7;
  color: #303133;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg-violation {
  margin-top: 8px;
  font-size: 12px;
  color: #f56c6c;
}
.empty-tip {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* 蒸馏卡片 */
.distill-card {
  margin: 14px 0;
  padding: 0;
  border-radius: 8px;
  background: #ffffff;
  border: 1px solid #ebeef5;
  overflow: hidden;
}
.distill-card.distill-summary { border-left: 3px solid #409eff; }
.distill-card.distill-judge { border-left: 3px solid #e6a23c; background: #fdf8ec; }
.distill-card.distill-frame { border-left: 3px solid #67c23a; background: #f0f9eb; }
.distill-card.distill-failed { border-left: 3px solid #f56c6c; background: #fef0f0; }

.distill-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  cursor: pointer;
  user-select: none;
}
.distill-head:hover { background: rgba(0, 0, 0, 0.02); }
.distill-icon { font-size: 16px; }
.distill-title {
  flex: 1;
  font-weight: 600;
  font-size: 13px;
  color: #303133;
}
.distill-toggle {
  font-size: 12px;
  color: #909399;
}
.distill-body {
  padding: 0 12px 12px 36px;
  border-top: 1px dashed #ebeef5;
}
.judge-sections {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.judge-sec {
  border-left: 3px solid #409eff;
  background: #fafafa;
  border-radius: 4px;
  padding: 8px 10px;
}
.judge-sec-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}
.judge-sec-idx {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  color: #fff;
  font-size: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.judge-sec-title {
  font-weight: 600;
  font-size: 13px;
  color: #303133;
}
.judge-sec-body {
  font-size: 12px;
  line-height: 1.7;
  color: #303133;
  white-space: pre-wrap;
  word-break: break-word;
}
.judge-memory-tip {
  font-size: 12px;
  color: #67c23a;
}
.distill-body pre {
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.7;
  color: #303133;
  margin: 8px 0 0 0;
  max-height: 360px;
  overflow-y: auto;
}
.distill-error {
  margin-top: 8px;
  font-size: 12px;
  color: #f56c6c;
}
.frame-evolved {
  font-size: 13px;
  color: #67c23a;
  padding: 8px 0;
}

/* 蒸馏流程信息条 */
.distill-info {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 10px 0;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;
}
.distill-info-skip {
  background: #fdf6ec;
  color: #b88230;
  border: 1px solid #faecd8;
}
.distill-info-cancelled {
  background: #f4f4f5;
  color: #909399;
  border: 1px solid #e9e9eb;
}
.distill-info-error {
  background: #fef0f0;
  color: #f56c6c;
  border: 1px solid #fde2e2;
}
</style>