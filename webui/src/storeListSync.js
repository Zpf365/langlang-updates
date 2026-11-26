export function startRepeatingSync({
  synchronize,
  intervalMs = 1200,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
  onError = () => {},
}) {
  let stopped = false;
  let timer = null;
  let running = false;
  const accountHandoffHandler = () => stop();

  function stop() {
    stopped = true;
    if (timer !== null) {
      clearTimer(timer);
      timer = null;
    }
    if (typeof window !== "undefined") {
      window.removeEventListener(
        "langlang-account-handoff",
        accountHandoffHandler,
      );
    }
  }

  async function tick() {
    if (stopped) return;
    if (!running) {
      running = true;
      try {
        await synchronize();
      } catch (error) {
        onError(error);
      } finally {
        running = false;
      }
    }
    if (!stopped) {
      timer = setTimer(tick, intervalMs);
    }
  }

  if (typeof window !== "undefined") {
    window.addEventListener(
      "langlang-account-handoff",
      accountHandoffHandler,
    );
  }
  void tick();
  return stop;
}
