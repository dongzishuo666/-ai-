<template>
  <el-card shadow="never">
    <template #header>
      <span class="card-title">上传文档</span>
    </template>

    <div
      class="upload-area"
      :class="{ 'drag-over': isDragOver }"
      @dragover.prevent="isDragOver = true"
      @dragleave.prevent="isDragOver = false"
      @drop.prevent="handleDrop"
      @click="triggerFileInput"
    >
      <input
        ref="fileInputRef"
        type="file"
        :accept="acceptFormats"
        hidden
        @change="handleFileChange"
      >
      <el-icon :size="48" color="#409eff" class="mb-12">
        <Upload />
      </el-icon>
      <div class="upload-text">点击或拖拽文件到此处上传</div>
      <div class="upload-tip">支持 PDF / DOCX / TXT / MD 格式，最大 10MB</div>
    </div>

    <div v-if="fileInfo" class="file-info">
      <el-icon color="#67c23a"><Document /></el-icon>
      <div class="file-detail">
        <div class="file-name">{{ fileInfo.filename }}</div>
        <div class="file-meta">
          <span>{{ formatType(fileInfo.doc_type) }}</span>
          <span>·</span>
          <span>{{ formatSize(fileInfo.file_size) }}</span>
          <span v-if="fileInfo.char_count">·</span>
          <span v-if="fileInfo.char_count">{{ fileInfo.char_count }} 字符</span>
        </div>
      </div>
      <el-button link type="danger" @click.stop="handleRemove">移除</el-button>
    </div>
  </el-card>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Upload, Document } from '@element-plus/icons-vue'

const emit = defineEmits(['upload-success'])

const fileInputRef = ref()
const isDragOver = ref(false)
const fileInfo = ref(null)

const acceptFormats = '.pdf,.docx,.txt,.md'
const maxSize = 10 * 1024 * 1024 // 10MB

const formatType = (type) => {
  const map = { pdf: 'PDF', docx: 'Word', txt: '文本', md: 'Markdown' }
  return map[type] || type.toUpperCase()
}

const formatSize = (bytes) => {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(2) + ' MB'
}

const triggerFileInput = () => {
  fileInputRef.value.click()
}

const handleDrop = (e) => {
  isDragOver.value = false
  const files = e.dataTransfer.files
  if (files.length) {
    validateAndUpload(files[0])
  }
}

const handleFileChange = (e) => {
  const file = e.target.files[0]
  if (file) {
    validateAndUpload(file)
  }
}

const validateAndUpload = (file) => {
  // 格式校验
  const ext = file.name.split('.').pop().toLowerCase()
  const validExts = ['pdf', 'docx', 'txt', 'md']
  if (!validExts.includes(ext)) {
    ElMessage.error('仅支持 PDF / DOCX / TXT / MD 格式')
    return
  }

  // 大小校验
  if (file.size > maxSize) {
    ElMessage.error('文件大小不能超过 10MB')
    return
  }

  // 空文件校验
  if (file.size === 0) {
    ElMessage.error('文件内容为空，请检查')
    return
  }

  emit('upload-success', file)
  fileInfo.value = {
    filename: file.name,
    file_size: file.size
  }
}

const handleRemove = () => {
  fileInfo.value = null
  fileInputRef.value.value = ''
  emit('remove')
}

defineExpose({
  setFileInfo: (info) => {
    fileInfo.value = info
  }
})
</script>

<style scoped>
.card-title {
  font-weight: 600;
  font-size: 16px;
}
.upload-area {
  border: 2px dashed #dcdfe6;
  border-radius: 8px;
  padding: 40px 20px;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s;
  background: #fafafa;
}
.upload-area:hover,
.upload-area.drag-over {
  border-color: #409eff;
  background: #f0f7ff;
}
.upload-text {
  font-size: 15px;
  color: #303133;
  margin-bottom: 8px;
}
.upload-tip {
  font-size: 13px;
  color: #909399;
}
.mb-12 { margin-bottom: 12px; }
.file-info {
  margin-top: 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  background: #f5f7fa;
  border-radius: 6px;
}
.file-detail {
  flex: 1;
  min-width: 0;
}
.file-name {
  font-size: 14px;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-meta {
  font-size: 12px;
  color: #909399;
  margin-top: 4px;
  display: flex;
  gap: 4px;
}
</style>
