<template>
  <el-card shadow="never" v-if="notes">
    <template #header>
      <span class="card-title">修改说明</span>
    </template>
    <div class="notes-content">
      <p v-for="(item, index) in noteList" :key="index">{{ item }}</p>
    </div>
  </el-card>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  notes: {
    type: String,
    default: ''
  }
})

const noteList = computed(() => {
  if (!props.notes) return []
  return props.notes
    .split('\n')
    .map(line => line.trim())
    .filter(line => {
      if (!line) return false
      // 修正2：移除前导 Markdown 符号（*、#、空格）后再匹配数字序号
      const cleanLine = line.replace(/^[\s*#]+/, '')
      return /^\d+[.、]/.test(cleanLine)
    })
})
</script>

<style scoped>
.card-title {
  font-weight: 600;
  font-size: 16px;
}
.notes-content {
  color: #606266;
  line-height: 1.8;
}
.notes-content p {
  margin: 4px 0;
  padding-left: 8px;
  border-left: 3px solid #409eff;
}
</style>
