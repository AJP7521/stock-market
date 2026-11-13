<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import AskChartPanel from './AskChartPanel.vue'
import AutomationPanel from './AutomationPanel.vue'
import CandleChart from './CandleChart.vue'
import IndicatorPanel from './IndicatorPanel.vue'
import PaperPanel from './PaperPanel.vue'

const TIMEFRAMES = ['1m', '5m', '15m', '1h', '1d']

const health = ref(null)
const error = ref('')

const mode = ref('live')            // 'live' or 'replay'
// The chart's symbol and interval live in the URL (?symbol=…&interval=…) so a refresh reopens it.
const urlParams = new URLSearchParams(location.search)
const startSymbol = (urlParams.get('symbol') || 'AAPL').toUpperCase()
const symbolInput = ref(startSymbol)
const symbol = ref(startSymbol)
const timeframe = ref(['1m', '5m', '15m', '1h', '1d'].includes(urlParams.get('interval')) ? urlParams.get('interval') : '1m')
const candles = ref([])
const source = ref('')
const historyError = ref('')
const latestStatus = ref(null)      // status of the most recent candle (complete / forming)
const feed = ref(null)              // upstream feed status from FastAPI
const socketState = ref('closed')   // browser <-> FastAPI WebSocket
const nowMs = ref(Date.now())
const chartRef = ref(null)
const chartKey = ref(0)

// Simple moving averages (calculated in Python; drawn here)
const smaOn = ref(true)
const smaShort = ref(5)
const smaLong = ref(20)
const sma = ref(null)               // response of /api/indicators/sma, or the current replay step's sma
const smaError = ref('')
let smaTimer = null

// Configured indicators (Prompt 9B): values calculated in Python for the chart's candles
const indicators = ref([])

// Replay state
const sessions = ref([])
const replaySymbol = ref('')
const replayStart = ref('')
const replayEnd = ref('')
const replaySpeed = ref(10)         // steps per second
const replay = ref(null)            // response of GET /api/replay
const replayIndex = ref(0)          // number of steps applied
const replayMessage = ref('')
const replayRunning = ref(false)

let socket = null
let clock = null
let replayTimer = null
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

// ---------------------------------------------------------------- live mode
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
  chartKey.value++
  await Promise.all([fetchSma(gen), fetchIndicators(gen)])
}

async function fetchIndicators(gen = generation) {
  const q = new URLSearchParams({ symbol: symbol.value, timeframe: timeframe.value })
  const res = await fetch(`/api/indicator-values?${q}`)
  if (!res.ok || gen !== generation) return
  indicators.value = (await res.json()).indicators
}

function indicatorsChanged() {
  if (mode.value === 'live') fetchIndicators()
  else if (replay.value) startReplay()
}

function smaParams() {
  return smaOn.value ? { short: String(smaShort.value), long: String(smaLong.value) } : null
}

function smaProblem(body) {
  return typeof body.detail === 'string' ? body.detail
    : (body.detail ?? []).map((d) => `${d.loc?.at(-1)}: ${d.msg}`).join('; ')
}

async function fetchSma(gen = generation) {
  const params = smaParams()
  if (!params) { sma.value = null; smaError.value = ''; return }
  const q = new URLSearchParams({ symbol: symbol.value, timeframe: timeframe.value, ...params })
  const res = await fetch(`/api/indicators/sma?${q}`)
  const body = await res.json()
  if (gen !== generation) return
  if (!res.ok) {
    smaError.value = `SMA settings rejected: ${smaProblem(body)}`   // keep the last valid lines
    return
  }
  smaError.value = ''
  sma.value = body
}

// Live candles arrive about once a second; recalculate the SMAs at most once a second.
function scheduleSma() {
  if (smaTimer) return
  smaTimer = setTimeout(() => { smaTimer = null; fetchSma(); fetchIndicators() }, 1000)
}

function applySmaSettings() {
  if (mode.value === 'live') fetchSma()
  else if (replay.value || replayMessage.value) startReplay()
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
      applyCandle(msg.candle, msg.status)
      scheduleSma()
    }
  }
}

function closeSocket() {
  if (socket) {
    socket.onclose = socket.onmessage = socket.onerror = null
    socket.close()
    socket = null
  }
  socketState.value = 'closed'
}

async function selectLive() {
  stopReplay()
  const gen = ++generation
  mode.value = 'live'
  symbol.value = symbolInput.value.trim().toUpperCase() || 'SAMPLE'
  history.replaceState(null, '', `?${new URLSearchParams({ symbol: symbol.value, interval: timeframe.value })}`)
  feed.value = null
  latestStatus.value = null
  try {
    await loadHistory(gen)
  } catch (e) {
    historyError.value = `Could not load candles: ${e.message}`
  }
  if (gen === generation) openSocket(gen)
}

