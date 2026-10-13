<template>
  <div class="article-page">
    <!-- 顶部模式切换 + 最终计分榜（随时可查看：本场榜 / 历史总榜） -->
    <div class="top-bar">
      <el-tabs v-model="activeMode" class="mode-tabs" @tab-change="handleModeChange">
        <el-tab-pane label="新建文章" name="create" />
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
        <!-- 新建模式：表单 + 进度 -->
        <template v-if="articleStore.viewMode === 'create'">
          <CreateForm @submit="handleCreate" />
          <StageProgress
            :stages="articleStore.stages"
            :status="articleStore.status"
            class="mt-20"
          />
        </template>

        <!-- 历史模式：列表 -->
        <ArticleList
          v-else
          @select="handleSelectArticle"
        />
      </div>

      <!-- 右侧结果面板 -->
      <div class="right-panel">
        <template v-if="showEmpty">
          <el-card shadow="never" class="empty-card">
            <el-empty :description="emptyText" />
          </el-card>
        </template>

        <template v-else>
          <OutlineDisplay
            :outline="articleStore.outline"
            :loading="isLoadingOutline"
            class="mb-20"
          />

          <ArticleDisplay
            :content="articleStore.finalArticle"
            :word-count="articleStore.stages.revision.wordCount"
            :loading="isLoadingArticle"
            class="mb-20"
          />

          <RevisionNotes :notes="articleStore.revisionNotes" />

          <!-- 最终质量检测员：流水线跑完后给三个环节 AI 的质检榜 -->
          <QualityScoreCard
            :scores="articleStore.qualityScores"
            :overall="articleStore.qualityOverall"
            :summary="articleStore.qualitySummary"
            class="mt-20"
            @view-history="showScoreBoard = true"
          />

          <el-alert
            v-if="articleStore.status === 'failed'"
            :title="articleStore.errorMessage || '生成失败'"
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
      feature="article"
      :task-id="articleStore.articleId"
      :live-scores="articleStore.qualityScores"
      :live-overall="articleStore.qualityOverall"
      title="文章 · 质量检测榜"
    />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { useArticleStore } from '@/stores/articleStore'
import CreateForm from '@/components/article/CreateForm.vue'
import StageProgress from '@/components/article/StageProgress.vue'
import OutlineDisplay from '@/components/article/OutlineDisplay.vue'
import ArticleDisplay from '@/components/article/ArticleDisplay.vue'
import RevisionNotes from '@/components/article/RevisionNotes.vue'
import ArticleList from '@/components/article/ArticleList.vue'
import QualityScoreCard from '@/components/common/QualityScoreCard.vue'
import ScoreBoardDialog from '@/components/common/ScoreBoardDialog.vue'

const articleStore = useArticleStore()
const showScoreBoard = ref(false)

const activeMode = computed(() => articleStore.viewMode)

const showEmpty = computed(() => {
  if (articleStore.viewMode === 'create') {
    return articleStore.status === 'idle'
  } else {
    return !articleStore.selectedId
  }
})

const emptyText = computed(() => {
  if (articleStore.viewMode === 'create') {
    return '输入创作需求，开始生成文章'
  } else {
    return '点击左侧记录查看详情'
  }
})

const isLoadingOutline = computed(() => {
  return articleStore.status === 'running' && articleStore.currentStage === 'outline'
})

const isLoadingArticle = computed(() => {
  return articleStore.status === 'running' && 
    (articleStore.currentStage === 'draft' || articleStore.currentStage === 'revision')
})

const handleModeChange = (mode) => {
  articleStore.setViewMode(mode)
}

const handleCreate = (formData) => {
  articleStore.submitCreate(formData)
}

const handleSelectArticle = (id) => {
  articleStore.loadDetail(id)
}

onBeforeUnmount(() => {
  articleStore.closeConnection()
})
</script>

<style scoped>
.article-page {
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
  overflow: hidden;
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
.mt-20 { margin-top: 20px; }
.mb-20 { margin-bottom: 20px; }
</style>
