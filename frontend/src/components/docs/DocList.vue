<template>
  <el-card shadow="never" class="list-card">
    <template #header>
      <span class="card-title">历史记录</span>
    </template>

    <el-table
      v-loading="docStore.listLoading"
      :data="docStore.list"
      stripe
      style="width: 100%"
      @row-click="handleRowClick"
    >
      <el-table-column prop="filename" label="文件名" min-width="140">
        <template #default="{ row }">
          <span class="file-name">{{ row.filename }}</span>
        </template>
      </el-table-column>

      <el-table-column prop="status" label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="statusType[row.status]" size="small">
            {{ statusText[row.status] }}
          </el-tag>
        </template>
      </el-table-column>

      <el-table-column prop="user_need" label="需求说明" min-width="140" show-overflow-tooltip />

      <el-table-column prop="created_at" label="创建时间" width="150">
        <template #default="{ row }">
          {{ formatTime(row.created_at) }}
        </template>
      </el-table-column>

      <el-table-column label="操作" width="70" align="right">
        <template #default="{ row }">
          <el-button
            v-if="row.status === 'done' || row.status === 'saved' || row.status === 'failed'"
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
        v-model:current-page="docStore.listPagination.page"
        v-model:page-size="docStore.listPagination.pageSize"
        :total="docStore.listPagination.total"
        layout="total, prev, pager, next"
        :page-sizes="[10, 20, 50]"
        @current-change="handlePageChange"
      />
    </div>

    <el-empty v-if="!docStore.listLoading && docStore.list.length === 0" description="暂无历史记录" />
  </el-card>
</template>

<script setup>
import { ElMessageBox, ElMessage } from 'element-plus'
import { useDocStore } from '@/stores/docStore'

const docStore = useDocStore()
const emit = defineEmits(['select'])

const statusText = {
  pending: '待处理',
  running: '处理中',
  done: '已完成',
  failed: '失败',
  saved: '已保存'
}

const statusType = {
  done: 'success',
  saved: 'success',
  failed: 'danger',
  pending: 'info',
  running: 'warning'
}

const formatTime = (timeStr) => {
  if (!timeStr) return '-'
  return new Date(timeStr).toLocaleString('zh-CN', { hour12: false })
}

const handleRowClick = (row) => {
  emit('select', row.task_id)
}

const handlePageChange = (page) => {
  docStore.fetchList(page)
}

const handleDelete = (row) => {
  ElMessageBox.confirm(`确定删除「${row.filename}」吗？`, '提示', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'warning'
  }).then(() => {
    docStore.deleteTask(row.task_id)
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
.file-name {
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
