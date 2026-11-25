<script setup>
// Trading assistant (Prompt 18): a side panel chat. Ask anything; questions about what to do get a recommendation for
// the PAPER account (buy, sell, close, hold or wait) that Python checks against the account. Nothing trades from the
// chat: "Place this paper order" sends the order only when the student clicks it, through the normal paper execution
// (Python re-checks first, and a second click returns the same order).
import { nextTick, ref, watch } from 'vue'
import { backendFetch } from './backend.js'
import { formatReply } from './chatText.js'

const props = defineProps({
  symbol: { type: String, required: true },
  timeframe: { type: String, required: true },
  smaShort: { type: Number, required: true },
  smaLong: { type: Number, required: true },
})
defineEmits(['close'])

const session = ref(null)
const draft = ref('')
const busy = ref(false)
const error = ref('')
const placing = ref({})            // turn id -> message after Place
const log = ref(null)

const SUGGESTIONS = ['Should I buy or sell now?', 'Explain the SMA crossover on this chart',
                     'How did the crossover rule do in a backtest?', 'What does unrealized P&L mean?']

async function post(url, body) {
  const res = await backendFetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                        body: body === undefined ? undefined : JSON.stringify(body) })
  const data = await res.json()
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail))
  return data
}

function sameContext() {
  const s = session.value
  return s && s.symbol === props.symbol && s.timeframe === props.timeframe && s.short === props.smaShort
    && s.long === props.smaLong
}

async function send(text) {
  const question = (text ?? draft.value).trim()
  if (!question || busy.value) return
  busy.value = true
  error.value = ''
  try {
    if (!sameContext()) {
      session.value = await post('/api/assistant/sessions', { symbol: props.symbol, timeframe: props.timeframe,
                                                             short: props.smaShort, long: props.smaLong })
    }
    session.value = { ...session.value, turns: [...session.value.turns, { id: 'pending', role: 'user', text: question }] }
    draft.value = ''
    scroll()
    const out = await post(`/api/assistant/sessions/${session.value.id}/turns`, { text: question })
    session.value = out.session
  } catch (e) {
    error.value = e.message
    if (session.value) session.value = { ...session.value, turns: session.value.turns.filter((t) => t.id !== 'pending') }
  } finally {
    busy.value = false
    scroll()
  }
}

async function place(turn) {
  placing.value = { ...placing.value, [turn.id]: 'Placing…' }
  try {
    const out = await post(`/api/assistant/turns/${turn.id}/place`)
    const o = out.order
    placing.value = { ...placing.value, [turn.id]: `${out.duplicate ? 'Already placed: ' : ''}order #${o.id} ${o.side} ${o.quantity} ${o.symbol}: ${o.status}${o.reason ? ` (${o.reason})` : ''}` }
    const res = await backendFetch(`/api/assistant/sessions/${session.value.id}`)
    if (res.ok) session.value = await res.json()
  } catch (e) {
    placing.value = { ...placing.value, [turn.id]: `Not placed: ${e.message}` }
  }
}

function newChat() { session.value = null; placing.value = {}; error.value = '' }
async function scroll() { await nextTick(); if (log.value) log.value.scrollTop = log.value.scrollHeight }

watch(() => [props.symbol, props.timeframe], () => newChat())
</script>

