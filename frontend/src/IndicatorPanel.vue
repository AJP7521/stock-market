<script setup>
// Create, list and remove named indicator instances for one symbol.
// Types and their settings come from FastAPI; Python validates and stores them in SQLite.
import { computed, onMounted, ref, watch } from 'vue'

const props = defineProps({ symbol: { type: String, required: true } })
const emit = defineEmits(['changed'])

const types = ref([])
const configs = ref([])
const typeKey = ref('')
const name = ref('')
const settings = ref({})
const error = ref('')
const busy = ref(false)

const selectedType = computed(() => types.value.find((t) => t.key === typeKey.value))

function resetSettings() {
  settings.value = Object.fromEntries((selectedType.value?.settings ?? []).map((s) => [s.name, s.default]))
}

async function loadTypes() {
  const res = await fetch('/api/indicator-types')
  types.value = (await res.json()).types
  if (!typeKey.value && types.value.length) {
    typeKey.value = types.value[0].key
    resetSettings()
  }
}

async function loadConfigs() {
  const res = await fetch(`/api/indicator-configs?${new URLSearchParams({ symbol: props.symbol })}`)
  configs.value = (await res.json()).indicators
}

function problem(body) {
  return typeof body.detail === 'string' ? body.detail
    : (body.detail ?? []).map((d) => `${d.loc?.at(-1)}: ${d.msg}`).join('; ')
}

async function add() {
  error.value = ''
  busy.value = true
  try {
    // Send the number as typed; Python decides whether it is valid.
    const typed = Object.fromEntries(Object.entries(settings.value).map(([k, v]) => [k, v === '' ? null : v]))
    const res = await fetch('/api/indicator-configs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol: props.symbol, name: name.value, type: typeKey.value, settings: typed }),
    })
    const body = await res.json()
    if (!res.ok) {
      error.value = `Not added: ${problem(body)}`   // existing indicators stay as they are
      return
    }
    name.value = ''
    await loadConfigs()
    emit('changed')
  } finally {
    busy.value = false
  }
}

async function remove(cfg) {
  await fetch(`/api/indicator-configs/${cfg.id}`, { method: 'DELETE' })
  await loadConfigs()
  emit('changed')
}

const describe = (cfg) => Object.entries(cfg.settings).map(([k, v]) => `${k} ${v}`).join(', ')
const typeLabel = (key) => types.value.find((t) => t.key === key)?.label ?? key

watch(() => props.symbol, () => { error.value = ''; loadConfigs() })
onMounted(async () => { await loadTypes(); await loadConfigs() })
</script>

<template>
  <section class="indicators" data-testid="indicator-panel">
    <form class="add" data-testid="indicator-form" @submit.prevent="add">
      <strong>Indicators for {{ symbol }}</strong>
      <label>
        Type
        <select v-model="typeKey" data-testid="indicator-type" @change="resetSettings">
          <option v-for="t in types" :key="t.key" :value="t.key">{{ t.label }}</option>
        </select>
      </label>
      <label>Name <input v-model="name" data-testid="indicator-name" size="12" placeholder="e.g. Range 2" /></label>
      <label v-for="s in selectedType?.settings ?? []" :key="s.name" :title="s.description">
        {{ s.label }}
        <input v-model.number="settings[s.name]" type="number" step="1" :data-testid="`indicator-setting-${s.name}`"
               style="width:4.5em" />
      </label>
      <button type="submit" :disabled="busy" data-testid="indicator-add">Add indicator</button>
    </form>
    <p v-if="selectedType" class="hint">
      {{ selectedType.description }} Units: {{ selectedType.units }}. Output: {{ selectedType.output }}.
    </p>
    <p v-if="error" class="bad" data-testid="indicator-error">{{ error }}</p>
    <ul data-testid="indicator-list">
      <li v-for="cfg in configs" :key="cfg.id" :data-testid="`indicator-item-${cfg.name}`">
        <b>{{ cfg.name }}</b> · {{ typeLabel(cfg.type) }} ({{ describe(cfg) }})
        <button type="button" @click="remove(cfg)">Remove</button>
      </li>
      <li v-if="!configs.length" class="hint">No indicators configured for {{ symbol }}.</li>
    </ul>
  </section>
</template>

<style scoped>
.indicators { margin: 0.5rem 0; padding: 0.5rem 0.75rem; border: 1px solid #ddd; max-width: 880px; }
.add { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; }
.hint { font-size: 0.8rem; color: #666; margin: 0.3rem 0; }
.bad { color: #c62828; margin: 0.3rem 0; }
ul { margin: 0.3rem 0 0; padding-left: 1.2rem; }
li button { margin-left: 0.5rem; font-size: 0.8rem; }
</style>
