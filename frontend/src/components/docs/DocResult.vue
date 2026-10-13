<template>
  <el-card shadow="never" v-loading="loading">
    <template #header>
      <span class="card-title">交付成品</span>
      <div class="header-actions">
        <el-button
          v-if="result && !isSaved"
          type="success"
          size="small"
          @click="handleSave"
        >
          保存成品
        </el-button>
        <el-tag v-if="isSaved" type="success" size="small">已保存</el-tag>
        <el-button
          v-if="result"
          type="primary"
          size="small"
          @click="handleCopy"
        >
          一键复制
        </el-button>
        <el-button
          v-if="result && taskId"
          size="small"
          @click="handleExport('docx')"
        >
          导出 Word
        </el-button>
        <el-button
          v-if="result && taskId"
          size="small"
          plain
          @click="handleExport('md')"
        >
          导出 Markdown
        </el-button>
      </div>
    </template>
    <div v-if="result" class="markdown-content" v-html="renderedHtml"></div>
    <el-empty v-else description="暂无成品" :image-size="80" />
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { renderMarkdown } from '@/utils/markdown'
import { exportDocUrl } from '@/api/docs'

const props = defineProps({
  // 成品正文（后端 final_prompt 字段，语义上是交付的成品）
  result: {
    type: String,
    default: ''
  },
  // 任务 ID：导出接口按任务取成品，没有它就只能复制
  taskId: {
    type: [Number, String],
    default: null
  },
  loading: {
    type: Boolean,
    default: false
  },
  isSaved: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['save'])

const renderedHtml = computed(() => renderMarkdown(props.result))

const handleCopy = async () => {
  try {
    await navigator.clipboard.writeText(props.result)
    ElMessage.success('已复制到剪贴板')
  } catch (e) {
    ElMessage.error('复制失败')
  }
}

const handleSave = () => {
  emit('save')
}

// 导出成品文件：先取 Blob 再触发下载，
// 这样后端返回 409（还没成品）时能弹出中文提示，而不是跳到一个 JSON 错误页
const handleExport = async (format) => {
  if (!props.taskId) {
    ElMessage.warning('任务未就绪，暂无法导出')
    return
  }
  try {
    const res = await fetch(exportDocUrl(props.taskId, format))
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      ElMessage.error(err.message || '导出失败')
      return
    }
    const blob = await res.blob()
    const cd = res.headers.get('Content-Disposition') || ''
    const matched = /filename\*=UTF-8''(.+)/.exec(cd)
    const filename = matched ? decodeURIComponent(matched[1]) : `成品.${format}`

    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = filename
    document.body.appendChild(link)
    link.click()
    URL.revokeObjectURL(link.href)
    link.remove()
    ElMessage.success('导出成功')
  } catch (e) {
    ElMessage.error('导出失败，请稍后重试')
  }
}
</script>

<style scoped>
.card-title {
  font-weight: 600;
  font-size: 16px;
}
.header-actions {
  float: right;
  display: flex;
  align-items: center;
  gap: 12px;
}
.markdown-content {
  line-height: 1.8;
  color: #303133;
  padding: 8px 0;
}
.markdown-content :deep(h1) {
  font-size: 20px;
  margin: 16px 0 12px;
  border-bottom: 2px solid #409eff;
  padding-bottom: 6px;
}
.markdown-content :deep(h2) {
  font-size: 17px;
  margin: 14px 0 8px;
}
.markdown-content :deep(h3) {
  font-size: 15px;
  margin: 10px 0 6px;
}
.markdown-content :deep(ul),
.markdown-content :deep(ol) {
  padding-left: 24px;
  margin: 8px 0;
}
.markdown-content :deep(li) {
  margin: 4px 0;
}
.markdown-content :deep(code) {
  background: #f5f7fa;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 13px;
}
</style>
