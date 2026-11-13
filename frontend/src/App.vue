<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import AskChartPanel from './AskChartPanel.vue'
import AutomationPanel from './AutomationPanel.vue'
import CandleChart from './CandleChart.vue'
import IndicatorPanel from './IndicatorPanel.vue'
import PaperPanel from './PaperPanel.vue'
import VoicePanel from './VoicePanel.vue'

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

// Quote header: the latest close and its change since the open of the first loaded candle.
const money = (v) => v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const quote = computed(() => {
  const rows = candles.value
  if (!rows.length) return null
  const first = rows[0]
  const last = rows.at(-1)
  const change = last.close - first.open
  const pct = first.open ? (change / first.open) * 100 : null
  const sign = change > 0 ? '+' : change < 0 ? '−' : ''
  return {
    price: money(last.close),
    change: `${sign}${money(Math.abs(change))}`,
    pct: pct === null ? '' : `(${sign}${Math.abs(pct).toFixed(2)}%)`,
    direction: change > 0 ? 'up' : change < 0 ? 'down' : 'flat',
    at: last.timestamp.replace('T', ' ').slice(0, 16),
    since: first.timestamp.replace('T', ' ').slice(0, 16),
  }
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
  <header class="topbar">
    <div class="topbar-inner">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">◆</span>
        <span>Market <b>Workstation</b></span>
      </div>

      <form v-if="mode === 'live'" class="search" @submit.prevent="selectLive">
        <input v-model="symbolInput" data-testid="symbol-input" aria-label="Symbol"
               placeholder="Symbol: AAPL, or SAMPLE for the synthetic CSV"
               title="SAMPLE = synthetic CSV; any other symbol streams from Yahoo Finance" />
        <button type="submit">Load</button>
      </form>
      <div v-else class="search search-off">Replay of recorded observations · read-only</div>

      <div class="segmented" role="group" aria-label="Mode">
        <button type="button" :class="{ active: mode === 'live' }" data-testid="mode-live" @click="selectLive">Live</button>
        <button type="button" :class="{ active: mode === 'replay' }" data-testid="mode-replay" @click="enterReplay">Replay</button>
      </div>

      <div class="health">
        <span class="dot" :class="health ? 'ok' : error ? 'bad' : 'muted'" aria-hidden="true"></span>
        Backend
        <strong v-if="health" data-testid="health-status">{{ health.status }}</strong>
        <strong v-else-if="error" class="bad" data-testid="health-error">{{ error }}</strong>
        <span v-else>checking…</span>
        <small v-if="health && health.live_feed === false" data-testid="live-feed-off"> · live feed off</small>
        <small v-if="health && health.test_feed" class="warn" data-testid="test-feed-on"> · TEST price feed on</small>
        <button type="button" class="link" @click="checkHealth">Check again</button>
      </div>
    </div>
  </header>

  <dl class="strip" data-testid="live-status">
    <div><dt>Mode</dt><dd data-testid="status-mode">{{ mode === 'replay' ? 'Replay' : 'Live' }}</dd></div>
    <div><dt>Symbol</dt><dd data-testid="status-symbol">{{ symbol }}</dd></div>
    <div><dt>Interval</dt><dd data-testid="status-interval">{{ timeframe }}</dd></div>
    <div class="wide"><dt>Source</dt><dd data-testid="status-source">{{ source || '—' }}</dd></div>
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

  <div class="layout">
    <main class="main-col">
      <section class="quote">
        <p class="quote-meta">
          {{ mode === 'replay' ? 'Replay' : 'Live' }} · {{ timeframe }} candles · USD ·
          <span class="sim-badge">Simulated paper trading</span>
        </p>
        <h1>{{ symbol || replaySymbol || '—' }}</h1>
        <div v-if="quote" class="price-row">
          <span class="price">{{ quote.price }}</span>
          <span class="change" :class="quote.direction">{{ quote.change }} {{ quote.pct }}</span>
        </div>
        <p v-if="quote" class="quote-time">
          Last close of the {{ quote.at }} UTC candle · change since the {{ quote.since }} UTC open
        </p>
        <p v-else class="quote-time">No candles loaded yet.</p>
      </section>

      <form v-if="mode === 'replay'" class="card replay-form" data-testid="replay-form" @submit.prevent="startReplay">
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
        <label>Steps/s <input v-model.number="replaySpeed" type="number" min="1" max="200" data-testid="replay-speed" style="width:4.5em" /></label>
        <button type="submit" data-testid="replay-start-button">Start replay</button>
        <button type="button" :disabled="!replayRunning" @click="stopReplay">Pause</button>
        <button type="button" :disabled="!replay || replayIndex >= replay.steps.length" data-testid="replay-finish"
                @click="finishReplay">Skip to end</button>
        <span class="hint full" v-if="sessions.length">
          Recorded: <span v-for="s in sessions" :key="s.symbol">{{ s.symbol }} ({{ s.observations }} obs, {{ s.first }} → {{ s.last }}) </span>
        </span>
        <span class="hint full" v-else>No recorded sessions yet.</span>
      </form>

      <p v-if="replayMessage" class="notice" data-testid="replay-message">{{ replayMessage }}</p>
      <p v-if="historyError" class="notice" data-testid="history-error">{{ historyError }}</p>
      <p v-else-if="mode === 'live' && !candles.length && symbol !== 'SAMPLE'" class="notice" data-testid="no-candles">
        No recorded candles for {{ symbol }} yet; waiting for live updates…
      </p>

      <section class="card chart-panel">
        <div class="chart-toolbar">
          <div class="tabs" role="tablist" aria-label="Interval" data-testid="timeframe-tabs">
            <button v-for="tf in TIMEFRAMES" :key="tf" type="button" role="tab" :aria-selected="timeframe === tf"
                    :class="{ active: timeframe === tf }" :data-testid="`timeframe-${tf}`"
                    @click="timeframe = tf; changeTimeframe()">{{ tf }}</button>
          </div>
          <form class="sma" data-testid="sma-form" @submit.prevent="applySmaSettings">
            <label class="check"><input v-model="smaOn" type="checkbox" data-testid="sma-on" @change="applySmaSettings" /> SMAs</label>
            <label>Short <input v-model.number="smaShort" type="number" step="1" data-testid="sma-short" style="width:4em" /></label>
            <label>Long <input v-model.number="smaLong" type="number" step="1" data-testid="sma-long" style="width:4em" /></label>
            <button type="submit" data-testid="sma-apply">Apply</button>
          </form>
        </div>
        <p class="hint sma-note">
          SMAs: whole numbers, short &lt; long; averages of completed closes.
          <span v-if="smaError" class="bad" data-testid="sma-error">{{ smaError }}</span>
          <span v-if="sma && !smaError" data-testid="sma-crossovers">
            Crossovers: {{ sma.crossovers.length ? sma.crossovers.map((c) => `${c.signal} ${c.timestamp.slice(11, 16)}Z`).join(', ') : 'none' }}
          </span>
        </p>
        <CandleChart ref="chartRef" :key="chartKey" :rows="candles" :label="chartLabel" :sma="sma"
                     :indicators="indicators" />
      </section>

      <IndicatorPanel :symbol="mode === 'replay' ? (replaySymbol.trim().toUpperCase() || symbol) : symbol"
                      @changed="indicatorsChanged" />

      <section v-if="indicators.length && candles.length" class="card">
        <table class="values" data-testid="indicator-values">
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
      </section>

      <AskChartPanel v-if="candles.length && symbol !== 'SAMPLE'" :symbol="symbol" :timeframe="timeframe"
                     :candles="candles" :forming-timestamp="formingTimestamp" :sma-short="smaShort" :sma-long="smaLong"
                     :as-of="mode === 'replay' ? currentStep?.replay_time ?? null : null" />

      <AutomationPanel v-if="mode === 'live' && symbol && symbol !== 'SAMPLE'" :symbol="symbol" :timeframe="timeframe"
                       :sma-short="smaShort" :sma-long="smaLong" />
    </main>

    <aside class="side-col">
      <PaperPanel v-if="mode === 'live'" :symbol="symbol" />
      <VoicePanel />
    </aside>
  </div>
</template>

<style scoped>
/* Top bar: wordmark, symbol search, mode switch, backend health */
.topbar { position: sticky; top: 0; z-index: 10; background: var(--surface); border-bottom: 1px solid var(--border); }
.topbar-inner {
  max-width: 1440px; margin: 0 auto; padding: 12px 24px;
  display: flex; align-items: center; gap: 20px; flex-wrap: wrap;
}
.brand { display: flex; align-items: center; gap: 8px; font-size: 1.25rem; letter-spacing: -0.02em; white-space: nowrap; }
.brand b { color: var(--accent); }
.brand-mark { color: var(--accent); font-size: 1.1rem; }
.search {
  flex: 1 1 320px; max-width: 620px; display: flex; align-items: center;
  background: #f1f3f4; border-radius: 999px; padding: 4px 4px 4px 18px;
}
.search input { flex: 1; min-width: 0; border: none; background: transparent; font-size: 0.95rem; padding: 6px 0; outline: none; }
.search button { padding: 7px 20px; }
.search-off { color: var(--text-muted); padding: 10px 18px; font-size: 0.9rem; }
.segmented { display: inline-flex; background: #f1f3f4; border-radius: 999px; padding: 3px; }
.segmented button { border: none; background: transparent; padding: 6px 16px; color: var(--text-muted); }
.segmented button.active { background: var(--surface); color: var(--text); box-shadow: 0 1px 3px rgb(0 0 0 / 0.12); }
.health { margin-left: auto; display: flex; align-items: center; gap: 6px; font-size: 0.85rem; color: var(--text-muted); }
.health strong { color: var(--text); }
.dot { width: 8px; height: 8px; border-radius: 50%; background: #9aa1a6; }
.dot.ok { background: var(--up); }
.dot.bad { background: var(--down); }
button.link { border: none; background: none; color: var(--accent); padding: 2px 4px; font-weight: 600; }
button.link:hover { background: none; text-decoration: underline; }

/* Status strip under the top bar */
.strip {
  max-width: 1440px; margin: 0 auto; padding: 10px 24px;
  display: flex; flex-wrap: wrap; gap: 8px 28px; border-bottom: 1px solid var(--border);
}
.strip div { min-width: 0; }
.strip .wide { max-width: 360px; }
.strip dt { font-size: 0.7rem; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; }
.strip dd { margin: 2px 0 0; font-weight: 600; font-variant-numeric: tabular-nums; }

/* Two columns: chart and analysis on the left, account and voice on the right */
.layout {
  max-width: 1440px; margin: 0 auto; padding: 20px 24px 40px;
  display: grid; grid-template-columns: minmax(0, 1fr) 400px; gap: 24px; align-items: start;
}
@media (max-width: 1100px) { .layout { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 640px) {
  .topbar-inner, .strip, .layout { padding-left: 16px; padding-right: 16px; }
  .health { margin-left: 0; }
  .topbar { position: static; }
}

/* Quote header */
.quote { padding: 4px 2px 18px; }
.quote-meta { margin: 0; color: var(--text-muted); font-size: 0.85rem; }
.sim-badge { background: var(--accent-soft); color: var(--accent); font-weight: 600; border-radius: 999px; padding: 1px 8px; font-size: 0.75rem; }
.quote h1 { font-size: 1.9rem; margin: 4px 0 6px; }
.price-row { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap; }
.price { font-size: 2.6rem; font-weight: 700; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; }
.change { font-size: 1.35rem; font-weight: 650; font-variant-numeric: tabular-nums; }
.change.up { color: var(--up); }
.change.down { color: var(--down); }
.change.flat { color: var(--text-muted); }
.quote-time { margin: 4px 0 0; color: var(--text-muted); font-size: 0.85rem; }

/* Chart card */
.chart-toolbar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; }
.tabs { display: flex; gap: 4px; }
.tabs button { border: none; background: transparent; color: var(--text); padding: 6px 12px; border-radius: 8px; }
.tabs button.active { background: #e8f0fb; color: #1757c2; }
.sma { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.sma .check { display: flex; align-items: center; gap: 5px; color: var(--text); font-weight: 600; }
.sma-note { margin: 8px 0 12px; }
.sma-note span { margin-left: 6px; }

.replay-form { display: flex; flex-wrap: wrap; gap: 10px 14px; align-items: center; }
.replay-form .full { flex-basis: 100%; }

.notice { padding: 10px 14px; background: #fff7eb; border: 1px solid #f1d3a6; border-radius: 10px; margin: 0 0 16px; }
.values td, .values th { text-align: right; }

.hint { font-size: 0.8rem; color: var(--text-muted); }
.ok { color: var(--up); }
.warn { color: var(--warn); }
.bad { color: var(--down); }
.muted { color: var(--text-muted); }
</style>
