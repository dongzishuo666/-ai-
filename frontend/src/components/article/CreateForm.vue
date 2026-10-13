<template>
  <el-card shadow="never">
    <template #header>
      <span class="card-title">创作需求</span>
    </template>
    <el-form
      ref="formRef"
      :model="form"
      label-width="100px"
      @submit.prevent
    >
      <el-form-item
        label="文章主题"
        prop="topic"
        :rules="[{ required: true, message: '请输入文章主题', trigger: 'blur' }]"
      >
        <el-input
          v-model="form.topic"
          placeholder="请输入文章主题，如：大模型时代的软件工程变革"
          maxlength="200"
          show-word-limit
        />
      </el-form-item>

      <el-form-item label="文体类型">
        <el-select v-model="form.genre" style="width: 100%">
          <el-option label="技术博客" value="tech_blog" />
          <el-option label="商业文案" value="business" />
          <el-option label="学术论文" value="academic" />
          <el-option label="散文随笔" value="essay" />
          <el-option label="新闻稿" value="news" />
          <el-option label="教程指南" value="tutorial" />
          <el-option label="通用文章" value="general" />
        </el-select>
      </el-form-item>

      <el-form-item label="目标字数">
        <el-slider
          v-model="form.target_word_count"
          :min="500"
          :max="10000"
          :step="100"
          show-input
          style="width: 80%"
        />
        <span class="unit">字</span>
      </el-form-item>

      <el-form-item label="风格要求">
        <el-input
          v-model="form.style"
          placeholder="如：专业严谨但易读"
          maxlength="100"
        />
      </el-form-item>

      <el-form-item label="目标读者">
        <el-input
          v-model="form.audience"
          placeholder="如：中高级开发者"
          maxlength="100"
        />
      </el-form-item>

      <el-form-item label="额外要求">
        <el-input
          v-model="form.additional_requirements"
          type="textarea"
          :rows="3"
          placeholder="其他补充要求，如：需要包含实际案例和代码片段"
          maxlength="500"
          show-word-limit
        />
      </el-form-item>

      <el-form-item label="写作模型">
        <div class="role-block">
          <div v-for="slot in roleSlots" :key="slot.key" class="role-row">
            <span class="role-label">{{ slot.label }}</span>
            <el-select v-model="roles[slot.key]" size="small" class="role-select">
              <el-option
                v-for="m in modelList"
                :key="m.ai_id"
                :label="m.display_name"
                :value="m.ai_id"
              />
            </el-select>
          </div>
          <div class="role-hint">
            默认全部使用 DeepSeek（成本最低），可逐级改成别的模型
          </div>
        </div>
      </el-form-item>

      <el-form-item>
        <el-button
          type="primary"
          :loading="loading"
          @click="handleSubmit"
        >
          开始生成
        </el-button>
        <el-button @click="handleReset">重置</el-button>
      </el-form-item>
    </el-form>
  </el-card>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { getAIStatus } from '@/api/system'

const emit = defineEmits(['submit'])

const formRef = ref()
const loading = ref(false)

// 三级写作各自的模型：默认 DeepSeek（成本最低），用户可逐级自选
const roleSlots = [
  { key: 'outliner', label: '大纲' },
  { key: 'writer', label: '正文' },
  { key: 'reviewer', label: '审校' }
]

const roles = reactive({
  outliner: 'deepseek',
  writer: 'deepseek',
  reviewer: 'deepseek'
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

const form = reactive({
  topic: '',
  genre: 'tech_blog',
  target_word_count: 2000,
  style: '',
  audience: '',
  additional_requirements: ''
})

const handleSubmit = async () => {
  if (!form.topic.trim()) {
    formRef.value.validateField('topic')
    return
  }
  loading.value = true
  emit('submit', { ...form, roles: { ...roles } })
  loading.value = false
}

const handleReset = () => {
  formRef.value.resetFields()
  form.target_word_count = 2000
}
</script>

<style scoped>
.card-title {
  font-weight: 600;
  font-size: 16px;
}
.unit {
  margin-left: 8px;
  color: #606266;
}
.role-block {
  width: 100%;
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
