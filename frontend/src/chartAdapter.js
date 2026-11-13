// Chart adapter: converts backend candles (the six CSV fields) into the shapes
// TradingView Lightweight Charts expects. Prices and volume are not recalculated
// here; the backend supplies the numbers.

const UP = '#0f8a5f'
const DOWN = '#d8273a'
// Volume bars use the same hues, lighter, so they sit behind the candles visually.
const UP_VOLUME = 'rgba(15, 138, 95, 0.28)'
const DOWN_VOLUME = 'rgba(216, 39, 58, 0.28)'
const SHORT = '#2962ff'
const LONG = '#ff9800'

/** "2026-11-09T14:31:00Z" -> 1794234660 (UTCTimestamp: whole seconds since the epoch, UTC). */
export function toChartTime(isoUtc) {
  const ms = Date.parse(isoUtc)
  if (Number.isNaN(ms) || !isoUtc.endsWith('Z')) {
    throw new Error(`Expected a UTC ISO timestamp ending in Z, got ${isoUtc}`)
  }
  return Math.floor(ms / 1000)
}

/** UTCTimestamp -> "2026-11-09" and "14:31 UTC" for the inspection legend. */
export function formatChartTime(time) {
  const iso = new Date(time * 1000).toISOString()
  return { date: iso.slice(0, 10), time: `${iso.slice(11, 16)} UTC` }
}

/**
 * One backend candle -> { candle, volume } for the two series, sharing one time value.
 * Unknown volume (null) becomes a whitespace point: no bar is drawn, which is different from a 0 bar.
 */
export function toBars(row) {
  const time = toChartTime(row.timestamp)
  const candle = { time, open: row.open, high: row.high, low: row.low, close: row.close }
  const volume = row.volume === null || row.volume === undefined
    ? { time }
    : { time, value: row.volume, color: row.close >= row.open ? UP_VOLUME : DOWN_VOLUME }
  return { candle, volume }
}

/** Backend candles -> { candles, volumes } using the same time value for both series. */
export function toSeriesData(rows) {
  const candles = []
  const volumes = []
  for (const row of rows) {
    const bars = toBars(row)
    candles.push(bars.candle)
    volumes.push(bars.volume)
  }
  return { candles, volumes }
}

/**
 * Backend SMA result -> line data (whitespace points during warm-up or while a candle forms),
 * crossover markers, and a time -> point lookup for the legend.
 */
export function toSmaData(sma) {
  if (!sma) return { short: [], long: [], markers: [], byTime: new Map() }
  const line = (key) => sma.points.map((p) => {
    const time = toChartTime(p.timestamp)
    return p[key] === null ? { time } : { time, value: p[key] }
  })
  const markers = sma.crossovers.map((c) => ({
    time: toChartTime(c.timestamp),
    position: c.signal === 'up' ? 'belowBar' : 'aboveBar',
    shape: c.signal === 'up' ? 'arrowUp' : 'arrowDown',
    color: c.signal === 'up' ? UP : DOWN,
    text: c.signal === 'up' ? 'Cross up' : 'Cross down',
  }))
  const byTime = new Map(sma.points.map((p) => [toChartTime(p.timestamp), p]))
  return { short: line('short'), long: line('long'), markers, byTime }
}

const INDICATOR_COLORS = ['#8e24aa', '#00897b', '#6d4c41', '#d81b60', '#3949ab', '#7cb342']

/** Configured indicators from the backend -> one line per indicator (gaps where value is null). */
export function toIndicatorLines(indicators) {
  return (indicators ?? []).map((ind, i) => ({
    id: ind.id,
    name: ind.name,
    pane: ind.pane,
    color: INDICATOR_COLORS[i % INDICATOR_COLORS.length],
    data: ind.points.map((p) => {
      const time = toChartTime(p.timestamp)
      return p.value === null ? { time } : { time, value: p.value }
    }),
    byTime: new Map(ind.points.map((p) => [toChartTime(p.timestamp), p.value])),
  }))
}

export const colors = { UP, DOWN, SHORT, LONG }
