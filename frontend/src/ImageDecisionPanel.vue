<script setup>
// "Chart-only decision" (Prompt 16): freeze a snapshot at a completed candle, draw it off screen, capture the PNG,
// and send ONLY that image to the model through POST /api/image-decisions. The model answers long, short or wait.
// The flowchart below shows each part and lights up the active step. No order is placed; the paper account is
// untouched. Emits "decided" so the dashboard can redraw the stance markers.
import { computed, nextTick, ref, watch } from 'vue'
import CandleChart from './CandleChart.vue'

const props = defineProps({
  symbol: { type: String, required: true },
  timeframe: { type: String, required: true },
  candles: { type: Array, required: true },
  formingTimestamp: { type: String, default: null },
  smaShort: { type: Number, required: true },
  smaLong: { type: Number, required: true },
  asOf: { type: String, default: null },
})
const emit = defineEmits(['decided'])

const cutoff = ref('')
const chartCandles = ref(60)
const step = ref(0)             // active flowchart step (1-7), 0 when idle
const reached = ref(0)          // last step completed
const failedStep = ref(0)
const problem = ref(null)
const snapshot = ref(null)
const captured = ref(null)
const result = ref(null)
const previewRef = ref(null)
const renderKey = ref(0)

const completed = computed(() =>
  props.candles.map((c) => c.timestamp).filter((t) => t !== props.formingTimestamp).reverse())
watch(completed, (list) => {
  if (!list.includes(cutoff.value)) cutoff.value = list[0] ?? ''
}, { immediate: true })

const utc = (iso) => (iso ? iso.replace('T', ' ').replace(/:00(\.000)?Z$/, ' UTC').replace(/Z$/, ' UTC') : '—')

// lane: where the step runs. carries: what moves on to the next step.
const STEPS = [
  { lane: 'browser', title: 'Choose a cutoff candle', part: 'Vue panel',
    carries: 'symbol, interval, cutoff → POST /api/decisions/snapshots' },
  { lane: 'server', title: 'Freeze the snapshot', part: 'FastAPI → SQLite decision_snapshots',
    carries: 'chart section (candles, SMA lines) back to the browser' },
  { lane: 'browser', title: 'Draw and capture the chart', part: 'Lightweight Charts → PNG',
    carries: 'PNG + snapshot ID → POST /api/image-decisions' },
  { lane: 'server', title: 'Check and store the PNG', part: 'FastAPI → captures/ folder',
    carries: 'the PNG + capture time, interval, stale flag, position context: no symbol, prices, SMA values, cash or share count' },
  { lane: 'model', title: 'Read the last two completed candles', part: 'Claude (vision) · question chart_signal',
    carries: 'one answer: long, short or wait' },
  { lane: 'server', title: 'Validate, compare and save', part: 'FastAPI: Python applies the same rule → SQLite',
    carries: 'the answer, Python\'s expected answer and the record → browser' },
  { lane: 'browser', title: 'Show the stance and a marker', part: 'Vue panel + chart marker',
    carries: 'nothing: no order, paper account untouched' },
]
const LANES = [
  { id: 'browser', label: 'Browser (Vue)' },
  { id: 'server', label: 'Backend (FastAPI + SQLite)' },
  { id: 'model', label: 'Model (Claude)' },
]
function stateOf(n) {
  if (failedStep.value === n) return 'failed'
  if (step.value === n) return 'active'
  return n <= reached.value ? 'done' : 'idle'
}

function detailText(body) {
  if (body?.detail && typeof body.detail === 'object' && !Array.isArray(body.detail)) return body.detail
  if (typeof body?.detail === 'string') return { problem: body.detail }
  return { problem: (body?.detail ?? []).map((d) => `${d.loc?.at(-1)}: ${d.msg}`).join('; ') || 'request failed' }
}

function advance(n) { reached.value = n - 1; step.value = n }
function fail(info) { failedStep.value = step.value; problem.value = info }

