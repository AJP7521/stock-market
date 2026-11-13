<script setup>
// Voice conversation about one recorded decision (Prompt 13).
// Claude has no audio API, so speech stays in the browser: getUserMedia opens the microphone (and shows that it is
// active), the Web Speech API's SpeechRecognition turns each spoken question into text, FastAPI asks Claude with a
// read-only summary of the selected decision, and speechSynthesis reads the reply aloud. Follow-ups use the same
// session. End voice, a denied microphone or any failure stops recognition and speech, stops every microphone
// track (releasing the microphone) and ends the session.
// Note: in Chrome, SpeechRecognition sends the audio to Google's speech service.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const decisions = ref([])
const selectedId = ref(null)
const session = ref(null)            // {session_id, record, call_kind, model, ...}
const turns = ref([])                // [{role, text, meta}]
const phase = ref('idle')            // idle | starting | listening | thinking | speaking
const heard = ref('')                // interim recognition text
const error = ref('')
const micLive = ref(false)
const micReleased = ref(null)        // true after End once every track reports 'ended'
const typed = ref('')

let stream = null
let recognition = null
let active = false

const recognitionClass = () => window.SpeechRecognition || window.webkitSpeechRecognition || null
const isSupported = () => !!recognitionClass() && 'speechSynthesis' in window && !!navigator.mediaDevices?.getUserMedia
const supported = ref(isSupported())
const selected = computed(() => decisions.value.find((d) => d.decision_id === selectedId.value) ?? null)
const utc = (iso) => (iso ? iso.replace('T', ' ').replace(/:00(\.000)?Z$/, ' UTC').replace(/Z$/, ' UTC') : '—')

async function loadDecisions() {
  const res = await fetch('/api/decisions?limit=30')
  if (!res.ok) return
  decisions.value = (await res.json()).decisions
  if (!decisions.value.some((d) => d.decision_id === selectedId.value)) selectedId.value = decisions.value[0]?.decision_id ?? null
}

async function post(url, body) {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `HTTP ${res.status}`)
  return data
}

async function start() {
  error.value = ''
  turns.value = []
  micReleased.value = null
  supported.value = isSupported()
  if (!supported.value) {
    error.value = 'This browser has no speech recognition or speech synthesis. Use Google Chrome on localhost.'
    return
  }
  phase.value = 'starting'
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    micLive.value = stream.getAudioTracks().some((t) => t.readyState === 'live')
  } catch (e) {
    await stop(`microphone unavailable: ${e.name}`)
    error.value = e.name === 'NotAllowedError'
      ? 'Microphone access was denied. Allow the microphone for this page and press Start voice again.'
      : `Could not open the microphone: ${e.name} ${e.message}`
    return
  }
  try {
    session.value = await post('/api/voice/sessions', { decision_id: selectedId.value })
  } catch (e) {
    await stop('connection failed')
    error.value = `Could not start the voice session (backend unreachable or rejected): ${e.message}`
    return
  }
  active = true
  const Recognition = recognitionClass()
  recognition = new Recognition()
  recognition.lang = 'en-US'
  recognition.interimResults = true
  recognition.continuous = false
  recognition.onresult = (event) => {
    const result = event.results[event.results.length - 1]
    heard.value = result[0].transcript
    if (result.isFinal) ask(result[0].transcript)
  }
  recognition.onerror = (event) => {
    if (['not-allowed', 'service-not-allowed', 'audio-capture', 'network'].includes(event.error)) {
      stop(`speech recognition error: ${event.error}`)
      error.value = event.error === 'network'
        ? 'The speech recognition service could not be reached; the session was ended.'
        : `Speech recognition stopped (${event.error}); the session was ended.`
    }
  }
  recognition.onend = () => { if (active && phase.value === 'listening') listen() }
  listen()
}

function listen() {
  if (!active) return
  heard.value = ''
  phase.value = 'listening'
  try { recognition.start() } catch { /* already started */ }
}

async function ask(text) {
  if (!active || !text.trim()) return
  phase.value = 'thinking'
  try { recognition.stop() } catch { /* not running */ }
  turns.value.push({ role: 'user', text })
  let reply
  try {
    reply = await post(`/api/voice/sessions/${session.value.session_id}/turns`, { text })
  } catch (e) {
    await stop('connection failed')
    error.value = `The question could not be sent; the session was ended: ${e.message}`
    return
  }
  if (!active) return
  const meta = `${reply.call_kind === 'mock' ? 'MOCK test double' : 'live'} · ${reply.model} · ${reply.latency_ms} ms`
  const spoken = reply.status === 'ok' ? reply.reply : `Sorry, no answer was available (${reply.status}).`
  turns.value.push({ role: 'assistant', text: spoken, meta })
  speak(spoken)
}