// Update one candle on the chart and in the history array (no full redraw).
function applyCandle(row, status) {
  chartRef.value?.upsert(row)
  const rows = candles.value
  const i = rows.findIndex((r) => r.timestamp === row.timestamp)
  if (i >= 0) rows[i] = row
  else rows.push(row)
  if (rows.at(-1).timestamp === row.timestamp) latestStatus.value = status
}

// ---------------------------------------------------------------- replay mode
const floorMinute = (iso) => iso.slice(0, 16) + ':00Z'
const nextMinute = (iso) => new Date(Date.parse(floorMinute(iso)) + 60000).toISOString().slice(0, 19) + 'Z'

async function enterReplay() {
  ++generation
  closeSocket()
  mode.value = 'replay'
  feed.value = null
  source.value = ''
  latestStatus.value = null
  replay.value = null
  replayMessage.value = ''
  candles.value = []
  chartKey.value++
  const res = await fetch('/api/replay/sessions')
  sessions.value = (await res.json()).sessions
  if (sessions.value.length && !sessions.value.some((s) => s.symbol === replaySymbol.value)) {
    pickSession(sessions.value[0].symbol)
  }
}

function pickSession(sym) {
  const s = sessions.value.find((x) => x.symbol === sym)
  replaySymbol.value = sym
  if (s) {
    replayStart.value = floorMinute(s.first)
    replayEnd.value = nextMinute(s.last)
  }
}

async function startReplay() {
  stopReplay()
  replayMessage.value = ''
  replay.value = null
  replayIndex.value = 0
  candles.value = []
  latestStatus.value = null
  sma.value = null
  indicators.value = []
  chartKey.value++
  const q = new URLSearchParams({
    symbol: replaySymbol.value.trim().toUpperCase(), start: replayStart.value, end: replayEnd.value,
    timeframe: timeframe.value, ...(smaParams() ?? {}),
  })
  const res = await fetch(`/api/replay?${q}`)
  const body = await res.json()
  if (!res.ok) {
    replayMessage.value = `Replay request failed: ${smaProblem(body)}`
    return
  }
  symbol.value = body.symbol
  source.value = 'Replay of recorded Yahoo observations (SQLite, read-only)'
  if (body.empty) {
    replayMessage.value = `${body.symbol} has no recorded observations between ${body.start} and ${body.end}.`
    return
  }
  replay.value = body
  replayRunning.value = true
  replayTimer = setInterval(stepReplay, 1000 / Math.max(1, Number(replaySpeed.value) || 10))
}

function stepReplay() {
  const r = replay.value
  if (!r || replayIndex.value >= r.steps.length) return stopReplay()
  const step = r.steps[replayIndex.value]
  if (step.candle) applyCandle(step.candle, step.status)
  if (step.sma) sma.value = step.sma        // only candles available at this step
  if (step.indicators) indicators.value = step.indicators
  replayIndex.value++
  if (replayIndex.value >= r.steps.length) stopReplay()
}

function finishReplay() {
  if (!replay.value) return
  clearInterval(replayTimer)
  while (replayIndex.value < replay.value.steps.length) stepReplay()
  stopReplay()
}

function stopReplay() {
  clearInterval(replayTimer)
  replayTimer = null
  replayRunning.value = false
}

function changeTimeframe() {
  if (mode.value === 'live') selectLive()
  else if (replay.value || replayMessage.value) startReplay()
}

// The last candles with each configured indicator's value at that candle (newest first).
const valueRows = computed(() => {
  const byTs = indicators.value.map((ind) => new Map(ind.points.map((p) => [p.timestamp, p.value])))
  return candles.value.slice(-12).reverse().map((c) => ({ ...c, values: byTs.map((m) => m.get(c.timestamp) ?? null) }))
})
const fmt = (v) => (v === null || v === undefined ? '—' : Number(v.toFixed(4)).toString())

// The latest candle, if it is still forming (not a valid cutoff for Ask about chart).
const formingTimestamp = computed(() =>
  latestStatus.value && !latestStatus.value.complete ? candles.value.at(-1)?.timestamp ?? null : null)

const currentStep = computed(() =>
  replay.value && replayIndex.value > 0 ? replay.value.steps[replayIndex.value - 1] : null)

// ---------------------------------------------------------------- display
const ageSeconds = computed(() => {
  const t = feed.value?.last_source_time_ms
  return t ? Math.max(0, Math.round((nowMs.value - t) / 1000)) : null
})

const feedClass = computed(() => ({
  live: 'ok', quiet: 'warn', connecting: 'warn', offline: 'muted',
}[feed.value?.status] ?? 'bad'))

