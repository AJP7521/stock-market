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
let timer = null

const money = (v) => (v === null || v === undefined ? '—'
  : v.toLocaleString('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }))
const shares = computed(() => account.value?.positions?.[props.symbol] ?? 0)

async function refresh() {
  const res = await fetch('/api/paper/account')
  if (res.ok) account.value = await res.json()
}

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
  <section class="paper" data-testid="paper-panel">
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
  </section>
</template>

<style scoped>
.paper { margin: 0.75rem 0; padding: 0.5rem 0.75rem; border: 1px solid #ddd; max-width: 880px; font-size: 0.9rem; }
header, .controls { display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; align-items: center; }
.controls { margin-top: 0.4rem; }
.hint { font-size: 0.8rem; color: #666; }
.info { color: #1b5e20; }
.bad, .rejected { color: #c62828; }
.filled { color: #1b7f5a; }
.pending { color: #b26b00; }
table { border-collapse: collapse; margin-top: 0.5rem; font-variant-numeric: tabular-nums; font-size: 0.8rem; }
th, td { border: 1px solid #ddd; padding: 0.15rem 0.4rem; text-align: right; }
td.reason { text-align: left; max-width: 16rem; }
</style>
