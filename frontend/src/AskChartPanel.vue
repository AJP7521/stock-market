<script setup>
// "Ask about chart" (Prompt 10A): freeze a decision snapshot at a selected completed candle, render that snapshot's
// candles, volume and indicator lines in a separate chart, capture it as a PNG, and send the PNG with the snapshot ID
// to POST /api/decisions. Shows what was captured, what the model was asked, its answers and the backend checks.
// It never places an order; the paper account is untouched.
import { computed, nextTick, ref, watch } from 'vue'
import CandleChart from './CandleChart.vue'

const props = defineProps({
  symbol: { type: String, required: true },
  timeframe: { type: String, required: true },
  candles: { type: Array, required: true },          // the dashboard's candles (oldest first)
  formingTimestamp: { type: String, default: null }, // the latest candle, if it is still forming
  smaShort: { type: Number, required: true },
  smaLong: { type: Number, required: true },
  asOf: { type: String, default: null },             // replay time, so the snapshot uses only data received by then
})

const cutoff = ref('')
const chartCandles = ref(60)
const busy = ref('')            // current step, or '' when idle
const problem = ref(null)       // {problem, recapture} or {problem}
const snapshot = ref(null)      // frozen snapshot (with chart section) used for the capture
const captured = ref(null)      // {png, symbol, interval, cutoff, snapshot_id, range}
const decision = ref(null)
const previewRef = ref(null)
const renderKey = ref(0)

const completed = computed(() =>
  props.candles.map((c) => c.timestamp).filter((t) => t !== props.formingTimestamp).reverse())

// Default to the newest completed candle; keep the student's choice while it still exists.
watch(completed, (list) => {
  if (!list.includes(cutoff.value)) cutoff.value = list[0] ?? ''
}, { immediate: true })

const utc = (iso) => (iso ? iso.replace('T', ' ').replace(/:00(\.000)?Z$/, ' UTC').replace(/Z$/, ' UTC') : '—')

function detailText(body) {
  if (body?.detail && typeof body.detail === 'object' && !Array.isArray(body.detail)) return body.detail
  if (typeof body?.detail === 'string') return { problem: body.detail }
  return { problem: (body?.detail ?? []).map((d) => `${d.loc?.at(-1)}: ${d.msg}`).join('; ') || 'request failed' }
}

async function ask() {
  problem.value = null
  decision.value = null
  captured.value = null
  snapshot.value = null
  try {
    busy.value = 'Freezing the snapshot…'
    let res = await fetch('/api/decisions/snapshots', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        symbol: props.symbol, timeframe: props.timeframe, short: props.smaShort, long: props.smaLong,
        candle_time: cutoff.value, chart_candles: chartCandles.value, as_of: props.asOf,
      }),
    })
    let body = await res.json()
    if (!res.ok) { problem.value = detailText(body); return }
    if (!body.complete) {
      problem.value = { problem: `Snapshot #${body.snapshot_id} is incomplete: ${body.missing.join('; ')}`,
                        recapture: 'Choose a completed candle with enough history for both SMAs.' }
      return
    }
    snapshot.value = body

    busy.value = 'Rendering and capturing the chart…'
    renderKey.value++
    await nextTick()
    const png = await previewRef.value.capture()
    captured.value = {
      png, snapshot_id: body.snapshot_id, symbol: body.symbol, interval: body.interval,
      cutoff: body.candle.timestamp, range: body.chart.range, decision_time: body.decision_time,
      market_data: body.market_data_source,
    }

    busy.value = 'Asking the model…'
    res = await fetch('/api/decisions', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        snapshot_id: body.snapshot_id, image_png: png,
        capture: { snapshot_id: body.snapshot_id, symbol: body.symbol, interval: body.interval, cutoff: body.candle.timestamp },
      }),
    })
    body = await res.json()
    if (!res.ok) { problem.value = detailText(body); return }
    decision.value = body
  } catch (e) {
    problem.value = { problem: `Request failed: ${e.message}` }
  } finally {
    busy.value = ''
  }
}

// One row per question: the model's answer and what the backend checked it against.
const answerRows = computed(() => {
  const d = decision.value
  if (!d) return []
  const python = d.backend_facts?.crossover_at_candle
  return d.questions.map((q) => {
    const answer = d.answers?.[q.name] ?? null
    let check = '', ok = null
    if (q.name === 'action') {
      ok = d.validation.status === 'valid'
      check = d.validation.checks.map((c) => `${c.passed ? '✓' : '✗'} ${c.rule} (${c.observed})`).join('; ')
        || `not checked (${d.status})`
    } else if (q.name === 'crossover') {
      ok = answer === python
      check = answer === null ? 'missing answer'
        : answer === 'unreadable' ? `chart unreadable; Python calculated ${python}`
        : `Python calculated ${python}: ${answer === python ? 'match' : 'CONFLICT'}`
    } else if (q.name === 'evidence') {
      ok = answer === 'sufficient'
      check = answer === null ? 'missing answer' : answer === 'sufficient' ? 'sufficient' : 'not sufficient'
    }
    return { name: q.name, instructions: q.instructions, choices: q.choices.map((c) => c.value), answer, check, ok }
  })
})

