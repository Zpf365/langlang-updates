function positiveSeconds(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return 0;
  return seconds > 10_000_000_000 ? Math.floor(seconds / 1000) : Math.floor(seconds);
}

export function createOrderCountdownClock({
  monotonicNow = () => performance.now() / 1000,
  wallNow = () => Date.now() / 1000,
  setIntervalFn = setInterval,
  clearIntervalFn = clearInterval,
  intervalMs = 1000,
} = {}) {
  let timer = null;
  let listener = null;
  let serverAnchor = 0;
  let monotonicAnchor = 0;

  function nowSeconds() {
    if (!serverAnchor) return Math.floor(wallNow());
    return Math.floor(
      serverAnchor + Math.max(0, monotonicNow() - monotonicAnchor),
    );
  }

  function publish() {
    listener?.(nowSeconds());
  }

  function setServerTime(value) {
    const seconds = positiveSeconds(value);
    if (!seconds) return false;
    serverAnchor = seconds;
    monotonicAnchor = monotonicNow();
    publish();
    return true;
  }

  function start() {
    if (timer !== null) return false;
    publish();
    timer = setIntervalFn(publish, intervalMs);
    return true;
  }

  function stop() {
    if (timer === null) return false;
    clearIntervalFn(timer);
    timer = null;
    return true;
  }

  function subscribe(callback) {
    listener = typeof callback === "function" ? callback : null;
    publish();
  }

  return {
    nowSeconds,
    setServerTime,
    start,
    stop,
    subscribe,
  };
}
