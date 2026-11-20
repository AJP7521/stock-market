<script setup>
// Backtest of the SMA crossover rule (Prompt 17). SIMULATED: Python runs the strategy over stored candles, using only
// candles completed by each decision time, fills at the next candle's open and saves the run in SQLite. This panel
// only sends the settings and shows the stored result: summary, equity curve, orders and fills, round trips, and what
// the result establishes and leaves uncertain.
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { LineSeries, createChart, createSeriesMarkers } from 'lightweight-charts'
import { backendFetch } from './backend.js'
import { colors } from './chartAdapter.js'

const props = defineProps({
  symbol: { type: String, required: true },
  timeframe: { type: String, required: true },
  smaShort: { type: Number, default: 5 },
  smaLong: { type: Number, default: 20 },
})

const short = ref(props.smaShort)
const long = ref(props.smaLong)
const startingCash = ref(1000)
const run = ref(null)
const runs = ref([])
const error = ref('')
const busy = ref(false)
const chartBox = ref(null)
let chart = null

watch(() => props.smaShort, (v) => { short.value = v })
watch(() => props.smaLong, (v) => { long.value = v })

const money = (v) => (v === null || v === undefined ? '—'
  : v.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }))
const pct = (v) => (v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`)
const hm = (ts) => (ts ? ts.replace('T', ' ').replace(':00Z', 'Z') : '—')
const isTest = (r) => /test|synthetic/.test(r?.data_labels ?? '')

async function loadRuns() {
  try {
    const res = await backendFetch(`/api/backtests?${new URLSearchParams({ symbol: props.symbol })}`)
    if (res.ok) runs.value = (await res.json()).runs
  } catch { /* backend unreachable: keep the last list */ }
}

async function runBacktest() {
  busy.value = true
  error.value = ''
  try {
    const res = await backendFetch('/api/backtests', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: props.symbol, timeframe: props.timeframe, short: short.value, long: long.value,
                             starting_cash: startingCash.value }),
    })
    const body = await res.json()
    if (!res.ok) {
      error.value = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
      return
    }
    await show(body)
    loadRuns()
  } catch (e) {
    error.value = `Backtest not run: ${e.message}`
  } finally {
    busy.value = false
  }
}

async function open(id) {
  error.value = ''
  try {
    const res = await backendFetch(`/api/backtests/${id}`)
    if (res.ok) await show(await res.json())
    else error.value = `Run ${id} not found`
  } catch (e) {
    error.value = e.message
  }
}

async function show(body) {
  run.value = body
  await nextTick()
  drawEquity()
}

function drawEquity() {
  chart?.remove()
  chart = null
  if (!chartBox.value || !run.value?.equity?.length) return
  chart = createChart(chartBox.value, {
    autoSize: true,
    layout: { background: { color: '#ffffff' }, textColor: '#5b636a',
              fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif' },
    grid: { vertLines: { visible: false }, horzLines: { color: '#eef0f2' } },
    rightPriceScale: { borderVisible: false },
    timeScale: { timeVisible: true, secondsVisible: false, borderVisible: false },
  })
  const t = (ts) => Date.parse(ts) / 1000
  const series = chart.addSeries(LineSeries, { color: colors.SHORT, lineWidth: 2, priceLineVisible: false })
  series.setData(run.value.equity.map((e) => ({ time: t(e.time), value: e.equity })))
  createSeriesMarkers(series, run.value.orders.filter((o) => o.status === 'filled').map((o) => ({
    time: t(o.fill_candle),
    position: o.side === 'buy' ? 'belowBar' : 'aboveBar',
    shape: o.side === 'buy' ? 'arrowUp' : 'arrowDown',
    color: o.side === 'buy' ? colors.UP : colors.DOWN,
    text: `${o.side} ${o.quantity}`,
  })))
  chart.timeScale().fitContent()
}

watch(() => props.symbol, () => { run.value = null; chart?.remove(); chart = null; loadRuns() })
onMounted(loadRuns)
onBeforeUnmount(() => chart?.remove())
</script>

<template>
  <section class="card backtest" data-testid="backtest-panel">
    <header>
      <strong>Backtest · SMA crossover</strong>
      <span class="tag">SIMULATED</span>
      <span class="hint">Buy on an upward crossover, sell everything on a downward one. Decisions use only candles
        completed by that time; orders fill at the next candle's open; whole shares; zero fees, slippage and spread.</span>
    </header>
    <form class="controls" @submit.prevent="runBacktest">
      <span>{{ symbol }} · {{ timeframe }}</span>
      <label>Short <input v-model.number="short" type="number" step="1" min="1" data-testid="bt-short" style="width:4em" /></label>
      <label>Long <input v-model.number="long" type="number" step="1" min="2" data-testid="bt-long" style="width:4em" /></label>
      <label>Starting cash <input v-model.number="startingCash" type="number" step="1" min="1" data-testid="bt-cash" style="width:7em" /></label>
      <button type="submit" :disabled="busy" data-testid="bt-run">{{ busy ? 'Running…' : 'Run backtest' }}</button>
    </form>
    <p v-if="error" class="bad" data-testid="bt-error">{{ error }}</p>

    <template v-if="run">
      <p class="source" data-testid="bt-source">
        Run #{{ run.id }} · {{ run.candle_count }} completed candles {{ hm(run.first_candle) }} → {{ hm(run.last_candle) }}
        <span v-if="run.excluded_count">({{ run.excluded_count }} still forming, not used)</span>
        · data: <b :class="{ warn: isTest(run) }">{{ run.data_source }}</b>
      </p>
      <div class="tiles">
        <div><span>Final equity</span><b data-testid="bt-final-equity">{{ money(run.summary.final_equity) }}</b>
          <small>from {{ money(run.summary.starting_cash) }}</small></div>
        <div><span>Return</span><b data-testid="bt-return" :class="run.summary.total_return_pct >= 0 ? 'up' : 'down'">{{ pct(run.summary.total_return_pct) }}</b>
          <small>buy &amp; hold <span data-testid="bt-buyhold">{{ pct(run.summary.buy_hold_return_pct) }}</span></small></div>
        <div><span>Max drawdown</span><b data-testid="bt-drawdown">{{ pct(-run.summary.max_drawdown_pct) }}</b>
          <small>{{ money(run.summary.max_drawdown) }}</small></div>
        <div><span>Round trips</span><b data-testid="bt-trips">{{ run.summary.wins }} / {{ run.summary.round_trips }} won</b>
          <small>in market {{ run.summary.exposure_candles }} of {{ run.candle_count }} closes</small></div>
        <div><span>Realized P&amp;L</span><b data-testid="bt-realized">{{ money(run.summary.realized) }}</b>
          <small>cash {{ money(run.summary.final_cash) }}</small></div>
        <div><span>Unrealized P&amp;L</span><b data-testid="bt-unrealized">{{ money(run.summary.unrealized) }}</b>
          <small>{{ run.summary.final_shares }} shares open</small></div>
      </div>

      <h3>Equity at each close</h3>
      <div ref="chartBox" class="equity" data-testid="bt-equity-chart"></div>

      <h3>Orders and fills</h3>
      <div class="table-wrap"><table data-testid="bt-orders">
        <thead><tr><th>#</th><th>Signal (close)</th><th>Signal</th><th>Side</th><th>Status</th><th>Filled (open of)</th>
          <th>Qty</th><th>Price</th><th>Cash after</th><th>Shares after</th><th>Realized after</th></tr></thead>
        <tbody>
          <tr v-for="o in run.orders" :key="o.seq" :data-testid="`bt-order-${o.seq}`">
            <td>{{ o.seq }}</td><td>{{ hm(o.signal_candle) }}</td><td>{{ o.signal }}</td><td>{{ o.side }}</td>
            <td :class="o.status">{{ o.status }}<span v-if="o.reason" class="reason"> · {{ o.reason }}</span></td>
            <td>{{ hm(o.fill_candle) }}</td><td>{{ o.quantity ?? '' }}</td><td>{{ money(o.price) }}</td>
            <td>{{ money(o.cash_after) }}</td><td>{{ o.position_after ?? '' }}</td><td>{{ money(o.realized_after) }}</td>
          </tr>
          <tr v-if="!run.orders.length"><td colspan="11" class="hint">No crossover produced an order.</td></tr>
        </tbody>
      </table></div>

      <h3>Round trips</h3>
      <div class="table-wrap"><table data-testid="bt-roundtrips">
        <thead><tr><th>Entry</th><th>Entry price</th><th>Exit</th><th>Exit price</th><th>Shares</th><th>P&amp;L</th>
          <th>Return</th><th>Status</th></tr></thead>
        <tbody>
          <tr v-for="(t, i) in run.trips" :key="i" :data-testid="`bt-trip-${i + 1}`">
            <td>{{ hm(t.entry_candle) }}</td><td>{{ money(t.entry_price) }}</td><td>{{ hm(t.exit_candle) }}</td>
            <td>{{ money(t.exit_price) }}</td><td>{{ t.shares }}</td>
            <td :class="t.pnl >= 0 ? 'up' : 'down'">{{ money(t.pnl) }}</td><td>{{ pct(t.return_pct) }}</td>
            <td>{{ t.status }}</td>
          </tr>
          <tr v-if="!run.trips.length"><td colspan="8" class="hint">No trades.</td></tr>
        </tbody>
      </table></div>

      <div class="explain" data-testid="bt-explain">
        <div>
          <h4>What this result establishes</h4>
          <ul><li v-for="(s, i) in run.interpretation.establishes" :key="i">{{ s }}</li></ul>
        </div>
        <div>
          <h4>What it leaves uncertain</h4>
          <ul data-testid="bt-uncertain"><li v-for="(s, i) in run.interpretation.uncertain" :key="i">{{ s }}</li></ul>
        </div>
      </div>
      <p class="hint">Assumptions: {{ run.fill_rule }}. Weighted average cost, as in the paper account. An open position
        at the end is valued at the last close, not sold.</p>
    </template>

    <details v-if="runs.length" class="earlier">
      <summary>Earlier runs for {{ symbol }} ({{ runs.length }})</summary>
      <table data-testid="bt-runs">
        <thead><tr><th>Run</th><th>Created (UTC)</th><th>TF</th><th>SMA</th><th>Candles</th><th>Return</th>
          <th>Buy &amp; hold</th><th></th></tr></thead>
        <tbody>
          <tr v-for="r in runs" :key="r.id">
            <td>#{{ r.id }}</td><td>{{ r.created?.slice(0, 19).replace('T', ' ') }}</td><td>{{ r.timeframe }}</td>
            <td>{{ r.short }}/{{ r.long }}</td><td>{{ r.candle_count }}</td><td>{{ pct(r.summary.total_return_pct) }}</td>
            <td>{{ pct(r.summary.buy_hold_return_pct) }}</td>
            <td><button type="button" @click="open(r.id)">Open</button></td>
          </tr>
        </tbody>
      </table>
    </details>
  </section>
</template>

<style scoped>
.backtest { font-size: 0.9rem; }
header, .controls { display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; align-items: center; }
header strong { font-size: 1.05rem; }
.controls { margin-top: 0.5rem; }
.tag { font-size: 0.7rem; font-weight: 700; letter-spacing: 0.06em; padding: 2px 6px; border-radius: 4px;
       background: #fff4e0; color: var(--warn); }
.hint { font-size: 0.8rem; color: var(--text-muted); }
.source { margin: 0.6rem 0 0.4rem; color: var(--text-muted); }
.warn { color: var(--warn); }
.bad, .rejected { color: var(--down); }
.not_filled { color: var(--warn); }
.filled, .up { color: var(--up); }
.down { color: var(--down); }
.reason { color: var(--text-muted); font-size: 0.8rem; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; }
.tiles > div { border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; display: flex;
               flex-direction: column; gap: 2px; }
.tiles span { font-size: 0.75rem; color: var(--text-muted); }
.tiles b { font-size: 1.1rem; font-variant-numeric: tabular-nums; }
.tiles small { font-size: 0.75rem; color: var(--text-muted); }
.equity { height: 220px; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
h3 { font-size: 0.95rem; margin: 0.9rem 0 0.3rem; }
h4 { font-size: 0.9rem; margin: 0 0 0.3rem; }
table { width: 100%; }
th, td { text-align: right; padding: 4px 8px; font-variant-numeric: tabular-nums; }
.table-wrap { overflow-x: auto; }
.explain { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; margin-top: 0.9rem; }
.explain > div { background: var(--bg); border-radius: 8px; padding: 10px 12px; }
.explain ul { margin: 0; padding-left: 1.1rem; }
.explain li { margin: 0.2rem 0; }
.earlier { margin-top: 0.8rem; }
</style>
