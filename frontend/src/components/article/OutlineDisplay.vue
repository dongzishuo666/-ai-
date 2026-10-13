<template>
  <el-card shadow="never" v-loading="loading">
    <template #header>
      <span class="card-title">文章大纲</span>
    </template>
    <div v-if="outline" class="markdown-content" v-html="renderedHtml"></div>
    <el-empty v-else description="暂无大纲内容" :image-size="80" />
  </el-card>
</template>

<script setup>
import { computed } from 'vue'
import { renderMarkdown } from '@/utils/markdown'

const props = defineProps({
  outline: {
    type: String,
    default: ''
  },
  loading: {
    type: Boolean,
    default: false
  }
})

const renderedHtml = computed(() => renderMarkdown(props.outline))
</script>

<style scoped>
.card-title {
  font-weight: 600;
  font-size: 16px;
}
.markdown-content {
  line-height: 1.8;
  color: #303133;
}
.markdown-content :deep(h2) {
  font-size: 18px;
  margin: 16px 0 8px;
}
.markdown-content :deep(h3) {
  font-size: 16px;
  margin: 12px 0 6px;
}
</style>
