<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getHealth, type Health } from '../api/health'

const loading = ref(false)
const health = ref<Health | null>(null)
const error = ref('')

async function checkConnection() {
  if (loading.value) return
  loading.value = true
  health.value = null
  error.value = ''
  try {
    health.value = await getHealth()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '无法连接后端'
  } finally {
    loading.value = false
  }
}

onMounted(checkConnection)
</script>

<template>
  <main>
    <p class="eyebrow">CODEPOET / CORE DEMO</p>
    <h1>代码诗</h1>
    <p class="intro">廉洁主题生成式可视化创作平台</p>
    <section aria-labelledby="status-heading">
      <h2 id="status-heading">开发环境连接检查</h2>
      <div aria-live="polite">
        <p v-if="loading">正在连接后端…</p>
        <p v-else-if="health" class="success">后端与数据库已连接 · API {{ health.apiVersion }}</p>
        <p v-else class="error">{{ error }}。请确认后端已启动，再重试。</p>
      </div>
      <button :disabled="loading" @click="checkConnection">{{ loading ? '检查中…' : '重新检查' }}</button>
    </section>
    <p class="note">当前已配置应用启动与连接检查。创作画布、登录及作品保存将在后续任务中实现。</p>
  </main>
</template>
