<template>
  <div class="eval-panel">
    <div class="panel-header">
      <span class="panel-title">终局评价</span>
      <el-tag v-if="evaluation" :type="consensusType">
        {{ consensusText }}
      </el-tag>
    </div>

    <template v-if="evaluation">
      <div class="eval-section">
        <div class="section-label">整体效率评分</div>
        <div class="efficiency-score">{{ evaluation.efficiency_score }} / 10</div>
      </div>

      <div class="eval-section">
        <div class="section-label">贡献度排名</div>
        <div class="rank-list">
          <div v-for="(item, index) in evaluation.contribution_ranking" :key="index" class="rank-item">
            <span class="rank-num">{{ index + 1 }}</span>
            <span class="rank-name">{{ item.display_name || item.ai_id }}</span>
            <span class="rank-desc">{{ item.contribution }}</span>
          </div>
        </div>
      </div>

      <div class="eval-section">
        <div class="section-label">未解决问题</div>
        <ul class="issue-list">
          <li v-for="(item, index) in evaluation.unresolved_issues" :key="index">{{ item }}</li>
        </ul>
      </div>

      <div class="eval-section">
        <div class="section-label">改进建议</div>
        <ul class="suggestion-list">
          <li v-for="(item, index) in evaluation.suggestions" :key="index">{{ item }}</li>
        </ul>
      </div>

      <div class="eval-section">
        <div class="section-label">综合总结</div>
        <p class="summary-text">{{ evaluation.summary }}</p>
      </div>

      <div class="eval-section">
        <div class="section-label">行动方案</div>
        <div class="action-plan markdown-content" v-html="renderedPlan"></div>
      </div>
    </template>

    <el-empty v-else description="讨论结束后生成" :image-size="80" />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { renderMarkdown } from '@/utils/markdown'

const props = defineProps({
  evaluation: {
    type: Object,
    default: null
  }
})

const consensusText = computed(() => {
  const map = {
    full: '完全共识',
    partial: '部分共识',
    none: '无共识'
  }
  return map[props.evaluation?.consensus_level] || '-'
})

const consensusType = computed(() => {
  const map = {
    full: 'success',
    partial: 'warning',
    none: 'danger'
  }
  return map[props.evaluation?.consensus_level] || 'info'
})

const renderedPlan = computed(() => {
  return renderMarkdown(props.evaluation?.action_plan || '')
})
</script>

<style scoped>
.eval-panel {
  height: 100%;
  overflow-y: auto;
  padding-right: 8px;
}
.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.panel-title {
  font-weight: 600;
  font-size: 15px;
  color: #303133;
}
.eval-section {
  margin-bottom: 18px;
}
.section-label {
  font-size: 13px;
  font-weight: 600;
  color: #606266;
  margin-bottom: 8px;
  padding-left: 8px;
  border-left: 3px solid #409eff;
}
.efficiency-score {
  font-size: 28px;
  font-weight: 700;
  color: #67c23a;
  text-align: center;
  padding: 10px;
  background: #f0f9eb;
  border-radius: 8px;
}
.rank-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.rank-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 10px;
  background: #f8f9fc;
  border-radius: 6px;
}
.rank-num {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #409eff;
  color: #fff;
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.rank-name {
  font-weight: 500;
  font-size: 13px;
  width: 100px;
  flex-shrink: 0;
}
.rank-desc {
  flex: 1;
  font-size: 12px;
  color: #606266;
  min-width: 0;
}
.issue-list li,
.suggestion-list li {
  margin: 4px 0;
  font-size: 13px;
  color: #303133;
}
.summary-text {
  line-height: 1.8;
  font-size: 13px;
  color: #303133;
  margin: 0;
  text-indent: 2em;
}
.action-plan {
  padding: 12px;
  background: #f8f9fc;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.7;
}
.action-plan :deep(table) {
  width: 100%;
  border-collapse: collapse;
}
.action-plan :deep(th),
.action-plan :deep(td) {
  border: 1px solid #eaeef5;
  padding: 6px 10px;
  text-align: left;
}
.action-plan :deep(th) {
  background: #ecf5ff;
  font-weight: 600;
}
</style>
