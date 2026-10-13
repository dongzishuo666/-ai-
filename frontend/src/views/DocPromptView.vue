<template>
  <div class="doc-page">
    <!-- 顶部模式切换 + 最终计分榜（随时可查看：本场榜 / 历史总榜） -->
    <div class="top-bar">
      <el-tabs v-model="activeMode" class="mode-tabs" @tab-change="handleModeChange">
        <el-tab-pane label="新建任务" name="create" />
        <el-tab-pane label="历史记录" name="history" />
      </el-tabs>
      <el-button
        size="small"
        type="success"
        @click="showScoreBoard = true"
      >
        最终计分榜
      </el-button>
    </div>

    <div class="page-content">
      <!-- 左侧面板 -->
      <div class="left-panel">
        <!-- 新建模式 -->
        <template v-if="docStore.viewMode === 'create'">
          <FileUploader
            ref="uploaderRef"
            @upload-success="handleUploadSuccess"
            @remove="handleRemoveFile"
          />

          <div v-if="docStore.taskId" class="mt-20">
            <el-card shadow="never">
              <template #header>
                <span class="card-title">需求描述</span>
              </template>
              <el-input
                v-model="needInput"
                type="textarea"
                :rows="4"
                placeholder="请描述你要什么成品，如：根据这份报告，写一份面向非技术人员的摘要说明"
                maxlength="500"
                show-word-limit
              />
              <div class="role-block">
                <div v-for="slot in roleSlots" :key="slot.key" class="role-row">
                  <span class="role-label">{{ slot.label }}</span>
                  <el-select
                    v-model="roles[slot.key]"
                    size="small"
                    class="role-select"
                  >
                    <el-option
                      v-for="m in modelList"
                      :key="m.ai_id"
                      :label="m.display_name"
                      :value="m.ai_id"
                    />
                  </el-select>
                </div>
                <div class="role-hint">
                  默认全部使用 DeepSeek（成本最低），可逐棒改成别的模型
                </div>
              </div>
              <div class="btn-wrap">
                <el-button
                  type="primary"
                  :disabled="!needInput.trim()"
                  :loading="docStore.status === 'running'"
                  @click="handleSubmit"
                >
                  生成成品
                </el-button>
              </div>
            </el-card>
          </div>

          <ProcessProgress
            v-if="docStore.status !== 'idle' && docStore.status !== 'uploading'"
            :steps="docStore.steps"
            :status="docStore.status"
            class="mt-20"
          />
        </template>

        <!-- 历史模式 -->
        <DocList v-else @select="handleSelectTask" />
      </div>

      <!-- 右侧结果面板 -->
      <div class="right-panel">
        <template v-if="showEmpty">
          <el-card shadow="never" class="empty-card">
            <el-empty :description="emptyText" />
          </el-card>
        </template>

        <template v-else>
          <DocResult
            :result="docStore.finalResult"
            :loading="docStore.status === 'running'"
            :is-saved="docStore.isSaved"
            :task-id="docStore.taskId"
            @save="handleSave"
          />

          <!-- 最终质量检测员：流水线跑完后给三个环节 AI 的质检榜 -->
          <QualityScoreCard
            :scores="docStore.qualityScores"
            :overall="docStore.qualityOverall"
            :summary="docStore.qualitySummary"
            class="mt-20"
            @view-history="showScoreBoard = true"
          />

          <el-alert
            v-if="docStore.status === 'failed'"
            :title="docStore.errorMessage || '处理失败'"
            type="error"
            show-icon
            class="mt-20"
          />
        </template>
      </div>
    </div>

    <!-- 质检榜弹窗：本场质检分 + 历史总榜 -->
    <ScoreBoardDialog
      v-model:visible="showScoreBoard"
      feature="doc"
      :task-id="docStore.taskId"
      :live-scores="docStore.qualityScores"
      :live-overall="docStore.qualityOverall"
      title="文档 · 质量检测榜"
    />
  </div>
</template>

