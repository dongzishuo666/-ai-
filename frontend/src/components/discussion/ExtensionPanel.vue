<template>
  <div class="extension-panel">
    <!-- 弹劾区 -->
    <div class="section">
      <div class="section-title">
        弹劾裁判
        <el-tooltip content="过半数讨论者同意后，该裁判将被替换" placement="top">
          <el-icon :size="14"><InfoFilled /></el-icon>
        </el-tooltip>
      </div>
      <div class="impeach-form">
        <el-select
          v-model="impeachForm.initiator_ai_id"
          placeholder="发起者"
          size="small"
          style="width: 130px"
        >
          <el-option
            v-for="p in store.participants"
            :key="p.ai_id"
            :label="p.display_name || p.ai_id"
            :value="p.ai_id"
          />
        </el-select>
        <el-select v-model="impeachForm.target_role" placeholder="裁判角色" size="small" style="width: 120px">
          <el-option label="合规裁判" value="compliance" />
          <el-option label="秩序裁判" value="order" />
          <el-option label="评价裁判" value="evaluation" />
        </el-select>
        <el-input
          v-model="impeachForm.reason"
          size="small"
          placeholder="弹劾理由（必填，5字以上）"
          style="flex: 1"
        />
        <el-button
          size="small"
          type="danger"
          :loading="isImpeaching"
          :disabled="!canImpeach"
          @click="handleImpeach"
        >
          发起弹劾
        </el-button>
      </div>

      <!-- 弹劾记录 -->
      <div v-if="impeachments.length" class="impeach-list">
        <div v-for="imp in impeachments" :key="imp.id" class="impeach-item">
          <el-tag
            :type="imp.result === 'passed' ? 'success' : imp.result === 'rejected' ? 'info' : 'warning'"
            size="small"
          >
            {{ { passed: '通过', rejected: '驳回', pending: '进行中' }[imp.result] || imp.result }}
          </el-tag>
          <span class="imp-text">
            {{ imp.target_judge_role }}裁判（{{ imp.target_ai_id }}）·
            同意 {{ imp.agree_count }} / 反对 {{ imp.disagree_count }}
            <template v-if="imp.replacement_ai_id">
              · 替补：{{ imp.replacement_ai_id }}
            </template>
          </span>
        </div>
      </div>
    </div>

    <el-divider />

    <!-- 总结并记忆区（按工作流程文档对齐：蒸馏 + 取消 + 仅裁判总结） -->
    <div class="section">
      <div class="section-title">
        总结并记忆
        <span class="sub">讨论者13部分总结 · 裁判总结 · 写入本地记忆库</span>
      </div>

      <div class="distill-actions">
        <el-button
          v-if="store.distillPhase === 'idle'"
          size="small"
          type="primary"
          :disabled="!store.discussionId"
          @click="onStartDistill"
        >
          总结并记忆
        </el-button>

        <el-button
          v-else
          size="small"
          type="warning"
          :loading="isCancelling"
          @click="onCancelDistill"
        >
          取消总结
        </el-button>

        <el-button
          v-if="store.distillPhase === 'idle'"
          size="small"
          :disabled="!store.discussionId"
          @click="onStartJudgeSummary"
        >
          裁判总结
        </el-button>
      </div>

      <div v-if="distillStatus" class="distill-status">{{ distillStatus }}</div>

      <!-- 记忆库计数提示（后端持久化） -->
      <div class="memory-hint">
        记忆库共 {{ memoryCount }} 条（已存后端）
        <el-button
          size="small"
          link
          type="info"
          @click="showMemoryDialog = true"
        >查看</el-button>
        <el-button size="small" link type="primary" @click="goMemoryPage">
          打开记忆库
        </el-button>
      </div>
    </div>

    <!-- 记忆库对话框 -->
    <el-dialog
      v-model="showMemoryDialog"
      title="记忆库（后端持久化）"
      width="640px"
    >
      <div v-if="memoryItems.length === 0" class="empty-tip">暂无记忆</div>
      <div v-else class="memory-list">
        <div
          v-for="item in memoryItems"
          :key="item.id"
          class="memory-item"
        >
          <div class="memory-head">
            <el-tag size="small" :type="item.memory_type === 'judge' ? 'warning' : 'success'">
              {{ item.memory_type === 'judge' ? '裁判总结' : '讨论总结' }}
            </el-tag>
            <span class="memory-time">{{ formatTime(item.created_at) }}</span>
            <el-button size="small" link type="danger" @click="onDeleteMemory(item.id)">删除</el-button>
          </div>
          <div v-if="item.judge_model || item.display_name" class="memory-meta">
            {{ item.judge_model ? `裁判模型：${item.judge_model}` : `总结者：${item.display_name}` }}
          </div>
          <pre class="memory-body">{{ item.content }}</pre>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { reactive, ref, computed, onMounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { InfoFilled } from '@element-plus/icons-vue'
import { useDiscussionStore } from '@/stores/discussionStore'
import {
  impeachJudge,
  getImpeachments,
} from '@/api/extensions'
import { useRouter } from 'vue-router'
import { getMemoryList, deleteMemory } from '@/api/memory'

const store = useDiscussionStore()
const router = useRouter()
const showMemoryDialog = ref(false)
const memoryItems = ref([])
const memoryCount = ref(0)

// 记忆已改为后端持久化（memories 表）：数据源是接口，不再读 localStorage
async function refreshMemoryItems() {
  try {
    const res = await getMemoryList({ page: 1, page_size: 20 })
    memoryItems.value = res?.items || []
    memoryCount.value = res?.total || 0
  } catch (e) {
    memoryItems.value = []
    memoryCount.value = 0
  }
}

function goMemoryPage() {
  showMemoryDialog.value = false
  router.push('/memory')
}

function formatTime(iso) {
  try {
    const d = new Date(iso)
    return d.toLocaleString('zh-CN', { hour12: false })
  } catch (e) { return iso }
}

async function onDeleteMemory(id) {
  try {
    await ElMessageBox.confirm('确认删除该记忆条目？', '提示', {
      type: 'warning',
    })
  } catch (e) {
    return // 用户取消
  }
  try {
    await deleteMemory(id)
  } catch (e) {
    return // request.js 已弹出后端中文提示
  }
  ElMessage.success('已删除')
  refreshMemoryItems()
}

// ---------------- 弹劾（原逻辑保留） ----------------
const impeachForm = reactive({
  initiator_ai_id: '',
  target_role: 'compliance',
  reason: '',
})
const isImpeaching = ref(false)
const impeachments = ref([])

// 未手动选择发起者时，默认取第一位讨论者
watch(
  () => store.participants,
  (list) => {
    if (!impeachForm.initiator_ai_id && list?.length) {
      impeachForm.initiator_ai_id = list[0].ai_id
    }
  },
  { immediate: true, deep: false }
)

const canImpeach = computed(() => {
  if (!store.discussionId) return false
  if (store.status !== 'running' && store.status !== 'paused') return false
  if (!impeachForm.initiator_ai_id) return false
  if (impeachForm.reason.trim().length < 5) return false
  return true
})

async function handleImpeach() {
  if (!canImpeach.value) return
  isImpeaching.value = true
  try {
    await impeachJudge(store.discussionId, {
      initiator_ai_id: impeachForm.initiator_ai_id,
      target_role: impeachForm.target_role,
      reason: impeachForm.reason.trim(),
    })
    ElMessage.success('弹劾投票已发起')
    impeachForm.reason = ''
    await loadImpeachments()
  } catch (e) {
    // request.js 已统一弹出后端返回的中文提示，此处不重复弹窗
    console.error('弹劾失败', e)
  } finally {
    isImpeaching.value = false
  }
}

async function loadImpeachments() {
  if (!store.discussionId) return
  try {
    // request.js 已解包 { code, message, data }，此处 res 就是 data 本体
    const res = await getImpeachments(store.discussionId)
    impeachments.value = res?.items || []
  } catch (e) {
    impeachments.value = []
  }
}

// ---------------- 蒸馏（总结并记忆） ----------------
const distillStatus = computed(() => {
  switch (store.distillPhase) {
    case 'individual': return '正在生成讨论者13部分总结...'
    case 'judging': return '正在生成裁判总结...'
    case 'cancelling': return '正在取消...'
    case 'cancelled': return '已取消'
    case 'skipped': return '已跳过（请查看聊天区说明）'
    case 'done': return '已完成'
    default: return ''
  }
})

function onStartDistill() {
  store.startDistill()
}
function onStartJudgeSummary() {
  store.startJudgeSummary()
}
// store.distillPhase 不存在 'cancelling' 取值，用本地 ref 驱动按钮 loading
const isCancelling = ref(false)
async function onCancelDistill() {
  isCancelling.value = true
  try {
    await store.cancelDistill()
  } finally {
    isCancelling.value = false
  }
}

onMounted(async () => {
  await loadImpeachments()
  refreshMemoryItems()
})

// 监听记忆库对话框开启时刷新
watch(showMemoryDialog, (v) => { if (v) refreshMemoryItems() })
</script>

<style scoped>
.extension-panel {
  padding: 0 4px;
}
.section-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  font-size: 14px;
  margin-bottom: 8px;
}
.section-title .sub {
  margin-left: auto;
  font-weight: normal;
  font-size: 12px;
  color: #909399;
}
.impeach-form {
  display: flex;
  gap: 8px;
  align-items: center;
}
.impeach-list {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.impeach-item {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 12px;
}
.distill-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.distill-status {
  margin-top: 10px;
  font-size: 13px;
  color: #409eff;
}
.memory-hint {
  margin-top: 10px;
  font-size: 12px;
  color: #909399;
}
.memory-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 60vh;
  overflow-y: auto;
}
.memory-item {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 10px;
}
.memory-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.memory-time {
  font-size: 12px;
  color: #909399;
}
.memory-meta {
  font-size: 12px;
  color: #606266;
  margin-bottom: 4px;
}
.memory-body {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.6;
  color: #303133;
  margin: 0;
  max-height: 200px;
  overflow-y: auto;
}
.empty-tip {
  text-align: center;
  color: #909399;
  padding: 20px;
}
</style>