export function beginOrderRefresh(activeIds, orderId) {
  if (activeIds.has(orderId)) {
    return { activeIds, started: false };
  }
  return {
    activeIds: new Set([...activeIds, orderId]),
    started: true,
  };
}

export function finishOrderRefresh(activeIds, orderId) {
  const next = new Set(activeIds);
  next.delete(orderId);
  return next;
}
