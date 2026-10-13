import { createRouter, createWebHistory } from 'vue-router'
import { useDocStore } from '@/stores/docStore'
import { useDiscussionStore } from '@/stores/discussionStore'
import { useArticleStore } from '@/stores/articleStore'

const routes = [
  { path: '/', redirect: '/discussion' },
  {
    path: '/docs',
    name: 'DocPrompt',
    component: () => import('@/views/DocPromptView.vue')
  },
  {
    path: '/discussion',
    name: 'Discussion',
    component: () => import('@/views/DiscussionView.vue')
  },
  {
    path: '/chat',
    name: 'Chat',
    component: () => import('@/views/ChatView.vue')
  },
  {
    path: '/articles',
    name: 'Article',
    component: () => import('@/views/ArticleView.vue')
  },
  {
    path: '/memory',
    name: 'Memory',
    component: () => import('@/views/MemoryView.vue')
  },
  {
    path: '/tokens',
    name: 'TokenStats',
    component: () => import('@/views/TokenStatsView.vue')
  },
  {
    path: '/system',
    name: 'System',
    component: () => import('@/views/SystemView.vue')
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// 页面切换时清理对应模块连接
router.afterEach((to) => {
  if (to.name !== 'DocPrompt') {
    useDocStore().closeConnection()
  }
  if (to.name !== 'Discussion') {
    useDiscussionStore().closeConnection()
  }
  if (to.name !== 'Article') {
    useArticleStore().closeConnection()
  }
})

export default router
