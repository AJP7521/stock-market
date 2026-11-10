<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import CandleChart from './CandleChart.vue'

const TIMEFRAMES = ['1m', '5m', '15m', '1h', '1d']

const health = ref(null)
const error = ref('')

const symbolInput = ref('AAPL')
const symbol = ref('AAPL')
const timeframe = ref('1m')
const candles = ref([])
const source = ref('')
const historyError = ref('')
const latestStatus = ref(null)      // status of the most recent candle (complete / forming)
const feed = ref(null)              // upstream feed status from FastAPI
const socketState = ref('closed')   // browser <-> FastAPI WebSocket
const nowMs = ref(Date.now())
const chartRef = ref(null)

let socket = null
let clock = null
let generation = 0                  // ignores messages from a superseded subscription

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

async function loadHistory(gen) {
  historyError.value = ''
  const q = new URLSearchParams({ symbol: symbol.value, timeframe: timeframe.value })
  const res = await fetch(`/api/history?${q}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const body = await res.json()
  if (gen !== generation) return
  candles.value = body.candles
  source.value = body.source
  latestStatus.value = body.status.at(-1) ?? null
}

function openSocket(gen) {
  closeSocket()
  const q = new URLSearchParams({ symbol: symbol.value, timeframe: timeframe.value })
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/api/ws/candles?${q}`)
  socket = ws
  socketState.value = 'connecting'
  ws.onopen = () => { if (gen === generation) socketState.value = 'open' }
  ws.onclose = () => { if (gen === generation) socketState.value = 'closed' }
  ws.onerror = () => { if (gen === generation) socketState.value = 'error' }
  ws.onmessage = (event) => {
    if (gen !== generation) return
    const msg = JSON.parse(event.data)
    if (msg.symbol !== symbol.value || msg.interval !== timeframe.value) return
    feed.value = msg.feed
    if (msg.type === 'candle') {
      chartRef.value?.upsert(msg.candle)
      upsertRow(msg.candle)
      if (!candles.value.length || msg.candle.timestamp >= candles.value.at(-1).timestamp) {
        latestStatus.value = msg.status
      }
    }
  }
}

// Keep the history array in step with live updates, without triggering a full redraw.
function upsertRow(row) {
  const rows = candles.value
  const i = rows.findIndex((r) => r.timestamp === row.timestamp)
  if (i >= 0) rows[i] = row
  else rows.push(row)
}

function closeSocket() {
  if (socket) {
    socket.onclose = socket.onmessage = socket.onerror = null
    socket.close()
    socket = null
  }
  socketState.value = 'closed'
}

async function select() {
  const gen = ++generation
  symbol.value = symbolInput.value.trim().toUpperCase() || 'SAMPLE'
  feed.value = null
  latestStatus.value = null
  try {
    await loadHistory(gen)
  } catch (e) {
    historyError.value = `Could not load candles: ${e.message}`
  }
  if (gen === generation) openSocket(gen)
}

const ageSeconds = computed(() => {
  const t = feed.value?.last_source_time_ms
  return t ? Math.max(0, Math.round((nowMs.value - t) / 1000)) : null
})

const feedClass = computed(() => ({
  live: 'ok', quiet: 'warn', connecting: 'warn', offline: 'muted',
}[feed.value?.status] ?? 'bad'))

const chartLabel = computed(() =>
  symbol.value === 'SAMPLE' ? 'Synthetic one-minute sample (prompts/data/sample-candles.csv)'
                            : `${symbol.value} · ${timeframe.value}`)

onMounted(() => {
  checkHealth()
  select()
  clock = setInterval(() => { nowMs.value = Date.now() }, 1000)
})

onBeforeUnmount(() => {
  clearInterval(clock)
  closeSocket()
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

    <form class="controls" @submit.prevent="select">
      <label>Symbol <input v-model="symbolInput" data-testid="symbol-input" size="8" /></label>
      <label>
        Interval
        <select v-model="timeframe" data-testid="timeframe-select" @change="select">
          <option v-for="tf in TIMEFRAMES" :key="tf" :value="tf">{{ tf }}</option>
        </select>
      </label>
      <button type="submit">Load</button>
      <span class="hint">SAMPLE = synthetic CSV; any other symbol streams from Yahoo Finance</span>
    </form>

    <dl class="status" data-testid="live-status">
      <div><dt>Symbol</dt><dd data-testid="status-symbol">{{ symbol }}</dd></div>
      <div><dt>Interval</dt><dd data-testid="status-interval">{{ timeframe }}</dd></div>
      <div><dt>Source</dt><dd data-testid="status-source">{{ source || '—' }}</dd></div>
      <div>
        <dt>Last source observation</dt>
        <dd data-testid="status-age">
          <template v-if="ageSeconds !== null">{{ ageSeconds }} s ago</template>
          <template v-else>none yet</template>
        </dd>
      </div>
      <div>
        <dt>Feed</dt>
        <dd :class="feedClass" data-testid="status-feed">
          {{ feed?.status ?? 'waiting' }}<small v-if="feed?.detail"> ({{ feed.detail }})</small>
        </dd>
      </div>
      <div><dt>Browser ↔ FastAPI</dt><dd data-testid="status-socket">{{ socketState }}</dd></div>
      <div>
        <dt>Latest candle</dt>
        <dd data-testid="status-candle">
          <template v-if="latestStatus">
            <span v-if="latestStatus.complete">complete</span>
            <strong v-else class="warn">forming</strong>
            <small v-if="latestStatus.coverage_complete === false">
              · {{ latestStatus.minutes_present }}/{{ latestStatus.minutes_expected }} min</small>
          </template>
          <template v-else>—</template>
        </dd>
      </div>
    </dl>

    <p v-if="historyError" data-testid="history-error">{{ historyError }}</p>
    <p v-else-if="!candles.length && symbol !== 'SAMPLE'" data-testid="no-candles">
      No recorded candles for {{ symbol }} yet; waiting for live updates…
    </p>
    <CandleChart ref="chartRef" :key="`${symbol}-${timeframe}`" :rows="candles" :label="chartLabel" />
  </main>
</template>

<style scoped>
main { font-family: system-ui, sans-serif; }
.controls { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; margin: 0.5rem 0; }
.hint { font-size: 0.8rem; color: #666; }
.status { display: flex; flex-wrap: wrap; gap: 0.5rem 1.25rem; margin: 0.5rem 0 1rem; font-size: 0.9rem; }
.status dt { font-size: 0.7rem; color: #666; text-transform: uppercase; }
.status dd { margin: 0; font-variant-numeric: tabular-nums; }
.ok { color: #1b7f5a; }
.warn { color: #b26b00; }
.bad { color: #c62828; }
.muted { color: #666; }
</style>
