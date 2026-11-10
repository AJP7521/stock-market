<script setup>
import { onMounted, ref } from 'vue'

const health = ref(null)
const error = ref('')

async function checkHealth() {
  error.value = ''
  health.value = null
  try {
    const res = await fetch('/api/health')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    health.value = await res.json()
  } catch (e) {
    error.value = `Backend unreachable: ${e.message}`
  }
}

onMounted(checkHealth)
</script>

<template>
  <main>
    <h1>Live Market Workstation</h1>
    <p>
      Backend health:
      <strong v-if="health" data-testid="health-status">{{ health.status }}</strong>
      <strong v-else-if="error" data-testid="health-error">{{ error }}</strong>
      <span v-else>checking…</span>
    </p>
    <button type="button" @click="checkHealth">Check again</button>
  </main>
</template>
