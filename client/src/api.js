// Thin fetch wrapper: JSON in/out. `{ error, code, hint }` bodies become exceptions that keep code and hint.
async function request(method, path, { params, body } = {}) {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  }
  let response;
  try {
    response = await fetch(url, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (cause) {
    const error = new Error(cause && cause.message ? cause.message : "Network error");
    error.code = "network";
    throw error;
  }
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { error: text.slice(0, 300) };
  }
  if (!response.ok) {
    const error = new Error((data && data.error) || `Error ${response.status}`);
    error.code = data && data.code;
    error.hint = data && data.hint;
    error.status = response.status;
    throw error;
  }
  return data;
}

// Every write and most reads go through the same tool handlers the assistant uses.
const call = (name, args) => request("POST", "/api/ui/call", { body: { name, arguments: args || {} } });

export const api = {
  health: () => request("GET", "/api/health"),
  status: () => request("GET", "/api/status"),
  dashboard: () => request("GET", "/api/dashboard"),
  visit: () => request("POST", "/api/dashboard/visit", { body: {} }),
  history: (id, limit = 300) => request("GET", `/api/targets/${encodeURIComponent(id)}/history`, { params: { limit } }),
  call,
};

// Message for a toast: the backend's error plus its hint.
export function errorText(error) {
  if (!error) return "";
  const base = error.message || String(error);
  return error.hint ? `${base} — ${error.hint}` : base;
}
