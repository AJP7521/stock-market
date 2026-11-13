<script setup>
// Candlestick chart with volume bars underneath, drawn with TradingView Lightweight Charts.
// `rows` sets the whole history; `upsert(row)` updates one candle in place (live updates).
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CandlestickSeries, HistogramSeries, LineSeries, createChart, createSeriesMarkers } from 'lightweight-charts'
import { colors, formatChartTime, toBars, toIndicatorLines, toSeriesData, toSmaData } from './chartAdapter.js'

const props = defineProps({
  rows: { type: Array, required: true }, // backend candles: timestamp, open, high, low, close, volume
  label: { type: String, default: '' },
  sma: { type: Object, default: null },  // backend SMA result: points, crossovers, windows
  indicators: { type: Array, default: () => [] },  // configured indicators with their points
  // {width, height} in CSS pixels: a fixed-size chart for capture. It is laid out synchronously, so a capture does
  // not wait for the browser to draw a frame (a hidden page may not draw any).
  fixedSize: { type: Object, default: null },
})

const container = ref(null)
const inspected = ref(null) // candle shown in the legend (hovered, or the latest)

let chart
let candleSeries
let volumeSeries
let shortSeries
let longSeries
let markers
let byTime = new Map()     // chart time -> backend row
let smaByTime = new Map()  // chart time -> SMA point
const indicatorSeries = new Map()   // indicator id -> line series
const indicatorLines = ref([])      // [{id, name, color, byTime}] for the legend
let hovering = false

function describe(time) {
  const row = byTime.get(time)
  if (!row) return null
  const values = indicatorLines.value.map((l) => ({ id: l.id, name: l.name, color: l.color, value: l.byTime.get(time) ?? null }))
  return { ...row, ...formatChartTime(time), sma: smaByTime.get(time) ?? null, indicators: values }
}

/** One line per configured indicator; "separate" ones go in a second pane below the candles. */
function applyIndicators(list) {
  const lines = toIndicatorLines(list)
  const keep = new Set(lines.map((l) => l.id))
  for (const [id, series] of indicatorSeries) {
    if (!keep.has(id)) { chart.removeSeries(series); indicatorSeries.delete(id) }
  }
  for (const line of lines) {
    let series = indicatorSeries.get(line.id)
    if (!series) {
      series = chart.addSeries(LineSeries, {
        color: line.color, lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
        crosshairMarkerVisible: false, title: line.name,
      }, line.pane === 'separate' ? 1 : 0)
      indicatorSeries.set(line.id, series)
    }
    series.setData(line.data)
  }
  indicatorLines.value = lines
  if (!hovering) showLatest()
}

/** Draw the SMA lines (gaps during warm-up) and a marker at each crossover. */
function applySma(sma) {
  const { short, long, markers: m, byTime: points } = toSmaData(sma)
  shortSeries.setData(short)
  longSeries.setData(long)
  markers.setMarkers(m)
  smaByTime = points
  if (!hovering) showLatest()
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
  const appended = !byTime.has(candle.time)
  byTime.set(candle.time, row)
  if (appended) chart.timeScale().fitContent()   // keep a newly started candle in view
  if (!hovering) showLatest()
}

// One animation frame, or 50 ms if the page draws no frames (hidden).
const nextFrame = () => new Promise((resolve) => {
  const t = setTimeout(resolve, 50)
  requestAnimationFrame(() => { clearTimeout(t); resolve() })
})

/**
 * PNG data URL of the chart canvas only (candles, volume, lines, markers; no legend or page),
 * using Lightweight Charts' own image export. Fits all candles first.
 */
async function capture() {
  chart.timeScale().fitContent()
  await nextFrame()
  await nextFrame()
  return chart.takeScreenshot(true, false).toDataURL('image/png')
}

defineExpose({ upsert, capture })

// Display only: values were compared at full precision in Python.
const fmt = (v) => (v === null || v === undefined ? '—' : Number(v.toFixed(4)).toString())

onMounted(() => {
  chart = createChart(container.value, {
    ...(props.fixedSize ? { autoSize: false, width: props.fixedSize.width, height: props.fixedSize.height }
                        : { autoSize: true }),
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

  // SMA lines share the price scale with the candles; crossovers are markers on the candles.
  const lineOptions = { lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }
  shortSeries = chart.addSeries(LineSeries, { ...lineOptions, color: colors.SHORT })
  longSeries = chart.addSeries(LineSeries, { ...lineOptions, color: colors.LONG })
  markers = createSeriesMarkers(candleSeries, [])

  chart.subscribeCrosshairMove((param) => {
    hovering = param.time !== undefined && byTime.has(param.time)
    if (hovering) inspected.value = describe(param.time)
    else showLatest()
  })
  render(props.rows)
  applySma(props.sma)
  applyIndicators(props.indicators)
})

watch(() => props.rows, (rows) => chart && render(rows))
watch(() => props.sma, (sma) => chart && applySma(sma))
watch(() => props.indicators, (list) => chart && applyIndicators(list))

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
        <template v-if="sma">
          <span class="sma-short">SMA{{ sma.short_window }}
            <b data-testid="legend-sma-short">{{ fmt(inspected.sma?.short) }}</b></span>
          <span class="sma-long">SMA{{ sma.long_window }}
            <b data-testid="legend-sma-long">{{ fmt(inspected.sma?.long) }}</b></span>
          <b v-if="inspected.sma?.signal" data-testid="legend-signal"
             :class="inspected.sma.signal">{{ inspected.sma.signal === 'up' ? '▲ cross up' : '▼ cross down' }}</b>
        </template>
        <span v-for="ind in inspected.indicators" :key="ind.id" :style="{ color: ind.color }"
              :data-testid="`legend-indicator-${ind.name}`">
          {{ ind.name }} <b>{{ fmt(ind.value) }}</b>
        </span>
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
.sma-short { color: #2962ff; }
.sma-long { color: #ff9800; }
.up { color: #1b7f5a; }
.down { color: #c62828; }
footer { font-size: 0.8rem; color: #555; margin-top: 0.4rem; }
</style>
