<template>
  <div class="memory-page">
    <!-- 概览统计 -->
    <el-row :gutter="16" class="stat-row">
      <el-col v-for="s in statCards" :key="s.label" :span="6">
        <el-card shadow="never" class="stat-card">
          <div class="stat-value" :style="{ color: s.color }">{{ s.value }}</div>
          <div class="stat-label">{{ s.label }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 检索 + 列表 -->
    <el-card v-loading="loading" shadow="never" class="list-card">
      <div class="toolbar">
        <el-input
          v-model="keyword"
          placeholder="检索记忆：结论 / 主题 / 模型 / 发言者"
          clearable
          style="max-width: 340px"
          @keyup.enter="onSearch"
          @clear="onSearch"
        >
          <template #prefix><el-icon><Search /></el-icon></template>
        </el-input>

        <el-select
          v-model="memoryType"
          placeholder="全部类型"
          clearable
          style="width: 150px"
          @change="onSearch"
        >
          <el-option label="裁判总结" value="judge" />
          <el-option label="讨论总结" value="discussion" />
          <el-option label="认知框架" value="frame" />
          <el-option label="手动记录" value="manual" />
        </el-select>

        <el-button type="primary" @click="onSearch">检索</el-button>
        <el-button @click="loadAll">
          <el-icon><Refresh /></el-icon>
        </el-button>

        <div class="toolbar-right">
          <span class="total-text">共 {{ total }} 条</span>
          <el-button type="danger" plain :disabled="!total" @click="onClear">清空</el-button>
        </div>
      </div>

      <el-empty
        v-if="!loading && !list.length"
        description="还没有记忆 —— 到「多AI讨论」页点「总结并记忆」，裁判总结会自动存入这里"
      />

      <div v-else class="memory-grid">
        <div
          v-for="item in list"
          :key="item.id"
          class="memory-card"
          @click="openDetail(item)"
        >
          <div class="card-head">
            <el-tag size="small" :type="typeTagType(item.memory_type)">
              {{ typeLabel(item.memory_type) }}
            </el-tag>
            <span class="card-time">{{ formatTime(item.created_at) }}</span>
            <el-button
              class="card-del"
              size="small"
              link
              type="danger"
              @click.stop="onDelete(item)"
            >
              删除
            </el-button>
          </div>

          <div v-if="item.topic" class="card-topic">
            <el-icon><ChatDotSquare /></el-icon>
            <span class="topic-text">{{ item.topic }}</span>
          </div>

          <div class="card-meta">
            <span v-if="item.judge_model">裁判模型：{{ item.judge_model }}</span>
            <span v-else-if="item.display_name">
              总结者：{{ item.display_name }}
            </span>
            <span v-if="item.source_id">· 讨论 #{{ item.source_id }}</span>
            <span>· {{ item.char_count || 0 }} 字</span>
          </div>

          <!-- 结构化四节预览（裁判总结） -->
          <div v-if="sectionList(item).length" class="section-preview">
            <div
              v-for="s in sectionList(item)"
              :key="s.key"
              class="preview-item"
            >
              <span class="preview-title" :style="{ borderColor: s.color }">
                {{ s.title }}
              </span>
              <span class="preview-text">{{ truncate(s.content, 70) }}</span>
            </div>
          </div>

          <!-- 无结构时退化为正文预览 -->
          <div v-else class="plain-preview">{{ truncate(item.content, 140) }}</div>

          <div class="card-foot">
            <span>#{{ item.id }}</span>
            <span class="view-more">查看详情 →</span>
          </div>
        </div>
      </div>

      <div v-if="total > pageSize" class="pager">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="prev, pager, next"
          background
          @current-change="loadList"
        />
      </div>
    </el-card>

    <!-- 详情抽屉 -->
    <el-drawer v-model="detailVisible" size="56%" :with-header="true">
      <template #header>
        <div class="drawer-title">
          <el-tag size="small" :type="typeTagType(detail.memory_type)">
            {{ typeLabel(detail.memory_type) }}
          </el-tag>
          <span>{{ detail.topic || '记忆详情' }}</span>
        </div>
      </template>

      <div v-if="detail.id" class="detail-body">
        <div class="detail-meta">
          <span>时间：{{ formatTime(detail.created_at) }}</span>
          <span v-if="detail.judge_model">裁判模型：{{ detail.judge_model }}</span>
          <span v-else-if="detail.display_name">总结者：{{ detail.display_name }}</span>
          <span v-if="detail.source_id">来源：讨论 #{{ detail.source_id }}</span>
          <span>字数：{{ detail.char_count || 0 }}</span>
        </div>

        <!-- 四节结构化卡片：可视化核心 -->
        <div v-if="sectionList(detail).length" class="section-blocks">
          <div
            v-for="(s, i) in sectionList(detail)"
            :key="s.key"
            class="section-block"
            :style="{ borderLeftColor: s.color }"
          >
            <div class="block-head">
              <span class="block-index" :style="{ background: s.color }">{{ i + 1 }}</span>
              <span class="block-title">{{ s.title }}</span>
            </div>
            <div class="block-body">{{ s.content }}</div>
          </div>
        </div>

        <el-divider content-position="left">完整原文</el-divider>
        <pre class="raw-body">{{ detail.content }}</pre>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search, Refresh, ChatDotSquare } from '@element-plus/icons-vue'
