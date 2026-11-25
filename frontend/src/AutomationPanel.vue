<script setup>
// Paper automation (Prompt 12). While running, each newly completed candle is analysed once: the backend claims it
// (deduplicated in SQLite) and freezes its snapshot, this panel renders that snapshot off screen and captures a fresh
// PNG with the same chart capture as "Ask about chart", and the analyst + reviewer decide. Only an eligible,
// approved decision becomes a paper order; the fill happens at the next observed price after Python's rechecks.
// Stop disables new executions, including a model reply still pending. Trace follows one decision ID end to end.
// Rule mode (Prompt 17A): the fixed SMA crossover rule decides in Python when the candle is claimed; no chart capture
// and no model call. The backend returns the run already decided, so this panel skips the capture step.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import CandleChart from './CandleChart.vue'
import { backendFetch, createPoller } from './backend.js'

const props = defineProps({
  symbol: { type: String, required: true },
  timeframe: { type: String, required: true },
  smaShort: { type: Number, required: true },
  smaLong: { type: Number, required: true },
})

const view = ref(null)            // {state, account, runs, max_decision_age_s}
const busy = ref('')
const message = ref('')
const snapshot = ref(null)        // snapshot being rendered for capture
const renderKey = ref(0)
const previewRef = ref(null)
const trace = ref(null)
const orderSize = ref(1)          // shares for buy and sell while running (close sells the whole position)
const mode = ref('ai')            // 'ai' (analyst + reviewer) or 'rule' (fixed SMA crossover rule)
const attempted = new Set()       // candles this page already tried (the backend also deduplicates)
let working = false

const params = computed(() => ({ symbol: props.symbol, timeframe: props.timeframe, short: props.smaShort, long: props.smaLong }))
const running = computed(() => !!view.value?.state?.enabled)
const money = (v) => (v === null || v === undefined ? '—'
  : v.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }))
const utc = (iso) => (iso ? iso.replace('T', ' ').replace(/:00(\.000)?Z$/, ' UTC').replace(/Z$/, ' UTC') : '—')

async function post(url, body) {
  const res = await backendFetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const data = await res.json()
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail))
  return data
}

async function refresh() {
  const res = await backendFetch(`/api/automation/status?${new URLSearchParams(params.value)}`)
  if (res.ok) view.value = await res.json()
}

async function start() {
  message.value = ''
  attempted.clear()
  try { view.value = await post('/api/automation/start', { ...params.value, order_size: orderSize.value, mode: mode.value }) }
  catch (e) { message.value = e.message }
}

async function stop() {
  try { view.value = await post('/api/automation/stop', params.value) } catch (e) { message.value = e.message }
}

/** Find the newest completed candle not yet handled and run it through the decision service. */
async function tick() {
  await refresh()
  if (!running.value || working) return
  const res = await backendFetch(`/api/history?${new URLSearchParams({ symbol: props.symbol, timeframe: props.timeframe })}`)
  if (!res.ok) return
  const body = await res.json()
  // Every candle that completed after Start and has no run yet, oldest first (the backend also deduplicates).
  const startedMs = view.value.state.started_ms
  const handled = new Set((view.value.runs ?? []).map((r) => r.candle_ts))
  const endMs = (c, s) => (s?.interval_end ? Date.parse(s.interval_end) : Date.parse(c.timestamp) + 60000)
  const next = body.candles
    .filter((c, i) => body.status[i]?.complete && endMs(c, body.status[i]) > startedMs)
    .map((c) => c.timestamp)
    .find((t) => !handled.has(t) && !attempted.has(t))
  if (!next) return
  attempted.add(next)
  working = true
  try {
    await analyse(next)
  } finally {
    working = false
    busy.value = ''
    await refresh()
  }
}

