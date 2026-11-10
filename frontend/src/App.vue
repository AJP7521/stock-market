<script setup>
import { onMounted, ref } from 'vue'
import CandleChart from './CandleChart.vue'

const health = ref(null)
const error = ref('')
const candles = ref([])
const historyError = ref('')

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

async function loadHistory() {
  historyError.value = ''
  try {
    const res = await fetch('/api/history')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    candles.value = await res.json()
  } catch (e) {
    historyError.value = `Could not load candles: ${e.message}`
  }
}

onMounted(() => {
  checkHealth()
  loadHistory()
})
</script>

<template>
  <main>
    <h1>Live Market Workstation</h1>
    <p>
      Backend health:
      <strong v-if="health" data-testid="health-status">{{ health.status }}</strong>
      <strong v-else-if="error" data-testid="health-error">{{ error }}</strong>
      <span v-else>checking…</span>
      <button type="button" @click="checkHealth">Check again</button>
    </p>

    <p v-if="historyError" data-testid="history-error">{{ historyError }}</p>
    <CandleChart
      v-else-if="candles.length"
      :rows="candles"
      label="Synthetic one-minute sample (prompts/data/sample-candles.csv)"
    />
    <p v-else>Loading candles…</p>
  </main>
</template>