import {
  getMemoryList,
  getMemoryStats,
  deleteMemory,
  clearMemories,
} from '@/api/memory'

// 裁判总结四节：顺序与后端 memory_service.JUDGE_SECTIONS 一致
const JUDGE_SECTIONS = [
  { key: 'conclusion', title: '最终结论', color: '#409eff' },
  { key: 'contributions', title: '各方贡献评估', color: '#67c23a' },
  { key: 'disputes', title: '争议点裁决', color: '#e6a23c' },
  { key: 'suggestions', title: '后续建议', color: '#8e7cff' },
]

const loading = ref(false)
const list = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 12
const keyword = ref('')
const memoryType = ref('')
const detailVisible = ref(false)
const detail = reactive({})

const stats = ref({ total: 0, by_type: {}, recent_7d: 0, topic_count: 0 })

const statCards = computed(() => [
  { label: '记忆总数', value: stats.value.total ?? 0, color: '#303133' },
  { label: '裁判总结', value: stats.value.by_type?.judge ?? 0, color: '#409eff' },
  { label: '讨论总结', value: stats.value.by_type?.discussion ?? 0, color: '#67c23a' },
  { label: '近 7 天新增', value: stats.value.recent_7d ?? 0, color: '#e6a23c' },
])

function typeLabel(t) {
  return {
    judge: '裁判总结',
    discussion: '讨论总结',
    frame: '认知框架',
    manual: '手动记录',
  }[t] || t || '记忆'
}

function typeTagType(t) {
  return { judge: 'primary', discussion: 'success', frame: 'warning', manual: 'info' }[t] || 'info'
}

function sectionList(item) {
  const s = item?.sections
  if (!s) return []
  return JUDGE_SECTIONS.filter((x) => s[x.key]).map((x) => ({
    ...x,
    content: s[x.key],
  }))
}

function truncate(text, n) {
  if (!text) return ''
  const flat = String(text).replace(/\s+/g, ' ').trim()
  return flat.length > n ? `${flat.slice(0, n)}…` : flat
}

function formatTime(iso) {
  if (!iso) return ''
  try {
    // 后端存的是 UTC（...Z），直接 new Date 即可按本地时区展示
    return new Date(iso).toLocaleString('zh-CN', { hour12: false })
  } catch (e) {
    return iso
  }
}

