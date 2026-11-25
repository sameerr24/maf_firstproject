function isSuccess(response) {
  return Boolean(response) &&
    String(response.type ?? response.status).toLowerCase() === "success";
}

export function responseMessage(response) {
  if (typeof response === "string" && response.trim()) return response.trim();
  if (!isSuccess(response)) return null;
  const value = response.message ?? response.data?.message ?? response.data;
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function responseRequestId(response) {
  if (typeof response === "string" && response.trim()) return response.trim();
  if (!isSuccess(response)) return null;
  const value = response.request_id ?? response.requestId ?? response.reqId ??
    response.data?.request_id ?? response.data?.requestId ?? response.data?.reqId ??
    response.message;
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function responseErrorMessage(response) {
  if (typeof response === "string" && response.trim()) return response.trim();
  if (!response || isSuccess(response)) return null;
  if (response instanceof Error && response.message) return response.message;
  const candidates = [
    response.message,
    response.error?.message,
    response.error,
    response.errors?.message,
    response.errors,
    response.msg,
  ];
  const message = candidates.find((candidate) =>
    typeof candidate === "string" && candidate.trim());
  return message?.trim() ?? null;
}