const chartLabel = computed(() => {
  if (mode.value === 'replay') return `Replay · ${symbol.value || replaySymbol.value} · ${timeframe.value}`
  return symbol.value === 'SAMPLE' ? 'Synthetic one-minute sample (prompts/data/sample-candles.csv)'
                                   : `${symbol.value} · ${timeframe.value}`
})

onMounted(() => {
  checkHealth()
  selectLive()
  clock = setInterval(() => { nowMs.value = Date.now() }, 1000)
})

onBeforeUnmount(() => {
  clearInterval(clock)
  clearTimeout(smaTimer)
  stopReplay()
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
      <small v-if="health && health.live_feed === false" data-testid="live-feed-off"> · live feed off</small>
      <small v-if="health && health.test_feed" class="warn" data-testid="test-feed-on"> · TEST price feed on</small>
      <button type="button" @click="checkHealth">Check again</button>
    </p>

    <div class="modes">
      <button type="button" :class="{ active: mode === 'live' }" data-testid="mode-live" @click="selectLive">Live</button>
      <button type="button" :class="{ active: mode === 'replay' }" data-testid="mode-replay" @click="enterReplay">Replay</button>
      <label>
        Interval
        <select v-model="timeframe" data-testid="timeframe-select" @change="changeTimeframe">
          <option v-for="tf in TIMEFRAMES" :key="tf" :value="tf">{{ tf }}</option>
        </select>
      </label>
    </div>

    <form class="controls" data-testid="sma-form" @submit.prevent="applySmaSettings">
      <label><input v-model="smaOn" type="checkbox" data-testid="sma-on" @change="applySmaSettings" /> SMAs</label>
      <label>Short <input v-model.number="smaShort" type="number" step="1" data-testid="sma-short" style="width:4em" /></label>
      <label>Long <input v-model.number="smaLong" type="number" step="1" data-testid="sma-long" style="width:4em" /></label>
      <button type="submit" data-testid="sma-apply">Apply</button>
      <span class="hint">Whole numbers, short &lt; long; averages of completed closes</span>
      <span v-if="smaError" class="bad" data-testid="sma-error">{{ smaError }}</span>
      <span v-if="sma && !smaError" class="hint" data-testid="sma-crossovers">
        Crossovers: {{ sma.crossovers.length ? sma.crossovers.map((c) => `${c.signal} ${c.timestamp.slice(11, 16)}Z`).join(', ') : 'none' }}
      </span>
    </form>

    <form v-if="mode === 'live'" class="controls" @submit.prevent="selectLive">
      <label>Symbol <input v-model="symbolInput" data-testid="symbol-input" size="8" /></label>
      <button type="submit">Load</button>
      <span class="hint">SAMPLE = synthetic CSV; any other symbol streams from Yahoo Finance</span>
    </form>

    <form v-else class="controls" data-testid="replay-form" @submit.prevent="startReplay">
      <label>
        Recorded symbol
        <input v-model="replaySymbol" list="recorded-symbols" data-testid="replay-symbol" size="8"
               @change="pickSession(replaySymbol.trim().toUpperCase())" />
        <datalist id="recorded-symbols">
          <option v-for="s in sessions" :key="s.symbol" :value="s.symbol">
            {{ s.observations }} observations, {{ s.first }} → {{ s.last }}
          </option>
        </datalist>
      </label>
      <label>From (UTC) <input v-model="replayStart" data-testid="replay-start" size="21" /></label>
      <label>To (UTC) <input v-model="replayEnd" data-testid="replay-end" size="21" /></label>
      <label>Steps/s <input v-model.number="replaySpeed" type="number" min="1" max="200" data-testid="replay-speed" style="width:4em" /></label>
      <button type="submit" data-testid="replay-start-button">Start replay</button>
      <button type="button" :disabled="!replayRunning" @click="stopReplay">Pause</button>
      <button type="button" :disabled="!replay || replayIndex >= replay.steps.length" data-testid="replay-finish"
              @click="finishReplay">Skip to end</button>
      <span class="hint" v-if="sessions.length">
        Recorded: <span v-for="s in sessions" :key="s.symbol">{{ s.symbol }} ({{ s.observations }} obs, {{ s.first }} → {{ s.last }}) </span>
      </span>
      <span class="hint" v-else>No recorded sessions yet.</span>
    </form>

    <dl class="status" data-testid="live-status">
      <div><dt>Mode</dt><dd data-testid="status-mode">{{ mode === 'replay' ? 'Replay' : 'Live' }}</dd></div>
      <div><dt>Symbol</dt><dd data-testid="status-symbol">{{ symbol }}</dd></div>
      <div><dt>Interval</dt><dd data-testid="status-interval">{{ timeframe }}</dd></div>
      <div><dt>Source</dt><dd data-testid="status-source">{{ source || '—' }}</dd></div>
      <template v-if="mode === 'live'">
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
      </template>
      <template v-else>
        <div>
          <dt>Replaying observation</dt>
          <dd data-testid="replay-progress">
            <template v-if="replay">{{ replayIndex }} / {{ replay.steps.length }}</template><template v-else>—</template>
          </dd>
        </div>
        <div>
          <dt>Replay time (received)</dt>
          <dd data-testid="replay-time">{{ currentStep?.replay_time ?? '—' }}</dd>
        </div>
        <div>
          <dt>Source time</dt>
          <dd data-testid="replay-source-time">
            {{ currentStep?.observation.source_time ?? '—' }}
            <strong v-if="currentStep?.observation.late" class="warn"> late</strong>
            <strong v-if="currentStep?.observation.revises_id" class="warn"> revision of #{{ currentStep.observation.revises_id }}</strong>
            <small v-if="currentStep?.observation.is_repeat"> repeat</small>
          </dd>
        </div>
        <div>
          <dt>Price</dt>
          <dd data-testid="replay-price">{{ currentStep?.observation.price ?? '—' }}</dd>
        </div>
      </template>
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

    <p v-if="replayMessage" class="notice" data-testid="replay-message">{{ replayMessage }}</p>
    <p v-if="historyError" data-testid="history-error">{{ historyError }}</p>
    <p v-else-if="mode === 'live' && !candles.length && symbol !== 'SAMPLE'" data-testid="no-candles">
      No recorded candles for {{ symbol }} yet; waiting for live updates…
    </p>
    <PaperPanel v-if="mode === 'live'" :symbol="symbol" />

    <AutomationPanel v-if="mode === 'live' && symbol && symbol !== 'SAMPLE'" :symbol="symbol" :timeframe="timeframe"
                     :sma-short="smaShort" :sma-long="smaLong" />

    <IndicatorPanel :symbol="mode === 'replay' ? (replaySymbol.trim().toUpperCase() || symbol) : symbol"
                    @changed="indicatorsChanged" />

    <CandleChart ref="chartRef" :key="chartKey" :rows="candles" :label="chartLabel" :sma="sma"
                 :indicators="indicators" />

    <AskChartPanel v-if="candles.length && symbol !== 'SAMPLE'" :symbol="symbol" :timeframe="timeframe"
                   :candles="candles" :forming-timestamp="formingTimestamp" :sma-short="smaShort" :sma-long="smaLong"
                   :as-of="mode === 'replay' ? currentStep?.replay_time ?? null : null" />

    <table v-if="indicators.length && candles.length" class="values" data-testid="indicator-values">
      <caption>Indicator values alongside the candles (latest first)</caption>
      <thead>
        <tr>
          <th>Candle (UTC)</th><th>High</th><th>Low</th><th>Close</th>
          <th v-for="ind in indicators" :key="ind.id">{{ ind.name }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in valueRows" :key="row.timestamp" :data-testid="`values-${row.timestamp}`">
          <td>{{ row.timestamp.replace('T', ' ').replace(':00Z', 'Z') }}</td>
          <td>{{ row.high }}</td><td>{{ row.low }}</td><td>{{ row.close }}</td>
          <td v-for="(v, i) in row.values" :key="i">{{ fmt(v) }}</td>
        </tr>
      </tbody>
    </table>
  </main>
</template>

<style scoped>
main { font-family: system-ui, sans-serif; }
.modes, .controls { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; margin: 0.5rem 0; }
.modes button.active { font-weight: bold; outline: 2px solid #1b7f5a; }
.hint { font-size: 0.8rem; color: #666; }
.values { border-collapse: collapse; margin-top: 0.75rem; font-size: 0.85rem; font-variant-numeric: tabular-nums; }
.values caption { text-align: left; font-size: 0.8rem; color: #666; padding-bottom: 0.25rem; }
.values th, .values td { border: 1px solid #ddd; padding: 0.2rem 0.5rem; text-align: right; }
.notice { padding: 0.5rem 0.75rem; background: #fff4e5; border: 1px solid #f0c48a; max-width: 880px; }
.status { display: flex; flex-wrap: wrap; gap: 0.5rem 1.25rem; margin: 0.5rem 0 1rem; font-size: 0.9rem; }
.status dt { font-size: 0.7rem; color: #666; text-transform: uppercase; }
.status dd { margin: 0; font-variant-numeric: tabular-nums; }
.ok { color: #1b7f5a; }
.warn { color: #b26b00; }
.bad { color: #c62828; }
.muted { color: #666; }
</style>
