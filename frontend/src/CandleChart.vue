<script setup>
// Candlestick chart with volume bars underneath, drawn with TradingView Lightweight Charts.
// `rows` sets the whole history; `upsert(row)` updates one candle in place (live updates).
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CandlestickSeries, HistogramSeries, createChart } from 'lightweight-charts'
import { colors, formatChartTime, toBars, toSeriesData } from './chartAdapter.js'

const props = defineProps({
  rows: { type: Array, required: true }, // backend candles: timestamp, open, high, low, close, volume
  label: { type: String, default: '' },
})

const container = ref(null)
const inspected = ref(null) // candle shown in the legend (hovered, or the latest)

let chart
let candleSeries
let volumeSeries
let byTime = new Map()     // chart time -> backend row
let hovering = false

function describe(time) {
  const row = byTime.get(time)
  return row ? { ...row, ...formatChartTime(time) } : null
}

function latestTime() {
  return byTime.size ? Math.max(...byTime.keys()) : null
}

function showLatest() {
  const t = latestTime()
  inspected.value = t === null ? null : describe(t)
}

function render(rows) {
  const { candles, volumes } = toSeriesData(rows)
  byTime = new Map(candles.map((c, i) => [c.time, rows[i]]))
  candleSeries.setData(candles)
  volumeSeries.setData(volumes)
  // autoSize measures the container asynchronously; fit after it has a width.
  requestAnimationFrame(() => chart?.timeScale().fitContent())
  showLatest()
}

/** Update one candle and its volume in place, or append the next candle. */
function upsert(row) {
  const { candle, volume } = toBars(row)
  const latest = latestTime()
  const historical = latest !== null && candle.time < latest   // a late update to an earlier candle
  candleSeries.update(candle, historical)
  volumeSeries.update(volume, historical)
  byTime.set(candle.time, row)
  if (!hovering) showLatest()
}

defineExpose({ upsert })

onMounted(() => {
  chart = createChart(container.value, {
    autoSize: true,
    layout: { attributionLogo: true },
    timeScale: { timeVisible: true, secondsVisible: false },
    crosshair: { mode: 0 }, // normal: the crosshair follows the pointer
  })
  candleSeries = chart.addSeries(CandlestickSeries, {
    upColor: colors.UP, downColor: colors.DOWN, borderVisible: false,
    wickUpColor: colors.UP, wickDownColor: colors.DOWN,
  })
  candleSeries.priceScale().applyOptions({ scaleMargins: { top: 0.1, bottom: 0.3 } })

  // Volume on its own overlay scale, in the bottom 25% of the pane.
  volumeSeries = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, priceScaleId: '' })
  volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.75, bottom: 0 } })

  chart.subscribeCrosshairMove((param) => {
    hovering = param.time !== undefined && byTime.has(param.time)
    if (hovering) inspected.value = describe(param.time)
    else showLatest()
  })
  render(props.rows)
})

watch(() => props.rows, (rows) => chart && render(rows))

onBeforeUnmount(() => chart?.remove())
</script>

<template>
  <section class="chart-card">
    <header>
      <strong>{{ label }}</strong>
      <span v-if="inspected" class="legend" data-testid="candle-legend">
        <span data-testid="legend-date">{{ inspected.date }}</span>
        <span data-testid="legend-time">{{ inspected.time }}</span>
        O <b data-testid="legend-open">{{ inspected.open }}</b>
        H <b data-testid="legend-high">{{ inspected.high }}</b>
        L <b data-testid="legend-low">{{ inspected.low }}</b>
        C <b data-testid="legend-close">{{ inspected.close }}</b>
        Vol <b data-testid="legend-volume">{{ inspected.volume ?? 'unknown' }}</b>
        <span v-if="inspected.volume !== null && inspected.volume !== undefined">shares</span>
      </span>
    </header>
    <div ref="container" class="chart" data-testid="candle-chart"></div>
    <footer>
      Charts by
      <a href="https://www.tradingview.com/" target="_blank" rel="noopener">TradingView</a>
      Lightweight Charts™ (Apache-2.0) ·
      <a href="https://tradingview.github.io/lightweight-charts/docs" target="_blank" rel="noopener">documentation</a>
    </footer>
  </section>
</template>

<style scoped>
.chart-card { max-width: 900px; font-family: system-ui, sans-serif; }
header { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: baseline; margin-bottom: 0.5rem; }
.legend { display: flex; flex-wrap: wrap; gap: 0.4rem; font-variant-numeric: tabular-nums; }
.chart { height: 400px; border: 1px solid #ddd; }
footer { font-size: 0.8rem; color: #555; margin-top: 0.4rem; }
</style>