async function ask() {
  problem.value = null; result.value = null; captured.value = null; snapshot.value = null
  failedStep.value = 0; reached.value = 0
  try {
    advance(1)
    advance(2)
    let res = await fetch('/api/decisions/snapshots', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        symbol: props.symbol, timeframe: props.timeframe, short: props.smaShort, long: props.smaLong,
        candle_time: cutoff.value, chart_candles: chartCandles.value, as_of: props.asOf,
      }),
    })
    let body = await res.json()
    if (!res.ok) return fail(detailText(body))
    if (!body.candle) {
      return fail({ problem: `Snapshot #${body.snapshot_id} has no cutoff candle: ${body.missing.join('; ')}`,
                    recapture: 'Choose a completed candle.' })
    }
    snapshot.value = body

    advance(3)
    renderKey.value++
    await nextTick()
    const png = await previewRef.value.capture()
    captured.value = { png, snapshot_id: body.snapshot_id, cutoff: body.candle.timestamp, range: body.chart.range }

    advance(4)
    const request = fetch('/api/image-decisions', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        snapshot_id: body.snapshot_id, image_png: png,
        capture: { snapshot_id: body.snapshot_id, symbol: body.symbol, interval: body.interval,
                   cutoff: body.candle.timestamp },
      }),
    })
    // The backend checks the PNG quickly, then waits for the model: show the model step while waiting.
    const toModel = setTimeout(() => { if (step.value === 4) advance(5) }, 250)
    res = await request
    clearTimeout(toModel)
    body = await res.json()
    if (!res.ok) return fail(detailText(body))
    advance(6)
    result.value = body
    if (body.status !== 'decided') {
      failedStep.value = 5
      problem.value = { problem: `No stance: ${body.status} (${body.error})` }
      reached.value = 6; step.value = 0
      return
    }
    advance(7)
    emit('decided', body)
    reached.value = 7
    step.value = 0
  } catch (e) {
    fail({ problem: `Request failed: ${e.message}` })
  } finally {
    if (failedStep.value) step.value = 0
  }
}

const busy = computed(() => step.value > 0)
const pct = (v) => `${Math.round(v * 100)}%`
// One bar per choice of the question asked, in its order, each matched to its estimate BY VALUE (Prompt 16E); values
// the question does not list are added at the end. Missing, out-of-range or non-numeric values are text, never drawn.
const probRows = computed(() => {
  const list = result.value?.probabilities ?? []
  if (!list.length) return []
  const asked = (question.value?.choices ?? []).map((c) => c.value)
  const values = [...asked, ...list.map((p) => p.value).filter((v) => !asked.includes(v))]
  return values.map((value) => {
    const raw = list.find((p) => p.value === value)?.probability
    const n = typeof raw === 'number' && raw >= 0 && raw <= 1 ? raw : null
    return { value, width: n === null ? '0%' : `${n * 100}%`, text: n === null ? String(raw ?? 'missing') : pct(n) }
  })
})
const stanceClass = computed(() => ({ long: 'ok', short: 'bad', hold: 'info', exit: 'warn', wait: 'muted' })[result.value?.stance] ?? '')
// Position context as sent (application-defined; from the frozen snapshot's paper account)
const positionText = computed(() => {
  const p = result.value?.position
  if (!p) return 'not recorded'
  const test = p.source === 'TEST override'
  if (p.state === 'empty') return `explicitly empty (${test ? 'TEST override' : '0 shares'})`
  if (p.state === 'open') return `open, ${p.direction} (${test ? 'TEST override' : `${p.shares} shares`})`
  return `unknown (${test ? 'TEST override' : 'paper account missing'})`
})
const sentText = computed(() => result.value?.request_sent?.input?.[1]?.content
  ?.filter((p) => p.type === 'input_text').map((p) => p.text).join(' ') ?? '')
const instructions = computed(() => result.value?.request_sent?.input?.[0]?.content?.[0]?.text ?? '')
const question = computed(() => result.value?.request_sent?.questions?.[0] ?? null)
const matchText = computed(() => {
  const r = result.value
  if (!r || r.python_signal == null) return ''
  if (r.stance == null) return `no answer; Python expected ${r.python_signal}`
  return r.signal_match ? 'match' : 'CONFLICT'
})
const sentParts = computed(() => result.value?.request_sent?.input?.[1]?.content?.map((p) => p.type) ?? [])
</script>

