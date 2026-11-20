// Shared handling for "the backend is not reachable" (Prompt 16B).
//
// backendFetch: like fetch, but throws BackendUnreachable when the request never reaches FastAPI: a network failure
// (connection refused), or the Vite dev proxy's 502/503/504 when the backend is stopped. Other HTTP errors are
// returned as usual, so callers keep their own handling of 4xx and 500 replies.
//
// createPoller: runs a task now and again after each run (never two at once). After a failure it waits twice as long
// as before, up to maxMs, so a stopped backend is asked rarely; after a success it returns to the normal interval.
// `offline` is true while the last run failed, for an on-screen notice. No error is left uncaught.
import { ref } from 'vue'

export class BackendUnreachable extends Error {}

export async function backendFetch(url, options) {
  let res
  try {
    res = await fetch(url, options)
  } catch (e) {
    throw new BackendUnreachable(`backend unreachable (${e.message})`)
  }
  if (res.status === 502 || res.status === 503 || res.status === 504) {
    throw new BackendUnreachable(`backend unreachable (HTTP ${res.status} from the dev proxy)`)
  }
  return res
}

export function createPoller(task, intervalMs, maxMs = 30000) {
  const offline = ref(false)
  let delay = intervalMs
  let timer = null
  let running = false
  let active = false

  async function run() {
    timer = null
    if (running) return
    running = true
    try {
      await task()
      offline.value = false
      delay = intervalMs
    } catch (e) {
      if (!(e instanceof BackendUnreachable)) console.warn('poll failed:', e)
      offline.value = true
      delay = Math.min(delay * 2, maxMs)
    } finally {
      running = false
      if (active) timer = setTimeout(run, delay)
    }
  }

  return {
    offline,
    start() { active = true; if (!timer && !running) run() },
    stop() { active = false; clearTimeout(timer); timer = null },
    /** Run again now (e.g. after the settings changed), keeping the schedule. */
    now() { if (!active) return; clearTimeout(timer); timer = null; run() },
  }
}
