<template>
  <el-card shadow="never" class="list-card">
    <template #header>
      <span class="card-title">历史记录</span>
    </template>

    <el-table
      v-loading="articleStore.listLoading"
      :data="articleStore.list"
      stripe
      style="width: 100%"
      @row-click="handleRowClick"
    >
      <el-table-column prop="topic" label="主题" min-width="180">
        <template #default="{ row }">
          <span class="topic-text">{{ row.topic }}</span>
        </template>
      </el-table-column>

      <el-table-column prop="genre" label="文体" width="100">
        <template #default="{ row }">
          <el-tag size="small">{{ genreMap[row.genre] || row.genre }}</el-tag>
        </template>
      </el-table-column>

      <el-table-column prop="status" label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusType[row.status]" size="small">
            {{ statusText[row.status] }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column prop="created_at" label="创建时间" width="160">
        <template #default="{ row }">
          {{ formatTime(row.created_at) }}
        </template>
      </el-table-column>

      <el-table-column label="操作" width="80" align="right">
        <template #default="{ row }">
          <el-button
            type="danger"
            link
            size="small"
            @click.stop="handleDelete(row)"
          >
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pagination-wrap">
      <el-pagination
        v-model:current-page="articleStore.listPagination.page"
        v-model:page-size="articleStore.listPagination.pageSize"
        :total="articleStore.listPagination.total"
        layout="total, prev, pager, next"
        :page-sizes="[10, 20, 50]"
        @current-change="handlePageChange"
      />
    </div>

    <el-empty v-if="!articleStore.listLoading && articleStore.list.length === 0" description="暂无历史记录" />
  </el-card>
</template>

<script setup>
import { ElMessageBox, ElMessage } from 'element-plus'
import { useArticleStore } from '@/stores/articleStore'

const articleStore = useArticleStore()

const emit = defineEmits(['select'])

const genreMap = {
  tech_blog: '技术博客',
  business: '商业文案',
  academic: '学术论文',
  essay: '散文随笔',
  news: '新闻稿',
  tutorial: '教程指南',
  general: '通用文章'
}

const statusText = {
  created: '待处理',
  outlining: '生成大纲',
  outline_ready: '大纲完成',
  writing: '撰写初稿',
  revising: '审核定稿',
  done: '已完成',
  failed: '失败'
}

const statusType = {
  done: 'success',
  failed: 'danger',
  created: 'info',
  outlining: 'warning',
  writing: 'warning',
  revising: 'warning'
}

const formatTime = (timeStr) => {
  if (!timeStr) return '-'
  return new Date(timeStr).toLocaleString('zh-CN', { hour12: false })
}

const handleRowClick = (row) => {
  emit('select', row.article_id)
}

const handlePageChange = (page) => {
  articleStore.fetchList(page)
}

const handleDelete = (row) => {
  ElMessageBox.confirm(`确定删除文章「${row.topic}」吗？`, '提示', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'warning'
  }).then(() => {
    articleStore.removeArticle(row.article_id)
    ElMessage.success('删除成功')
  }).catch(() => {})
}
</script>

<style scoped>
.card-title {
  font-weight: 600;
  font-size: 16px;
}
.list-card {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.list-card :deep(.el-card__body) {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.topic-text {
  display: inline-block;
  width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pagination-wrap {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
</style>