<template>
  <aside class="assistant" data-testid="assistant-panel" aria-label="Trading assistant">
    <header>
      <div>
        <strong>Assistant</strong>
        <span class="tag">SIMULATED · paper account</span>
      </div>
      <div class="head-actions">
        <button type="button" class="link" data-testid="assistant-new" @click="newChat">New chat</button>
        <button type="button" class="close" aria-label="Close assistant" data-testid="assistant-close"
                @click="$emit('close')">×</button>
      </div>
    </header>
    <p class="context">{{ symbol }} · {{ timeframe }} · SMA {{ smaShort }}/{{ smaLong }} — ask anything. Answers about
      this chart use the app's own data; nothing trades unless you click <b>Place this paper order</b>.</p>

    <div ref="log" class="log" data-testid="assistant-log">
      <div v-if="!session?.turns?.length" class="empty">
        <p>Try:</p>
        <button v-for="s in SUGGESTIONS" :key="s" type="button" class="suggestion" @click="send(s)">{{ s }}</button>
      </div>
      <template v-for="t in session?.turns ?? []" :key="t.id">
        <div v-if="t.role === 'user'" class="msg user">{{ t.text }}</div>
        <div v-else class="msg bot" :data-testid="`assistant-answer-${t.id}`">
          <div class="reply" data-testid="assistant-reply" v-html="formatReply(t.text)"></div>
          <p v-if="t.status && t.status !== 'ok'" class="bad">({{ t.status.replace('_', ' ') }})</p>

          <div v-if="t.recommendation" class="rec" data-testid="assistant-rec">
            <div class="rec-head">
              <span class="action" :class="t.recommendation.action" data-testid="assistant-rec-action">
                {{ t.recommendation.action.toUpperCase() + (t.python_check?.order?.quantity ? ` ${t.python_check.order.quantity}` : '') }}</span>
              <span class="conf">confidence {{ Math.round(t.recommendation.confidence * 100) }}% <small>(model's own
                estimate)</small></span>
            </div>
            <p v-if="t.python_check?.test_data" class="warn">Based on TEST data, not a market record.</p>
            <h4>Why</h4>
            <ul><li v-for="(r, i) in t.recommendation.reasons" :key="i"><b>{{ r.point }}</b> — {{ r.evidence }}</li></ul>
            <h4>Case against</h4>
            <ul><li v-for="(r, i) in t.recommendation.case_against" :key="i">{{ r }}</li></ul>
            <h4>Would change it</h4>
            <ul><li v-for="(r, i) in t.recommendation.would_change" :key="i">{{ r }}</li></ul>

            <details class="check" :open="t.python_check?.places_order && !t.python_check?.actionable">
              <summary data-testid="assistant-check">Python check:
                <b :class="t.python_check?.actionable ? 'ok' : t.python_check?.places_order ? 'bad' : 'muted'">
                  {{ t.python_check?.actionable ? 'can be placed' : t.python_check?.places_order ? 'cannot be placed now' : 'no order needed' }}</b></summary>
              <ul><li v-for="g in t.python_check?.gates ?? []" :key="g.gate" :class="g.passed ? 'ok' : 'bad'">
                {{ g.passed ? '✓' : '✗' }} {{ g.gate }} <small>({{ g.detail }})</small></li></ul>
            </details>

            <button v-if="t.python_check?.places_order" type="button" class="place"
                    :disabled="!t.python_check.actionable && !t.order_id" data-testid="assistant-place"
                    @click="place(t)">Place this paper order</button>
            <p v-if="placing[t.id]" class="placed" data-testid="assistant-place-result">{{ placing[t.id] }}</p>
            <p v-else-if="t.order_id" class="placed">Placed as paper order #{{ t.order_id }}.</p>
          </div>

          <details v-if="t.tool_calls?.length" class="tools">
            <summary>Data used ({{ t.tool_calls.length }} tool call{{ t.tool_calls.length > 1 ? 's' : '' }})</summary>
            <div v-for="(c, i) in t.tool_calls" :key="i" class="tool">
              <b>{{ c.name }}</b>
              <pre>{{ JSON.stringify(c.result, null, 1).slice(0, 2000) }}</pre>
            </div>
          </details>
          <small class="meta">{{ t.model }}{{ t.latency_ms != null ? ` · ${(t.latency_ms / 1000).toFixed(1)} s` : '' }}</small>
        </div>
      </template>
      <div v-if="busy" class="msg bot thinking" data-testid="assistant-busy">Thinking…</div>
    </div>

    <p v-if="error" class="bad err" data-testid="assistant-error">{{ error }}</p>
    <form class="compose" @submit.prevent="send()">
      <textarea v-model="draft" rows="2" placeholder="Ask anything…" data-testid="assistant-input"
                @keydown.enter.exact.prevent="send()"></textarea>
      <button type="submit" :disabled="busy || !draft.trim()" data-testid="assistant-send">Send</button>
    </form>
    <p class="disclaimer">Simulated paper trading for a course. Not financial advice.</p>
  </aside>
</template>