async function analyse(candleTime) {
  let claim
  try {
    busy.value = `Freezing the snapshot at ${utc(candleTime)}…`
    claim = await post('/api/automation/runs', { ...params.value, candle_time: candleTime })
  } catch (e) {
    message.value = `${utc(candleTime)}: ${e.message}`
    return
  }
  if (!claim.created || claim.run.status !== 'analysing') return
  const run = claim.run
  let png
  try {
    busy.value = `Capturing the chart for ${utc(candleTime)}…`
    snapshot.value = claim.snapshot
    renderKey.value++
    await nextTick()
    png = await Promise.race([
      previewRef.value.capture(),
      new Promise((_, reject) => setTimeout(() => reject(new Error('chart capture timed out after 15 s')), 15000)),
    ])
    if (!png || !png.startsWith('data:image/png;base64,')) throw new Error('the chart export returned no PNG')
  } catch (e) {
    await post(`/api/automation/runs/${run.id}/fail`, { reason: e.message }).catch((err) => { message.value = err.message })
    return
  }
  busy.value = `Analyst and reviewer for ${utc(candleTime)}…`
  const snap = claim.snapshot
  await post(`/api/automation/runs/${run.id}/analyse`, {
    image_png: png,
    capture: { snapshot_id: snap.snapshot_id, symbol: snap.symbol, interval: snap.interval, cutoff: snap.candle.timestamp },
  }).catch((e) => { message.value = e.message })
}

async function showTrace(decisionId) {
  try {
    const res = await backendFetch(`/api/automation/trace/${decisionId}`)
    trace.value = res.ok ? await res.json() : null
  } catch (e) {
    message.value = `Trace not loaded: ${e.message}`
  }
}

function outcome(r) {
  if (r.status === 'filled') return 'filled'
  if (r.status === 'order_pending') return 'order pending'
  return r.status.replace('_', ' ')
}

// Every 2 s, less often while the backend is unreachable.
const poller = createPoller(tick, 2000)
const offline = poller.offline
onMounted(() => poller.start())
onBeforeUnmount(() => poller.stop())
watch(params, () => { attempted.clear(); trace.value = null; poller.now() })
</script>