<script setup>
import { ref, computed, onBeforeUnmount, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useDocStore } from '@/stores/docStore'
import { getAIStatus } from '@/api/system'
import FileUploader from '@/components/docs/FileUploader.vue'
import ProcessProgress from '@/components/docs/ProcessProgress.vue'
import DocResult from '@/components/docs/DocResult.vue'
import DocList from '@/components/docs/DocList.vue'
import QualityScoreCard from '@/components/common/QualityScoreCard.vue'
import ScoreBoardDialog from '@/components/common/ScoreBoardDialog.vue'

const docStore = useDocStore()
const uploaderRef = ref()
const needInput = ref('')
const showScoreBoard = ref(false)

// 三棒各自的模型：默认 DeepSeek（成本最低），用户可逐棒自选
const roleSlots = [
  { key: 'planner', label: '初稿' },
  { key: 'optimizer', label: '审校' },
  { key: 'finalizer', label: '终审' }
]
const roles = ref({
  planner: 'deepseek',
  optimizer: 'deepseek',
  finalizer: 'deepseek'
})

const FALLBACK_IDS = [
  'deepseek', 'zhipu', 'kimi', 'wenxin',
  'claude', 'openai', 'gemini', 'grok', 'spark', 'hunyuan'
]
const modelList = ref([])

const loadModels = async () => {
  try {
    const res = await getAIStatus()
    const list = (res?.models || []).map((m) => ({
      ai_id: m.ai_id,
      display_name: m.display_name || m.ai_id,
      status: m.status
    }))
    modelList.value = list.length
      ? list
      : FALLBACK_IDS.map((id) => ({ ai_id: id, display_name: id, status: '' }))
  } catch {
    modelList.value = FALLBACK_IDS.map((id) => ({
      ai_id: id, display_name: id, status: ''
    }))
  }
}

onMounted(loadModels)

const activeMode = computed(() => docStore.viewMode)

const showEmpty = computed(() => {
  if (docStore.viewMode === 'create') {
    return !docStore.finalResult && docStore.status !== 'running'
  } else {
    return !docStore.selectedId
  }
})

const emptyText = computed(() => {
  if (docStore.viewMode === 'create') {
    return '上传文档并输入需求，直接生成成品'
  } else {
    return '点击左侧记录查看详情'
  }
})

const handleModeChange = (mode) => {
  docStore.setViewMode(mode)
}

const handleUploadSuccess = async (file) => {
  try {
    await docStore.uploadFile(file)
    uploaderRef.value.setFileInfo(docStore.fileInfo)
  } catch (e) {
    // 错误已在 store 中处理
  }
}

const handleRemoveFile = () => {
  docStore._resetAll()
}

const handleSubmit = () => {
  if (!needInput.value.trim()) {
    ElMessage.warning('请输入需求描述')
    return
  }
  docStore.submitProcess(needInput.value.trim(), { ...roles.value })
}

const handleSave = async () => {
  const success = await docStore.saveResult()
  if (success) {
    ElMessage.success('保存成功')
  }
}

const handleSelectTask = (id) => {
  docStore.loadDetail(id)
}

onBeforeUnmount(() => {
  docStore.closeConnection()
})
</script>

<style scoped>
.doc-page {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.top-bar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.mode-tabs {
  flex: 1;
  margin-bottom: 16px;
}
.page-content {
  display: flex;
  gap: 20px;
  flex: 1;
  overflow: hidden;
}
.left-panel {
  width: 420px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}
.right-panel {
  flex: 1;
  overflow-y: auto;
}
.empty-card {
  height: 400px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.card-title {
  font-weight: 600;
  font-size: 16px;
}
.btn-wrap {
  margin-top: 12px;
  text-align: right;
}
.mt-20 { margin-top: 20px; }
.role-block {
  margin-top: 12px;
}
.role-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.role-label {
  width: 40px;
  flex-shrink: 0;
  font-size: 13px;
  color: #606266;
}
.role-select {
  flex: 1;
}
.role-hint {
  font-size: 12px;
  color: #909399;
}
</style>