<style scoped>
.assistant {
  position: fixed; top: 0; right: 0; bottom: 0; z-index: 30; width: min(420px, 100vw);
  display: flex; flex-direction: column; background: var(--surface); border-left: 1px solid var(--border);
  box-shadow: -8px 0 24px rgba(0, 0, 0, 0.08); font-size: 0.9rem;
}
header { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px;
         border-bottom: 1px solid var(--border); }
header { gap: 8px; }
header strong { font-size: 1.05rem; margin-right: 8px; }
.head-actions { display: flex; gap: 8px; align-items: center; white-space: nowrap; }
.close { border: none; background: none; font-size: 1.5rem; line-height: 1; cursor: pointer; color: var(--text-muted); }
.link { border: none; background: none; color: var(--accent); cursor: pointer; padding: 0; }
.tag { white-space: nowrap; font-size: 0.7rem; font-weight: 700; letter-spacing: 0.04em; padding: 2px 6px; border-radius: 4px;
       background: #fff4e0; color: var(--warn); }
.context { margin: 0; padding: 8px 16px; color: var(--text-muted); font-size: 0.8rem; border-bottom: 1px solid var(--border); }
.log { flex: 1; overflow-y: auto; padding: 12px 16px; display: flex; flex-direction: column; gap: 10px; }
.empty p { margin: 0 0 6px; color: var(--text-muted); }
.suggestion { display: block; width: 100%; text-align: left; margin: 4px 0; padding: 8px 10px; border-radius: 8px;
              border: 1px solid var(--border); background: var(--bg); cursor: pointer; }
.msg { padding: 8px 12px; border-radius: 12px; max-width: 92%; white-space: pre-wrap; overflow-wrap: anywhere; }
.msg.user { align-self: flex-end; background: var(--accent); color: #fff; }
.msg.bot { align-self: flex-start; background: var(--bg); border: 1px solid var(--border); }
.msg.bot { white-space: normal; }
.reply :deep(p) { margin: 0 0 6px; }
.reply :deep(p:last-child) { margin-bottom: 0; }
.reply :deep(ul) { margin: 0 0 6px; }
.thinking { color: var(--text-muted); font-style: italic; }
.rec { margin-top: 8px; padding: 8px 10px; background: var(--surface); border: 1px solid var(--border-strong);
       border-radius: 8px; white-space: normal; }
.rec-head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.action { font-weight: 800; font-size: 1.05rem; }
.action.buy { color: var(--up); }
.action.sell, .action.close { color: var(--down); }
.action.hold, .action.wait { color: var(--text-muted); }
.conf { font-size: 0.8rem; }
h4 { margin: 8px 0 2px; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-muted); }
ul { margin: 0; padding-left: 1.1rem; }
li { margin: 2px 0; }
.check { margin-top: 8px; }
.check ul { list-style: none; padding-left: 0.2rem; }
.place { margin-top: 8px; width: 100%; padding: 8px; border-radius: 8px; border: none; background: var(--accent);
         color: #fff; font-weight: 600; cursor: pointer; }
.place:disabled { background: var(--border-strong); cursor: not-allowed; }
.placed { margin: 6px 0 0; font-size: 0.8rem; }
.tools { margin-top: 6px; font-size: 0.75rem; white-space: normal; }
.tool pre { max-height: 160px; overflow: auto; background: var(--surface); padding: 6px; border-radius: 6px; }
.meta { display: block; margin-top: 4px; color: var(--text-muted); font-size: 0.7rem; }
.compose { display: flex; gap: 8px; padding: 10px 16px; border-top: 1px solid var(--border); }
.compose textarea { flex: 1; resize: none; font: inherit; padding: 8px; border-radius: 8px; border: 1px solid var(--border-strong); }
.compose button { padding: 0 14px; border-radius: 8px; border: none; background: var(--accent); color: #fff; font-weight: 600; }
.compose button:disabled { background: var(--border-strong); }
.err { margin: 0; padding: 0 16px; }
.disclaimer { margin: 0; padding: 0 16px 10px; font-size: 0.7rem; color: var(--text-muted); }
.ok { color: var(--up); }
.bad { color: var(--down); }
.warn { color: var(--warn); margin: 4px 0; }
.muted { color: var(--text-muted); }
</style>
