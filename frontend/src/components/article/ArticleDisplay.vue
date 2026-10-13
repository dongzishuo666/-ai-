<template>
  <el-card shadow="never" v-loading="loading">
    <template #header>
      <span class="card-title">最终定稿</span>
      <div class="header-actions">
        <span v-if="wordCount" class="word-count">字数：{{ wordCount }}</span>
        <el-button
          v-if="content"
          type="primary"
          size="small"
          @click="handleCopy"
        >
          复制全文
        </el-button>
      </div>
    </template>
    <div v-if="content" class="markdown-content" v-html="renderedHtml"></div>
    <el-empty v-else description="暂无内容" :image-size="80" />
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { renderMarkdown } from '@/utils/markdown'

const props = defineProps({
  content: {
    type: String,
    default: ''
  },
  wordCount: {
    type: Number,
    default: 0
  },
  loading: {
    type: Boolean,
    default: false
  }
})

const renderedHtml = computed(() => renderMarkdown(props.content))

const handleCopy = async () => {
  try {
    await navigator.clipboard.writeText(props.content)
    ElMessage.success('已复制到剪贴板')
  } catch (e) {
    ElMessage.error('复制失败')
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
  gap: 16px;
}
.word-count {
  font-size: 13px;
  color: #909399;
}
.markdown-content {
  line-height: 1.8;
  color: #303133;
  padding: 8px 0;
}
.markdown-content :deep(h2) {
  font-size: 18px;
  margin: 20px 0 12px;
  border-bottom: 1px solid #ebeef5;
  padding-bottom: 6px;
}
.markdown-content :deep(h3) {
  font-size: 16px;
  margin: 16px 0 8px;
}
.markdown-content :deep(p) {
  margin: 10px 0;
  text-indent: 2em;
}
.markdown-content :deep(code) {
  background: #f5f7fa;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 13px;
}
.markdown-content :deep(pre) {
  background: #f5f7fa;
  padding: 12px;
  border-radius: 6px;
  overflow-x: auto;
  margin: 12px 0;
}
</style>