function speak(text) {
  phase.value = 'speaking'
  const u = new SpeechSynthesisUtterance(text)
  u.lang = 'en-US'
  u.onend = () => { if (active) listen() }
  u.onerror = () => { if (active) listen() }
  window.speechSynthesis.speak(u)
}

function askTyped() {
  const text = typed.value
  typed.value = ''
  ask(text)
}

/** End voice, denied microphone or failure: stop everything and release the microphone. */
async function stop(reason = 'ended by the student') {
  active = false
  try { recognition?.abort() } catch { /* ignore */ }
  recognition = null
  if ('speechSynthesis' in window) window.speechSynthesis.cancel()
  if (stream) {
    stream.getTracks().forEach((t) => t.stop())
    micReleased.value = stream.getTracks().every((t) => t.readyState === 'ended')
    stream = null
  }
  micLive.value = false
  phase.value = 'idle'
  heard.value = ''
  if (session.value && !session.value.ended) {
    session.value.ended = true
    await post(`/api/voice/sessions/${session.value.session_id}/end`, { reason }).catch(() => {})
  }
}

onMounted(loadDecisions)
onBeforeUnmount(() => stop('page closed'))
</script>

<template>
  <section class="voice" data-testid="voice">
    <h2>Talk about a decision (voice)</h2>
    <div class="controls">
      <label>Decision
        <select v-model="selectedId" :disabled="phase !== 'idle'" data-testid="voice-decision">
          <option v-for="d in decisions" :key="d.decision_id" :value="d.decision_id">
            #{{ d.decision_id }} · {{ d.symbol }} {{ utc(d.candle) }} · {{ d.proposal ?? 'no proposal' }}
            · {{ d.verdict ?? 'no review' }} · {{ d.outcome }}
          </option>
        </select>
      </label>
      <button type="button" :disabled="phase !== 'idle'" @click="loadDecisions">Refresh</button>
      <button type="button" :disabled="phase !== 'idle' || !selectedId" data-testid="voice-start" @click="start">Start voice</button>
      <button type="button" :disabled="phase === 'idle'" data-testid="voice-end" @click="stop()">End voice</button>
      <strong v-if="micLive" class="mic" data-testid="voice-mic">● Microphone active</strong>
      <span v-else class="hint" data-testid="voice-mic-off">Microphone off<template v-if="micReleased"> (released)</template></span>
      <span class="hint" data-testid="voice-phase">{{ phase }}</span>
    </div>
    <p v-if="selected" class="hint" data-testid="voice-selected">
      Selected: decision #{{ selected.decision_id }} · {{ selected.symbol }} · candle {{ utc(selected.candle) }} ·
      crossover {{ selected.crossover }} · analyst {{ selected.proposal ?? 'none' }} · reviewer
      {{ selected.verdict ?? 'none' }} · eligible {{ selected.eligible ? 'yes' : 'no' }} · outcome {{ selected.outcome }}
    </p>
    <p class="hint">Speech recognition and speech output run in the browser (Chrome sends the audio to Google's
      speech service); the answers come from Claude, which only reads this decision's record.</p>
    <p v-if="!supported" class="warn" data-testid="voice-unsupported">This browser has no speech recognition; use
      Google Chrome.</p>
    <p v-if="error" class="bad" data-testid="voice-error">{{ error }}</p>
    <p v-if="heard && phase === 'listening'" class="hint" data-testid="voice-heard">Hearing: “{{ heard }}”</p>

    <ol v-if="turns.length" class="turns" data-testid="voice-turns">
      <li v-for="(t, i) in turns" :key="i" :class="t.role">
        <strong>{{ t.role === 'user' ? 'You' : 'Assistant' }}:</strong> {{ t.text }}
        <span v-if="t.meta" class="hint">({{ t.meta }})</span>
      </li>
    </ol>
    <form v-if="phase !== 'idle' && phase !== 'starting'" class="controls" @submit.prevent="askTyped">
      <input v-model="typed" size="50" placeholder="…or type a question" data-testid="voice-typed" />
      <button type="submit">Ask</button>
    </form>
    <details v-if="session">
      <summary>Read-only record given to the model (session #{{ session.session_id }})</summary>
      <pre data-testid="voice-record">{{ session.summary_text }}</pre>
    </details>
  </section>
</template>

<style scoped>
.voice { max-width: 1100px; margin: 1rem 0; padding: 0.75rem; border: 1px solid #ddd; font-family: system-ui, sans-serif; }
h2 { font-size: 1.1rem; margin: 0 0 0.5rem; }
.controls { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; margin: 0.25rem 0; }
.hint { font-size: 0.8rem; color: #666; }
.mic { color: #c62828; }
.turns { font-size: 0.9rem; }
.turns li { margin: 0.3rem 0; }
pre { font-size: 0.75rem; white-space: pre-wrap; background: #f7f7f7; padding: 0.5rem; }
.bad { color: #c62828; }
.warn { color: #b26b00; }
</style>
