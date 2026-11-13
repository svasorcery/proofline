/** Same-origin transport. Writes run once; callers own explicit reconciliation. */
export class ApiError extends Error {
  readonly code: string;
  readonly details: Record<string, unknown>;
  readonly requestId: string | null;
  readonly status: number;
  readonly outcomeUnknown: boolean;

  constructor(
    code: string,
    message: string,
    options: {
      details?: Record<string, unknown>;
      requestId?: string | null;
      status?: number;
      outcomeUnknown?: boolean;
    } = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.details = options.details ?? {};
    this.requestId = options.requestId ?? null;
    this.status = options.status ?? 0;
    this.outcomeUnknown = options.outcomeUnknown ?? false;
  }
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method ?? 'GET').toUpperCase();
  const unsafe = !['GET', 'HEAD', 'OPTIONS'].includes(method);
  const headers = new Headers(options.headers);
  headers.set('Accept', 'application/json');
  if (options.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, { ...options, headers, credentials: 'same-origin' });
  } catch (error) {
    // Browser cancellation can originate in another realm (for example an
    // iframe); relying on instanceof Error would turn it into a visible error.
    if (!unsafe && record(error) && error.name === 'AbortError') throw error;
    throw new ApiError(
      'NETWORK_ERROR',
      unsafe
        ? 'Связь прервалась. Исход запроса неизвестен; сначала проверьте сохранённое состояние.'
        : 'Не удалось связаться с сервером.',
      { outcomeUnknown: unsafe },
    );
  }
  if (response.status === 204) return undefined as T;
  let body: unknown;
  try {
    body = await response.json();
  } catch (error) {
    if (!unsafe && record(error) && error.name === 'AbortError') throw error;
    throw new ApiError(
      response.ok ? 'INVALID_RESPONSE' : `HTTP_${response.status}`,
      'Сервер вернул ответ, который не удалось прочитать.',
      {
        status: response.status,
        requestId: response.headers.get('X-Request-ID'),
        outcomeUnknown: unsafe,
      },
    );
  }
  if (!response.ok) {
    const envelope = record(body) ? body : {};
    throw new ApiError(
      typeof envelope.code === 'string' ? envelope.code : `HTTP_${response.status}`,
      typeof envelope.message === 'string' ? envelope.message : 'Не удалось выполнить запрос.',
      {
        details: record(envelope.details) ? envelope.details : {},
        requestId:
          typeof envelope.request_id === 'string'
            ? envelope.request_id
            : response.headers.get('X-Request-ID'),
        status: response.status,
        outcomeUnknown: unsafe && response.status >= 500,
      },
    );
  }
  return body as T;
}

export function command<T>(
  path: string,
  method: 'POST' | 'PATCH',
  body: unknown,
  key?: string,
): Promise<T> {
  const headers: Record<string, string> = {};
  if (key !== undefined) headers['Idempotency-Key'] = key;
  return api<T>(path, { method, body: JSON.stringify(body), headers });
}

export function newKey(): string {
  return crypto.randomUUID();
}