const failedChecks = computed(() => decision.value?.final_check
  ? decision.value.final_check.gates.filter((g) => !g.passed).map((g) => g.gate)
  : [
      ...(decision.value?.validation.checks ?? []).filter((c) => !c.passed).map((c) => c.rule),
      ...(decision.value?.chart_checks ?? []).filter((c) => !c.passed).map((c) => c.rule),
    ])
const review = computed(() => decision.value?.review ?? null)

const sentText = computed(() => decision.value?.request_sent?.input?.[1]?.content
  ?.filter((p) => p.type === 'input_text').map((p) => p.text).join(' ') ?? '')
const sentJson = computed(() => JSON.stringify(decision.value?.request_sent?.input?.[1]?.content
  ?.find((p) => p.type === 'input_json')?.data ?? null, null, 2))
const strategy = computed(() => decision.value?.request_sent?.input?.[0]?.content?.[0]?.text ?? '')
</script>

<template>
  <section class="ask card" data-testid="ask-chart">
    <h2>Ask about chart (analyst + reviewer)</h2>
    <form class="controls" @submit.prevent="ask">
      <label>
        Cutoff candle (completed)
        <select v-model="cutoff" data-testid="ask-cutoff">
          <option v-for="t in completed" :key="t" :value="t">{{ utc(t) }}</option>
        </select>
      </label>
      <label>Candles shown
        <input v-model.number="chartCandles" type="number" min="5" max="500" style="width:4.5em" data-testid="ask-range" />
      </label>
      <button type="submit" :disabled="!!busy || !cutoff" data-testid="ask-button">Ask about chart</button>
      <span v-if="busy" class="hint" data-testid="ask-busy">{{ busy }}</span>
      <span class="hint">Requests analysis only: no order is placed and the paper account is unchanged.</span>
    </form>

    <div v-if="problem" class="notice bad-box" data-testid="ask-problem">
      <strong>{{ problem.problem }}</strong>
      <div v-if="problem.recapture" data-testid="ask-recapture">What to do: {{ problem.recapture }}</div>
    </div>

    <!-- Renders the frozen snapshot only (off screen); the PNG below is what the model receives. -->
    <div v-if="snapshot" class="offscreen" aria-hidden="true">
      <CandleChart ref="previewRef" :key="renderKey" :rows="snapshot.chart.candles" :sma="snapshot.chart.sma"
                   :indicators="snapshot.chart.indicators" label="" :fixed-size="{ width: 800, height: 400 }" />
    </div>

    <figure v-if="captured" class="capture" data-testid="ask-capture">
      <img :src="captured.png" alt="Captured chart sent to the model" data-testid="ask-capture-img" />
      <figcaption data-testid="ask-capture-caption">
        <strong data-testid="ask-capture-symbol">{{ captured.symbol }}</strong> ·
        <span data-testid="ask-capture-interval">{{ captured.interval }}</span> ·
        cutoff <strong data-testid="ask-capture-cutoff">{{ utc(captured.cutoff) }}</strong> ·
        snapshot <span data-testid="ask-capture-snapshot">#{{ captured.snapshot_id }}</span> ·
        {{ captured.range.candles }} candles {{ utc(captured.range.first) }} → {{ utc(captured.range.last) }} ·
        decision time {{ captured.decision_time }} ·
        market data: <span data-testid="ask-market-data">{{ captured.market_data }}</span>
        <div class="hint">Frozen copy: live updates to the chart above do not change it.</div>
      </figcaption>
    </figure>

    <div v-if="decision" class="result" data-testid="ask-result">
      <p>
        Decision <strong data-testid="ask-decision-id">#{{ decision.decision_id }}</strong> ·
        status <strong data-testid="ask-status">{{ decision.status }}</strong> ·
        model call: <strong data-testid="ask-call-kind" :class="decision.call_kind === 'mock' ? 'warn' : ''">
          {{ decision.call_kind === 'mock' ? 'MOCK (provider test double)' : 'live' }}</strong>
        ({{ decision.provider }}, {{ decision.model }}<template v-if="decision.latency_ms !== null">,
        {{ decision.latency_ms }} ms</template>)
      </p>

      <h3>What the model was asked</h3>
      <p class="hint" data-testid="ask-sent-text">
        One user message: “{{ sentText }}” + the captured PNG
        <a v-if="decision.image" :href="decision.image.url" target="_blank" data-testid="ask-stored-image">(stored PNG,
          {{ decision.image.width }}×{{ decision.image.height }}, sha256 {{ decision.image.sha256.slice(0, 12) }}…)</a>
        + the snapshot JSON.
      </p>
      <details>
        <summary>Strategy text and snapshot JSON sent</summary>
        <p class="hint">{{ strategy }}</p>
        <pre data-testid="ask-sent-json">{{ sentJson }}</pre>
      </details>

      <table class="answers" data-testid="ask-answers">
        <thead><tr><th>Question</th><th>Allowed answers</th><th>Model answer</th><th>Backend check</th></tr></thead>
        <tbody>
          <tr v-for="row in answerRows" :key="row.name" :data-testid="`ask-answer-${row.name}`">
            <td><strong>{{ row.name }}</strong><div class="hint">{{ row.instructions }}</div></td>
            <td>{{ row.choices.join(', ') }}</td>
            <td><strong :data-testid="`ask-answer-${row.name}-value`">{{ row.answer ?? 'missing' }}</strong></td>
            <td :class="row.ok === false ? 'bad' : row.ok ? 'ok' : ''" :data-testid="`ask-answer-${row.name}-check`">
              {{ row.check }}</td>
          </tr>
        </tbody>
      </table>

      <template v-if="review">
        <h3>Reviewer (second, separate call)</h3>
        <p data-testid="ask-review">
          Verdict <strong data-testid="ask-review-verdict" :class="review.verdict === 'accept' ? 'ok' : 'bad'">
            {{ review.verdict ?? `none (${review.status})` }}</strong>
          · model call: <strong :class="review.call_kind === 'mock' ? 'warn' : ''" data-testid="ask-review-call-kind">
            {{ review.call_kind === 'mock' ? 'MOCK (provider test double)' : 'live' }}</strong>
          ({{ review.provider }}, {{ review.model }}<template v-if="review.latency_ms !== null">,
          {{ review.latency_ms }} ms</template>)
          <span v-if="review.error" class="bad"> · {{ review.error }}</span>
        </p>
        <p class="hint">The reviewer received the analyst's choice and named answers, the backend facts, the same
          snapshot #{{ decision.snapshot_id }}<template v-if="decision.image"> and the same PNG</template>.</p>
        <ul class="hint" data-testid="ask-review-evidence">
          <li v-for="line in review.evidence" :key="line">Observed: {{ line }}</li>
        </ul>
        <table v-if="decision.final_check" class="answers" data-testid="ask-final-check">
          <caption>Final check by Python (every gate must pass)</caption>
          <thead><tr><th>Gate</th><th>Observed</th><th>Result</th></tr></thead>
          <tbody>
            <tr v-for="g in decision.final_check.gates" :key="g.gate">
              <td>{{ g.gate }}</td><td>{{ g.observed }}</td>
              <td :class="g.passed ? 'ok' : 'bad'">{{ g.passed ? 'pass' : 'FAIL' }}</td>
            </tr>
          </tbody>
        </table>
      </template>

      <p data-testid="ask-eligibility">
        <strong v-if="decision.eligible_for_order" class="ok">Eligible for automated execution</strong>
        <template v-else>
          <strong class="bad">Not eligible for automated execution</strong>
          <span v-if="failedChecks.length">: {{ failedChecks.join('; ') }}</span>
          <span v-else-if="decision.error">: {{ decision.error }}</span>
          <span v-else-if="decision.snapshot?.kind === 'test'">: test snapshot</span>
        </template>
      </p>
      <p class="hint" data-testid="ask-explanation">{{ decision.explanation }}</p>
    </div>
  </section>
</template>

<style scoped>
.controls { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; }
.hint { font-size: 0.8rem; color: #666; }
.offscreen { position: absolute; left: -10000px; top: 0; width: 800px; }
.capture { margin: 0.75rem 0; }
.capture img { max-width: 100%; border: 1px solid #e3e6e8; border-radius: 8px; display: block; }
.capture figcaption { font-size: 0.85rem; margin-top: 0.3rem; }
.answers { margin-top: 0.5rem; }
.answers caption { text-align: left; font-size: 0.8rem; color: #666; }
.answers th, .answers td { text-align: left; vertical-align: top; }
.answers td { min-width: 6rem; }
pre { font-size: 0.75rem; max-height: 16rem; overflow: auto; background: #f6f7f8; padding: 0.6rem; border-radius: 8px; }
.notice { padding: 0.6rem 0.8rem; margin: 0.5rem 0; border-radius: 10px; }
.bad-box { background: #fdecea; border: 1px solid #f5c2c0; }
.ok { color: #1b7f5a; }
.bad { color: #c62828; }
.warn { color: #b26b00; }
</style>
