// Minimal API client: API-key auth, JSON, uniform error objects.

export class ApiError extends Error {
  constructor(status, body) {
    const err = (body && body.error) || {};
    super(err.message || `HTTP ${status}`);
    this.status = status;
    this.code = err.code || 'http_error';
    this.details = err.details || {};
  }
}

export class Api {
  constructor(key) {
    this.key = key;
  }

  async request(method, path, body, extraHeaders = {}) {
    const headers = { 'X-API-Key': this.key, ...extraHeaders };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const res = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = null;
    const text = await res.text();
    if (text) {
      try { data = JSON.parse(text); } catch { data = { error: { message: text } }; }
    }
    if (!res.ok) throw new ApiError(res.status, data);
    return data;
  }

  get(path) { return this.request('GET', path); }
  post(path, body, headers) { return this.request('POST', path, body ?? {}, headers); }
}

export async function fetchSession() {
  const res = await fetch('/app/session', { headers: { Accept: 'application/json' } });
  if (!res.ok) return null;
  return res.json();
}