<template>
  <section class="auto card" data-testid="automation">
    <h2>Paper automation ({{ (running ? view.state.mode : mode) === 'rule' ? 'SMA crossover rule' : 'analyst + reviewer' }} → paper account)</h2>
    <p v-if="offline" class="bad" data-testid="auto-offline">Backend unreachable: showing the last state received;
      retrying less often until it answers.</p>
    <div class="controls">
      <span class="modes" role="radiogroup" aria-label="Decision mode">
        <label><input v-model="mode" type="radio" value="ai" :disabled="running" data-testid="auto-mode-ai" />
          AI (analyst + reviewer)</label>
        <label><input v-model="mode" type="radio" value="rule" :disabled="running" data-testid="auto-mode-rule" />
          Rule (SMA crossover)</label>
      </span>
      <label>Order size
        <input v-model.number="orderSize" type="number" min="1" step="1" :disabled="running" style="width:4em"
               data-testid="auto-order-size" /></label>
      <button type="button" :disabled="running" data-testid="auto-start" @click="start">Start</button>
      <button type="button" :disabled="!running" data-testid="auto-stop" @click="stop">Stop</button>
      <strong data-testid="auto-state" :class="running ? 'ok' : 'muted'">{{ running ? 'Running' : 'Stopped' }}</strong>
      <span class="hint">{{ symbol }} · {{ timeframe }} · strategy sma{{ smaShort }}x{{ smaLong }}
        <template v-if="view?.state"> · mode <span data-testid="auto-state-mode">{{ view.state.mode }}</span>
          · generation {{ view.state.generation }} · order size
          <span data-testid="auto-state-order-size">{{ view.state.order_size }}</span>
          · started {{ view.state.started_at ?? '—' }}</template>
        · decisions older than {{ view?.max_decision_age_s ?? '—' }} s are not filled</span>
      <span v-if="busy" class="hint" data-testid="auto-busy">{{ busy }}</span>
    </div>
    <p v-if="view?.account" class="acct" data-testid="auto-account">
      Account <strong data-testid="auto-account-id">{{ view.account.account }}</strong>
      <span v-if="view.account.account !== 'paper'" class="warn">(disposable TEST account: test market data)</span>
      · cash <strong data-testid="auto-cash">{{ money(view.account.cash) }}</strong>
      · {{ symbol }} shares <strong data-testid="auto-shares">{{ view.account.positions?.[symbol] ?? 0 }}</strong>
      · fills <strong data-testid="auto-fills">{{ view.account.fill_count }}</strong>
      · starting cash {{ money(view.account.starting_cash) }}
    </p>
    <p v-if="message" class="bad" data-testid="auto-message">{{ message }}</p>
    <p v-if="(running ? view.state.mode : mode) === 'rule'" class="hint" data-testid="auto-rule-note">
      Rule: upward crossover with no shares → buy the order size; downward crossover with shares → close the whole
      position; otherwise hold or wait (no order). Decided in Python from the frozen snapshot; no chart image, no model
      call. Fills at the next observed price with the same pre-fill checks.</p>

    <div v-if="snapshot" class="offscreen" aria-hidden="true">
      <CandleChart ref="previewRef" :key="renderKey" :rows="snapshot.chart.candles" :sma="snapshot.chart.sma"
                   :indicators="snapshot.chart.indicators" label="" :fixed-size="{ width: 800, height: 400 }" />
    </div>

    <table v-if="view?.runs?.length" class="runs" data-testid="auto-runs">
      <thead>
        <tr><th>Candle</th><th>Mode</th><th>Decision</th><th>Crossover</th><th>Proposal / rule</th><th>Verdict</th><th>Eligible</th>
          <th>Model calls</th><th>Outcome</th><th>Reason</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="r in view.runs" :key="r.id" :data-testid="`auto-run-${r.candle_ts}`">
          <td>{{ utc(r.candle_ts) }}</td>
          <td :data-testid="`auto-mode-${r.candle_ts}`">{{ r.mode }}</td>
          <td>{{ r.decision_id ? `#${r.decision_id}` : '—' }}</td>
          <td>{{ r.crossover ?? '—' }}</td>
          <td :data-testid="`auto-proposal-${r.candle_ts}`">{{ r.proposal ?? '—' }}</td>
          <td>{{ r.verdict ?? '—' }}</td>
          <td>{{ r.eligible === null ? '—' : r.eligible ? 'yes' : 'no' }}</td>
          <td :class="r.call_kind === 'mock' ? 'warn' : ''">{{ r.mode === 'rule' ? 'none' : r.call_kind === 'mock' ? 'MOCK' : r.call_kind ?? '—' }}</td>
          <td :data-testid="`auto-outcome-${r.candle_ts}`"><strong>{{ outcome(r) }}</strong></td>
          <td class="hint">{{ r.reason ?? '' }}</td>
          <td><button v-if="r.decision_id" type="button" :data-testid="`auto-trace-${r.decision_id}`"
                      @click="showTrace(r.decision_id)">Trace</button></td>
        </tr>
      </tbody>
    </table>
    <p v-else class="hint">No automation runs yet. While running, each newly completed candle is analysed once.</p>

    <div v-if="trace" class="trace" data-testid="auto-trace">
      <h3>Decision #{{ trace.decision_id }} from crossover to account</h3>
      <ol>
        <li data-testid="trace-crossover"><strong>Crossover</strong>: candle {{ utc(trace.crossover.candle.timestamp) }}
          closed {{ trace.crossover.candle.close }}; SMA{{ trace.crossover.indicators.sma_short_window }}
          {{ trace.crossover.indicators.sma_short }} vs SMA{{ trace.crossover.indicators.sma_long_window }}
          {{ trace.crossover.indicators.sma_long }}: <strong>{{ trace.crossover.indicators.crossover_at_candle }}</strong>
          · decision time {{ trace.crossover.decision_time }} · {{ trace.crossover.market_data }}
          · account then: {{ money(trace.account_at_decision.cash) }}, {{ trace.account_at_decision.position }} shares
          <a v-if="trace.image_url" :href="trace.image_url" target="_blank">(chart PNG)</a></li>
        <li data-testid="trace-analyst"><strong>Analyst</strong> ({{ trace.analyst.call_kind === 'mock' ? 'MOCK' : 'live' }},
          {{ trace.analyst.model }}): <strong>{{ trace.analyst.proposal ?? trace.analyst.status }}</strong>
          · answers {{ JSON.stringify(trace.analyst.answers) }}</li>
        <li data-testid="trace-review"><strong>Reviewer</strong>
          <template v-if="trace.review"> ({{ trace.review.call_kind === 'mock' ? 'MOCK' : 'live' }}, {{ trace.review.model }}):
            <strong>{{ trace.review.verdict ?? trace.review.status }}</strong>
            · {{ (trace.review.evidence ?? []).join('; ') }}</template>
          <template v-else>not run</template></li>
        <li data-testid="trace-final"><strong>Python final check</strong>:
          <strong :class="trace.eligible ? 'ok' : 'bad'">{{ trace.eligible ? 'eligible' : 'not eligible' }}</strong>
          <template v-if="trace.final_check && !trace.eligible">
            ({{ trace.final_check.gates.filter((g) => !g.passed).map((g) => g.gate).join('; ') }})</template></li>
        <li data-testid="trace-execution"><strong>Execution</strong>:
          <template v-if="trace.order">order #{{ trace.order.id }} {{ trace.order.side }} {{ trace.order.quantity }}
            · {{ trace.order.status }}<template v-if="trace.order.reason"> ({{ trace.order.reason }})</template>
            <template v-if="trace.order.fill"> · filled at {{ trace.order.fill.price }} (observation
              #{{ trace.order.fill.observation_id }}, {{ trace.order.fill.executed_at }}) · cash after
              {{ money(trace.order.fill.cash_after) }}, position {{ trace.order.fill.position_after }}</template>
          </template>
          <template v-else>no order · {{ trace.run?.status }}<template v-if="trace.run?.reason"> ({{ trace.run.reason }})</template></template></li>
        <li v-if="trace.pnl_row" data-testid="trace-pnl"><strong>P&amp;L row</strong>: {{ trace.pnl_row.description }}
          · realized <span data-testid="trace-pnl-realized">{{ money(trace.pnl_row.realized_micros / 1e6) }}</span>
          · unrealized <span data-testid="trace-pnl-unrealized">{{ trace.pnl_row.unrealized_micros === null ? 'unavailable'
            : money(trace.pnl_row.unrealized_micros / 1e6) }}</span>
          · total {{ trace.pnl_row.total_micros === null ? 'unavailable' : money(trace.pnl_row.total_micros / 1e6) }}</li>
        <li data-testid="trace-account"><strong>Account now</strong> ({{ trace.account_now?.account }}):
          {{ money(trace.account_now?.cash) }}, {{ trace.account_now?.positions?.[symbol] ?? 0 }} {{ symbol }} shares,
          {{ trace.account_now?.fill_count }} fills</li>
      </ol>
    </div>
  </section>
</template>

<style scoped>
.controls { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; }
.modes { display: inline-flex; gap: 0.75rem; }
.hint { font-size: 0.8rem; color: #666; }
.acct { font-size: 0.9rem; }
.offscreen { position: absolute; left: -10000px; top: 0; width: 800px; }
.runs { margin-top: 0.5rem; }
.runs th, .runs td { text-align: left; vertical-align: top; }
.trace ol { font-size: 0.85rem; padding-left: 1.25rem; }
.trace li { margin: 0.25rem 0; }
.ok { color: #1b7f5a; }
.bad { color: #c62828; }
.warn { color: #b26b00; }
.muted { color: #666; }
</style>
