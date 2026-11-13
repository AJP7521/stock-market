<script setup>
// Local paper account: cash, shares and order history, with buy / sell / close / hold / wait.
// Every click sends one request to FastAPI's single execution function, with a fresh request_id.
// "Retry last request" re-sends the same request_id: the backend must return the original order.
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps({ symbol: { type: String, required: true } })

const account = ref(null)
const quantity = ref(1)
const message = ref('')
const messageKind = ref('info')
const lastRequest = ref(null)
const pnl = ref([])
let timer = null

const money = (v) => (v === null || v === undefined ? '—'
  : v.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }))
const shares = computed(() => account.value?.positions?.[props.symbol] ?? 0)

async function refresh() {
  const [res, pnlRes] = await Promise.all([
    fetch('/api/paper/account'),
    fetch(`/api/paper/pnl?${new URLSearchParams({ symbol: props.symbol })}`),
  ])
  if (res.ok) account.value = await res.json()
  if (pnlRes.ok) pnl.value = (await pnlRes.json()).rows
}

const signed = (v) => (v === null || v === undefined ? 'unavailable' : money(v))

async function send(request) {
  lastRequest.value = request
  const res = await fetch('/api/paper/orders', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request),
  })
  const body = await res.json()
  if (!res.ok) {
    messageKind.value = 'bad'
    message.value = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    return
  }
  account.value = body.account
  const o = body.order
  messageKind.value = o?.status === 'rejected' ? 'bad' : 'info'
  message.value = o ? `Order #${o.id} ${o.side} ${o.quantity} ${o.symbol}: ${body.note}` : body.note
}

function submit(action) {
  const request = { symbol: props.symbol, action, request_id: crypto.randomUUID() }
  if (action === 'buy' || action === 'sell') request.quantity = quantity.value
  send(request)
}

function retryLast() {
  if (lastRequest.value) send(lastRequest.value)
}

onMounted(() => { refresh(); timer = setInterval(refresh, 1000) })   // pending orders fill as prices arrive
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <section class="paper card" data-testid="paper-panel">
    <header>
      <strong>Paper account</strong>
      <span>Cash <b data-testid="paper-cash">{{ money(account?.cash) }}</b></span>
      <span>{{ symbol }} shares <b data-testid="paper-shares">{{ shares }}</b></span>
      <span class="hint">start {{ money(account?.starting_cash) }} · fills {{ account?.fill_count ?? 0 }} · zero fees ·
        fills at the next observed price</span>
    </header>
    <div class="controls">
      <label>Quantity <input v-model.number="quantity" type="number" step="1" min="1" data-testid="paper-qty" style="width:4.5em" /></label>
      <button type="button" data-testid="paper-buy" @click="submit('buy')">Buy</button>
      <button type="button" data-testid="paper-sell" @click="submit('sell')">Sell</button>
      <button type="button" data-testid="paper-close" @click="submit('close')">Close position</button>
      <button type="button" data-testid="paper-hold" @click="submit('hold')">Hold</button>
      <button type="button" data-testid="paper-wait" @click="submit('wait')">Wait</button>
      <button type="button" :disabled="!lastRequest" data-testid="paper-retry" @click="retryLast">Retry last request</button>
    </div>
    <p v-if="message" :class="messageKind" data-testid="paper-message">{{ message }}</p>
    <table v-if="account?.orders?.length" data-testid="paper-orders">
      <thead>
        <tr><th>Order</th><th>Submitted (UTC)</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Status</th>
          <th>Fill price</th><th>Observation</th><th>Executed (UTC)</th><th>Cash after</th><th>Reason</th></tr>
      </thead>
      <tbody>
        <tr v-for="o in account.orders" :key="o.id" :data-testid="`paper-order-${o.id}`">
          <td>#{{ o.id }}</td>
          <td>{{ o.submitted_at?.slice(11, 23) }}</td>
          <td>{{ o.symbol }}</td><td>{{ o.side }}</td><td>{{ o.quantity }}</td>
          <td :class="o.status">{{ o.status }}</td>
          <td>{{ o.fill ? money(o.fill.price) : '' }}</td>
          <td>{{ o.fill ? `#${o.fill.observation_id}` : '' }}</td>
          <td>{{ o.fill?.executed_at?.slice(11, 23) ?? '' }}</td>
          <td>{{ o.fill ? money(o.fill.cash_after) : '' }}</td>
          <td class="reason">{{ o.reason ?? '' }}</td>
        </tr>
      </tbody>
    </table>
    <p v-else class="hint">No orders yet.</p>

    <h3>Profit and loss history · {{ symbol }}</h3>
    <p class="hint">Weighted average cost, zero fees. A row is added after each fill (valued at the fill price) and
      when a completed one-minute candle gives a new price for an open position (valued at its close).</p>
    <table v-if="pnl.length" data-testid="pnl-table">
      <thead>
        <tr><th>#</th><th>Event</th><th>Time (UTC)</th><th>Price</th><th>Price source</th><th>Cash</th>
          <th>Shares</th><th>Avg cost</th><th>Realized</th><th>Unrealized</th><th>Total P&amp;L</th></tr>
      </thead>
      <tbody>
        <tr v-for="r in pnl" :key="r.id" :data-testid="`pnl-row-${r.id}`" :class="r.event">
          <td>{{ r.id }}</td>
          <td class="reason">{{ r.description }}</td>
          <td>{{ r.event_time?.slice(11, 23) }}</td>
          <td>{{ r.price === null ? 'unavailable' : money(r.price) }}</td>
          <td class="reason">{{ r.price_source ?? '—' }}</td>
          <td>{{ money(r.cash) }}</td>
          <td>{{ r.shares }}</td>
          <td>{{ r.average_cost === null ? '—' : money(r.average_cost) }}</td>
          <td>{{ money(r.realized) }}</td>
          <td :class="{ bad: r.valuation === 'unavailable' }">{{ signed(r.unrealized) }}</td>
          <td :class="{ bad: r.valuation === 'unavailable' }"><b>{{ signed(r.total) }}</b></td>
        </tr>
      </tbody>
    </table>
    <p v-else class="hint">No P&amp;L rows yet for {{ symbol }}.</p>
  </section>
</template>

<style scoped>
.paper { font-size: 0.9rem; }
header strong { font-size: 1.05rem; flex-basis: 100%; }
header b { font-size: 1.05rem; }
header, .controls { display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; align-items: center; }
.controls { margin-top: 0.4rem; }
.hint { font-size: 0.8rem; color: #666; }
.info { color: #1b5e20; }
.bad, .rejected { color: #c62828; }
.filled { color: #1b7f5a; }
.pending { color: #b26b00; }
table { margin-top: 0.5rem; }
th, td { text-align: right; padding: 5px 8px; }
td.reason { text-align: left; white-space: normal; min-width: 10rem; }
h3 { font-size: 0.95rem; margin: 0.8rem 0 0.2rem; }
tr.valuation td { background: #f7f9fc; }
</style>
