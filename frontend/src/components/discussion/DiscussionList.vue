<template>
  <el-card shadow="never" class="list-card">
    <template #header>
      <span class="card-title">历史记录</span>
    </template>

    <el-table
      v-loading="store.listLoading"
      :data="store.list"
      stripe
      style="width: 100%"
      @row-click="handleRowClick"
    >
      <el-table-column prop="topic" label="主题" min-width="160" show-overflow-tooltip />

      <el-table-column prop="status" label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="statusType[row.status]" size="small">
            {{ statusText[row.status] }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column label="轮次" width="70" align="center">
        <template #default="{ row }">
          {{ row.rounds_completed || 0 }} / {{ row.total_rounds }}
        </template>
      </el-table-column>

      <el-table-column prop="created_at" label="创建时间" width="140">
        <template #default="{ row }">
          {{ formatTime(row.created_at) }}
        </template>
      </el-table-column>

      <el-table-column label="操作" width="70" align="right">
        <template #default="{ row }">
          <el-button
            v-if="['done', 'terminated', 'failed'].includes(row.status)"
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
        v-model:current-page="store.listPagination.page"
        v-model:page-size="store.listPagination.pageSize"
        :total="store.listPagination.total"
        layout="total, prev, pager, next"
        :page-sizes="[10, 20, 50]"
        @current-change="handlePageChange"
      />
    </div>

    <el-empty v-if="!store.listLoading && store.list.length === 0" description="暂无历史记录" />
  </el-card>
</template>

<script setup>
import { ElMessageBox, ElMessage } from 'element-plus'
import { useDiscussionStore } from '@/stores/discussionStore'

const store = useDiscussionStore()
const emit = defineEmits(['select'])

const statusText = {
  created: '待开始',
  running: '进行中',
  paused: '已暂停',
  done: '已结束',
  terminated: '已终止',
  failed: '失败'
}

const statusType = {
  done: 'success',
  running: 'primary',
  paused: 'warning',
  terminated: 'info',
  failed: 'danger',
  created: 'info'
}

const formatTime = (timeStr) => {
  if (!timeStr) return '-'
  return new Date(timeStr).toLocaleString('zh-CN', { hour12: false })
}

const handleRowClick = (row) => {
  emit('select', row.discussion_id)
}

const handlePageChange = (page) => {
  store.fetchList(page)
}

const handleDelete = (row) => {
  ElMessageBox.confirm(`确定删除讨论「${row.topic}」吗？`, '提示', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'warning'
  }).then(() => {
    store.deleteDiscussion(row.discussion_id)
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
.pagination-wrap {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
</style>
