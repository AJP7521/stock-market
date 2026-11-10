// Chart adapter: converts backend candles (the six CSV fields) into the shapes
// TradingView Lightweight Charts expects. Prices and volume are not recalculated
// here; the backend supplies the numbers.

const UP = '#26a69a'
const DOWN = '#ef5350'

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
    : { time, value: row.volume, color: row.close >= row.open ? UP : DOWN }
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

export const colors = { UP, DOWN }