async function loadList() {
  loading.value = true
  try {
    const res = await getMemoryList({
      keyword: keyword.value || undefined,
      memory_type: memoryType.value || undefined,
      page: page.value,
      page_size: pageSize,
    })
    list.value = res?.items || []
    total.value = res?.total || 0
  } catch (e) {
    list.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  try {
    stats.value = await getMemoryStats() || stats.value
  } catch (e) {
    /* 统计失败不阻断列表 */
  }
}

async function loadAll() {
  await Promise.all([loadList(), loadStats()])
}

function onSearch() {
  page.value = 1
  loadList()
}

function openDetail(item) {
  Object.assign(detail, item)
  detailVisible.value = true
}

async function onDelete(item) {
  try {
    await ElMessageBox.confirm(
      `确认删除这条${typeLabel(item.memory_type)}？删除后不可恢复。`,
      '删除记忆',
      { type: 'warning' }
    )
  } catch (e) {
    return // 用户取消
  }
  await deleteMemory(item.id)
  ElMessage.success('已删除')
  if (detailVisible.value && detail.id === item.id) detailVisible.value = false
  loadAll()
}

async function onClear() {
  try {
    await ElMessageBox.confirm(
      '确认清空当前筛选条件下的全部记忆？该操作不可恢复。',
      '清空记忆库',
      { type: 'warning' }
    )
  } catch (e) {
    return
  }
  await clearMemories(memoryType.value || undefined)
  ElMessage.success('已清空')
  page.value = 1
  loadAll()
}

onMounted(loadAll)
</script>

<style scoped>
.memory-page {
  max-width: 1280px;
  margin: 0 auto;
}

.stat-row {
  margin-bottom: 16px;
}

.stat-card {
  text-align: center;
  border-radius: 8px;
}

.stat-value {
  font-size: 28px;
  font-weight: 600;
  line-height: 1.2;
}

.stat-label {
  margin-top: 4px;
  font-size: 13px;
  color: #909399;
}

.list-card {
  border-radius: 8px;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.toolbar-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 12px;
}

.total-text {
  font-size: 13px;
  color: #909399;
}

.memory-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 14px;
}

.memory-card {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 14px;
  cursor: pointer;
  transition: box-shadow 0.2s, transform 0.2s;
  background: #fff;
}

.memory-card:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
  transform: translateY(-2px);
}

.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.card-time {
  font-size: 12px;
  color: #909399;
}

.card-del {
  margin-left: auto;
}

.card-topic {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.topic-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-meta {
  margin-top: 6px;
  font-size: 12px;
  color: #909399;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.section-preview {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.preview-item {
  display: flex;
  gap: 8px;
  align-items: baseline;
  font-size: 12px;
}

.preview-title {
  flex-shrink: 0;
  padding-left: 6px;
  border-left: 3px solid #409eff;
  font-weight: 600;
  color: #606266;
}

.preview-text {
  color: #909399;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.plain-preview {
  margin-top: 10px;
  font-size: 12px;
  color: #606266;
  line-height: 1.7;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.card-foot {
  margin-top: 12px;
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: #c0c4cc;
}

.view-more {
  color: #409eff;
}

.pager {
  margin-top: 16px;
  display: flex;
  justify-content: center;
}

.drawer-title {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 15px;
  font-weight: 600;
}

.detail-meta {
  display: flex;
  gap: 14px;
  flex-wrap: wrap;
  font-size: 12px;
  color: #909399;
  margin-bottom: 16px;
}

.section-blocks {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.section-block {
  border-left: 4px solid #409eff;
  background: #fafafa;
  border-radius: 6px;
  padding: 12px 14px;
}

.block-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.block-index {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  color: #fff;
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.block-title {
  font-weight: 600;
  font-size: 14px;
  color: #303133;
}

.block-body {
  font-size: 13px;
  line-height: 1.8;
  color: #303133;
  white-space: pre-wrap;
  word-break: break-word;
}

.raw-body {
  margin: 0;
  font-size: 12px;
  line-height: 1.8;
  color: #606266;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 320px;
  overflow-y: auto;
  background: #fafafa;
  padding: 12px;
  border-radius: 6px;
}
</style>