<template>
  <section class="card image-decision" data-testid="image-decision">
    <h2>Chart-only decision: long, short, hold, exit or wait</h2>
    <p class="hint">
      The model receives <strong>only the chart image</strong> plus the capture time, whether the chart is stale, and
      the <strong>position context</strong> (whether the paper account holds a position, and its direction), not the
      candle data. It compares the newer of the <strong>last two completed closes</strong> with the preceding close:
      with no position, higher is <strong>long</strong> and lower is <strong>short</strong>; with an open position, a
      move in its direction is <strong>hold</strong> and against it is <strong>exit</strong>. Equal closes, a stale or
      unclear chart, or an unknown position mean <strong>wait</strong>. The answer is a reading exercise:
      <strong>no trade is placed</strong> and the paper account is not changed.
    </p>
    <form class="controls" @submit.prevent="ask">
      <label>
        Cutoff candle (completed)
        <select v-model="cutoff" data-testid="img-cutoff">
          <option v-for="t in completed" :key="t" :value="t">{{ utc(t) }}</option>
        </select>
      </label>
      <label>Candles shown
        <input v-model.number="chartCandles" type="number" min="5" max="500" style="width:4.5em" data-testid="img-range" />
      </label>
      <button type="submit" :disabled="busy || !cutoff" data-testid="img-button">Send chart image</button>
      <span v-if="busy" class="hint" data-testid="img-busy">Step {{ step }}: {{ STEPS[step - 1].title }}…</span>
    </form>

    <!-- Educational flowchart: one row per step, placed in the lane where it runs. -->
    <figure class="flow" data-testid="img-flow" aria-label="How a chart-only decision moves through the system">
      <div class="lanes" aria-hidden="true">
        <div v-for="lane in LANES" :key="lane.id" class="lane-head" :class="`lane-${lane.id}`">{{ lane.label }}</div>
      </div>
      <ol class="flow-steps">
        <li v-for="(s, i) in STEPS" :key="i" class="flow-row" :data-testid="`img-flow-step-${i + 1}`"
            :data-state="stateOf(i + 1)">
          <div class="node" :class="[`lane-${s.lane}`, `state-${stateOf(i + 1)}`]">
            <span class="num">{{ i + 1 }}</span>
            <span><strong>{{ s.title }}</strong><small>{{ s.part }}</small>
              <em class="lane-tag">{{ LANES.find((l) => l.id === s.lane).label }}</em></span>
          </div>
          <div class="carries" :class="`from-${s.lane}`"><span class="arrow">↓</span> {{ s.carries }}</div>
        </li>
      </ol>
      <figcaption class="hint">
        Kept on the server and never sent to the model: symbol, prices, opens and closes, SMA values, the Python
        crossover and the paper account. The backend uses them only to check that the image matches the snapshot and
        to apply the same rule itself, so the model's answer can be compared afterwards. Stale: the cutoff candle
        completed more than one interval before the capture time.
      </figcaption>
    </figure>

    <div v-if="problem" class="notice bad-box" data-testid="img-problem">
      <strong>{{ problem.problem }}</strong>
      <div v-if="problem.recapture">What to do: {{ problem.recapture }}</div>
    </div>

    <div v-if="snapshot" class="offscreen" aria-hidden="true">
      <CandleChart ref="previewRef" :key="renderKey" :rows="snapshot.chart.candles" :sma="snapshot.chart.sma"
                   :indicators="snapshot.chart.indicators" label="" :fixed-size="{ width: 800, height: 400 }" />
    </div>

    <div v-if="captured" class="capture-row">
      <figure class="capture" data-testid="img-capture">
        <img :src="captured.png" alt="The chart image sent to the model" data-testid="img-capture-img" />
        <figcaption class="hint">
          The only input the model saw · snapshot #{{ captured.snapshot_id }} · cutoff
          <span data-testid="img-capture-cutoff">{{ utc(captured.cutoff) }}</span> ·
          {{ captured.range.candles }} candles
        </figcaption>
      </figure>

      <div v-if="result" class="result" data-testid="img-result">
        <p class="stance-line" data-testid="img-label" :data-label="result.label ?? ''">
          chart_signal
          <strong class="stance" :class="stanceClass" data-testid="img-stance">{{ result.stance ?? 'none' }}</strong>
          <span v-if="result.chosen_probability !== null" class="chosen-prob" data-testid="img-chosen-prob">
            · {{ pct(result.chosen_probability) }}</span>
        </p>
        <div v-if="result.probabilities_check" class="probs" data-testid="img-probabilities">
          <p class="hint">
            Probabilities: <strong data-testid="img-prob-source">{{ result.probabilities_source ?? 'none' }}</strong>
            <template v-if="result.probabilities_source === 'model-estimated'"> (Claude's own estimate, not a measured
              probability)</template> ·
            check <strong data-testid="img-prob-check" :class="result.probabilities_check === 'valid' ? 'ok' : 'bad'">
              {{ result.probabilities_check }}</strong>
          </p>
          <div v-for="p in probRows" :key="p.value" class="prob-row" :data-testid="`img-prob-${p.value}`">
            <span class="prob-name" :class="{ chosen: p.value === result.stance }">{{ p.value }}</span>
            <span class="prob-bar"><span :style="{ width: p.width }"></span></span>
            <span class="prob-value">{{ p.text }}</span>
          </div>
        </div>
        <p class="hint">
          Image decision <span data-testid="img-decision-id">#{{ result.image_decision_id }}</span> ·
          status <strong data-testid="img-status">{{ result.status }}</strong> ·
          model call: <strong data-testid="img-call-kind" :class="result.call_kind === 'mock' ? 'warn' : ''">
            {{ result.call_kind === 'mock' ? 'MOCK (provider test double)' : 'live' }}</strong>
          ({{ result.provider }}, {{ result.model }}<template v-if="result.latency_ms !== null">,
          {{ result.latency_ms }} ms</template>)
        </p>
        <p v-if="result.error" class="bad" data-testid="img-error">{{ result.error }}</p>
        <p class="hint" data-testid="img-context">
          Capture time <span data-testid="img-capture-time">{{ utc(result.capture_time) }}</span> ·
          <strong data-testid="img-stale" :class="result.stale ? 'warn' : ''">{{ result.stale ? 'stale' : 'not stale' }}</strong>
          · position <strong data-testid="img-position" :class="result.position?.state === 'unknown' ? 'warn' : ''">
            {{ positionText }}</strong>
          (sent to the model, except the share count)
        </p>
        <p class="hint" data-testid="img-compare">
          Python applied the same rule to the last two completed candles (not sent):
          <strong data-testid="img-python-signal">{{ result.python_signal ?? 'unknown' }}</strong> ·
          <strong data-testid="img-signal-check" :class="result.signal_match ? 'ok' : 'bad'">{{ matchText }}</strong>.
          SMA crossover at this candle: {{ result.python_crossover ?? 'unknown' }}.
        </p>
        <p class="no-trade" data-testid="img-no-trade">No trade placed. This answer is not eligible for execution.</p>
        <details>
          <summary>What the model was sent</summary>
          <p class="hint" data-testid="img-sent-parts">Message parts: {{ sentParts.join(' + ') }}</p>
          <p class="hint">“{{ sentText }}” + the PNG above.</p>
          <p class="hint">Instructions: {{ instructions }}</p>
          <template v-if="question">
            <p class="hint" data-testid="img-question">Question <strong>{{ question.name }}</strong>:
              {{ question.instructions }}</p>
            <ul class="hint">
              <li v-for="c in question.choices" :key="c.value"><strong>{{ c.value }}</strong>: {{ c.description }}</li>
            </ul>
          </template>
        </details>
      </div>
    </div>
  </section>
