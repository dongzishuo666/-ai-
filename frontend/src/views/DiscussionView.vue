<template>
  <div class="discussion-page">
    <!-- 顶部模式切换 -->
    <el-tabs v-model="activeMode" class="mode-tabs" @tab-change="handleModeChange">
      <el-tab-pane label="新建讨论" name="create" />
      <el-tab-pane label="历史记录" name="history" />
    </el-tabs>

    <div class="page-content">
      <!-- 左侧面板 -->
      <div class="left-panel">
        <template v-if="store.viewMode === 'create'">
          <CreateForm v-if="store.status === 'idle'" />

          <template v-else>
            <div class="action-bar">
              <el-button
                type="primary"
                size="small"
                :disabled="isBusy"
                @click="handleNewDiscussion"
              >
                新建讨论
              </el-button>
              <span v-if="isBusy" class="action-tip">请先暂停或终止当前讨论</span>
            </div>
            <ParticipantList :participants="store.participants" class="mb-16" />
            <JudgeInfo :judges="store.judges" class="mb-16" />
            <ControlBar />
          </template>
        </template>

        <DiscussionList v-else @select="handleSelectDiscussion" />
      </div>

      <!-- 右侧主区 -->
      <div class="right-panel">
        <!-- 秩序裁判评分进度：该环节串行耗时可达分钟级，无提示会被误认为卡死 -->
        <div v-if="store.judgeScoring" class="scoring-hint">
          <el-icon class="is-loading"><Loading /></el-icon>
          <span>
            第 {{ store.judgeScoring.round }} / {{ store.totalRounds }} 轮 ·
            {{ store.judgeScoring.message }}
          </span>
        </div>

        <!-- 总结并记忆入口：常驻在聊天区上方，避免藏在底部 Tab 里找不到 -->
        <div class="summary-bar">
          <el-button
            v-if="!isDistilling"
            type="primary"
            size="small"
            :disabled="!store.discussionId"
            @click="handleDistill"
          >
            总结并记忆
          </el-button>
          <template v-else>
            <el-button type="warning" size="small" :loading="true" @click="handleCancelDistill">
              取消总结
            </el-button>
          </template>

          <el-button
            size="small"
            :disabled="!store.discussionId || isDistilling"
            @click="handleJudgeSummary"
          >
            仅裁判总结
          </el-button>

          <el-button size="small" @click="goMemoryPage">打开记忆库</el-button>

          <el-button
            size="small"
            type="success"
            @click="showScoreBoard = true"
          >
            最终积分榜
          </el-button>

          <span v-if="distillTip" class="summary-tip">{{ distillTip }}</span>
          <span v-else-if="!store.discussionId" class="summary-tip muted">
            请先创建或从历史记录中选择一场讨论
          </span>
        </div>

        <MessageFlow
          :messages="store.messages"
          :topic="store.topic"
          class="message-flow-wrap"
        />

        <!-- 底部 Tab 面板 -->
        <el-tabs v-model="bottomTab" class="bottom-tabs">
          <el-tab-pane label="计分榜" name="scoreboard">
            <ScoreboardPanel
              :leaderboard="store.leaderboard"
              :round-scores="store.roundScores"
              :max-round="store.currentRound"
            />
          </el-tab-pane>
          <el-tab-pane
            v-if="store.midtermSummary"
            label="局中总结"
            name="midterm"
          >
            <div class="midterm-panel">
              <div class="midterm-meta">
                第 {{ store.midtermSummary.round }} 轮后由评价裁判（{{
                  store.midtermSummary.judge
                }}）产出，收敛期据此讨论
              </div>
              <div class="midterm-sec">
                <div class="sec-title">已形成的共同点</div>
                <ul>
                  <li v-for="(p, i) in store.midtermSummary.agreed_points" :key="i">
                    {{ p }}
                  </li>
                  <li v-if="!store.midtermSummary.agreed_points?.length" class="muted">
                    （暂无明确共同点）
                  </li>
                </ul>
              </div>
              <div class="midterm-sec">
                <div class="sec-title">待收敛的核心分歧</div>
                <ul>
                  <li v-for="(d, i) in store.midtermSummary.core_disagreements" :key="i">
                    <b>{{ d.point }}</b>
                    <div class="pos">{{ d.positions }}</div>
                  </li>
                  <li v-if="!store.midtermSummary.core_disagreements?.length" class="muted">
                    （裁判判定已无实质分歧）
                  </li>
                </ul>
              </div>
              <div class="midterm-sec">
                <div class="sec-title">建议的收敛方向</div>
                <ul>
                  <li v-for="(p, i) in store.midtermSummary.convergence_directions" :key="i">
                    {{ p }}
                  </li>
                </ul>
              </div>
              <div class="midterm-sec">
                <div class="sec-title">给参与者的收敛指令</div>
                <div class="guidance">{{ store.midtermSummary.guidance }}</div>
              </div>
            </div>
          </el-tab-pane>
          <el-tab-pane
            v-if="store.consensusChecks.length || store.consensusReached"
            label="共识判定"
            name="consensus"
          >
            <ConsensusPanel />
          </el-tab-pane>
          <el-tab-pane label="终局评估" name="evaluation">
            <JudgeEvaluationPanel :evaluation="store.evaluation" />
          </el-tab-pane>
          <el-tab-pane label="弹劾 / 总结记忆" name="extension">
            <ExtensionPanel />
          </el-tab-pane>
        </el-tabs>
      </div>
    </div>

    <el-alert
      v-if="store.errorMessage && store.status === 'failed'"
      :title="store.errorMessage"
      type="error"
      show-icon
      class="error-alert"
    >
      <template #default>
        <div class="retry-bar">
          <el-button size="small" type="primary" @click="store.newDiscussion()">
            重新创建
          </el-button>
        </div>
      </template>
    </el-alert>

    <!-- 终止原因：区分「暂停超时自动终止」与「手动终止」 -->
    <el-alert
      v-if="store.status === 'terminated' && store.endReason"
      :title="store.endReasonText"
      type="warning"
      show-icon
      class="error-alert"
    />

    <!-- 最终积分榜：本场终榜 + 历史总榜（平时分），可随时查看 -->
    <ScoreBoardDialog
      v-model:visible="showScoreBoard"
      feature="discussion"
      :task-id="store.discussionId"
      :live-scores="store.finalScores"
      title="讨论 · 最终积分榜"
    />
  </div>
</template>

<script setup>
import { ref, computed, watch, onBeforeUnmount, onActivated } from 'vue'
import { ElMessageBox } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { useDiscussionStore } from '@/stores/discussionStore'

import CreateForm from '@/components/discussion/CreateForm.vue'
import ParticipantList from '@/components/discussion/ParticipantList.vue'
import JudgeInfo from '@/components/discussion/JudgeInfo.vue'
import ControlBar from '@/components/discussion/ControlBar.vue'
import MessageFlow from '@/components/discussion/MessageFlow.vue'
import ScoreboardPanel from '@/components/discussion/ScoreboardPanel.vue'
import ConsensusPanel from '@/components/discussion/ConsensusPanel.vue'
import JudgeEvaluationPanel from '@/components/discussion/JudgeEvaluationPanel.vue'
import ExtensionPanel from '@/components/discussion/ExtensionPanel.vue'
import DiscussionList from '@/components/discussion/DiscussionList.vue'
import ScoreBoardDialog from '@/components/common/ScoreBoardDialog.vue'

const store = useDiscussionStore()
const bottomTab = ref('scoreboard')
const showScoreBoard = ref(false)

const activeMode = computed({
  get: () => store.viewMode,
  set: (val) => store.setViewMode(val)
})

const handleModeChange = (mode) => {
  store.setViewMode(mode)
}

const handleSelectDiscussion = (id) => {
  store.loadDetail(id)
}

// ---------------- 总结并记忆 ----------------
const router = useRouter()

const DISTILL_TIPS = {
  individual: '正在生成讨论者总结...',
  judging: '正在生成裁判总结，完成后自动存入记忆库',
  cancelled: '已取消',
  skipped: '已跳过（详见聊天区说明）',
  done: '已完成并存入记忆库',
}
const isDistilling = computed(() =>
  ['individual', 'judging'].includes(store.distillPhase)
)
const distillTip = computed(() => DISTILL_TIPS[store.distillPhase] || '')

function handleDistill() {
  store.startDistill()
  bottomTab.value = 'extension'   // 顺带切到面板，方便看进度与取消
}
function handleJudgeSummary() {
  store.startJudgeSummary()
  bottomTab.value = 'extension'
}
async function handleCancelDistill() {
  await store.cancelDistill()
}
function goMemoryPage() {
  router.push('/memory')
}

// 无论从哪里触发，总结进行中都把底部面板切到「弹劾 / 总结记忆」
watch(
  () => store.distillPhase,
  (v) => { if (v && v !== 'idle') bottomTab.value = 'extension' }
)

// 讨论进行中不允许丢弃当前会话
const isBusy = computed(() =>
  ['created', 'running', 'paused'].includes(store.status)
)

const handleNewDiscussion = async () => {
  try {
    await ElMessageBox.confirm(
      '将清空当前讨论内容并返回创建页，确定继续？',
      '新建讨论',
      { type: 'warning', confirmButtonText: '确定', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  store.newDiscussion()
}

// 页面被 keep-alive 缓存：切走不会 unmount，切回也不会重新挂载，
// 因此重连只能挂在 onActivated 上，否则会停在最后一次收到的状态。
onActivated(() => {
  store.ensureConnection()
})

onBeforeUnmount(() => {
  store.closeConnection()
})
</script>

<style scoped>
.discussion-page {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.mode-tabs {
  margin-bottom: 12px;
}
.page-content {
  display: flex;
  gap: 16px;
  flex: 1;
  overflow: hidden;
}
.left-panel {
  width: 340px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}
.right-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  gap: 12px;
}
.message-flow-wrap {
  flex: 1;
  min-height: 0;
}
.summary-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  padding: 8px 12px;
}
.summary-tip {
  font-size: 12px;
  color: #409eff;
  margin-left: auto;
}
.summary-tip.muted {
  color: #909399;
}
.bottom-tabs {
  height: 280px;
  flex-shrink: 0;
}
.bottom-tabs :deep(.el-tabs__content) {
  height: calc(100% - 38px);
  overflow-y: auto;
}
.mb-16 { margin-bottom: 12px; }
.action-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.action-tip {
  font-size: 12px;
  color: var(--el-color-warning);
}
.error-alert {
  margin-top: 12px;
}
.retry-bar {
  margin-top: 8px;
}
.scoring-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  margin-bottom: 8px;
  font-size: 13px;
  color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-7);
  border-radius: 6px;
}
</style>