</template>

<style scoped>
.controls { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; }
.hint { font-size: 0.8rem; color: #666; }
.offscreen { position: absolute; left: -10000px; top: 0; width: 800px; }

/* flowchart: three lanes, one row per step */
.flow { margin: 0.9rem 0; padding: 0.75rem; background: #f8f9fa; border: 1px solid #e3e6e8; border-radius: 10px; }
.lanes, .flow-row { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0.5rem; }
.lane-head { font-size: 0.72rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;
  color: #555; padding: 0 0.2rem 0.35rem; border-bottom: 2px solid currentColor; }
.lane-head.lane-browser { color: #2962ff; }
.lane-head.lane-server { color: #0f8a5f; }
.lane-head.lane-model { color: #8a3ffc; }
.flow-steps { list-style: none; margin: 0.4rem 0 0; padding: 0; }
.flow-row { align-items: center; }
.node { display: flex; gap: 0.5rem; align-items: flex-start; padding: 0.45rem 0.55rem; border-radius: 8px;
  background: #fff; border: 1.5px solid #d6dade; font-size: 0.82rem; transition: all 0.2s; }
.node small { display: block; color: #666; font-size: 0.72rem; }
.lane-tag { display: none; font-size: 0.68rem; font-style: normal; color: #888; }
.node .num { flex: none; width: 1.4rem; height: 1.4rem; border-radius: 50%; display: grid; place-items: center;
  font-size: 0.72rem; font-weight: 700; background: #eef0f2; color: #333; }
.node.lane-browser { grid-column: 1; border-left: 4px solid #2962ff; }
.node.lane-server { grid-column: 2; border-left: 4px solid #0f8a5f; }
.node.lane-model { grid-column: 3; border-left: 4px solid #8a3ffc; }
.node.state-active { box-shadow: 0 0 0 3px rgba(41, 98, 255, 0.25); border-color: #2962ff; }
.node.state-active .num { background: #2962ff; color: #fff; }
.node.state-done .num { background: #0f8a5f; color: #fff; }
.node.state-failed { border-color: #d8273a; background: #fdecea; }
.node.state-failed .num { background: #d8273a; color: #fff; }
.carries { grid-column: 1 / -1; font-size: 0.72rem; color: #666; padding: 0.15rem 0 0.35rem 0.6rem; }
.carries.from-server { padding-left: calc(33.3% + 0.6rem); }
.carries.from-model { padding-left: calc(66.6% + 0.6rem); }
.flow-row:last-child .carries { color: #b26b00; }
.arrow { font-weight: 700; color: #999; }

.capture-row { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: 1rem; align-items: start; }
@media (max-width: 760px) {
  .capture-row { grid-template-columns: minmax(0, 1fr); }
  .carries.from-server, .carries.from-model { padding-left: 0.6rem; }
  /* one column: the lane becomes a tag inside each step */
  .lanes { display: none; }
  .node.lane-browser, .node.lane-server, .node.lane-model { grid-column: 1 / -1; }
  .lane-tag { display: block; }
}
.capture { margin: 0; }
.capture img { max-width: 100%; border: 1px solid #e3e6e8; border-radius: 8px; display: block; }
.stance-line { margin: 0 0 0.3rem; font-size: 0.9rem; }
.chosen-prob { font-size: 1.3rem; font-weight: 600; }
.probs { margin: 0.2rem 0 0.6rem; }
.prob-row { display: grid; grid-template-columns: 3.2rem minmax(0, 1fr) 3rem; gap: 0.5rem; align-items: center;
  font-size: 0.8rem; margin: 0.15rem 0; }
.prob-name.chosen { font-weight: 700; }
.prob-bar { height: 0.55rem; background: #eef0f2; border-radius: 4px; overflow: hidden; }
.prob-bar span { display: block; height: 100%; background: #8a95a1; }
.prob-value { text-align: right; font-variant-numeric: tabular-nums; }
.stance { display: inline-block; margin-left: 0.4rem; font-size: 1.6rem; text-transform: uppercase; letter-spacing: 0.03em; }
.no-trade { font-size: 0.82rem; padding: 0.4rem 0.6rem; background: #fff8e6; border: 1px solid #f3dca4; border-radius: 8px; }
.notice { padding: 0.6rem 0.8rem; margin: 0.5rem 0; border-radius: 10px; }
.bad-box { background: #fdecea; border: 1px solid #f5c2c0; }
.ok { color: #1b7f5a; }
.bad { color: #c62828; }
.muted { color: #6b7280; }
.warn { color: #b26b00; }
.info { color: #2962ff; }
</style>
