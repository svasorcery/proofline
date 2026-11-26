import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, command } from './api';

afterEach(() => vi.unstubAllGlobals());

function guestRequired(): Response {
  return new Response(JSON.stringify({ code: 'GUEST_SESSION_REQUIRED' }), { status: 401 });
}

describe('API transport', () => {
  it('opens one guest session for concurrent reads and retries those reads', async () => {
    let opened = false;
    const fetch = vi.fn(async (path: string, options: RequestInit) => {
      if (path === '/api/v1/guest-session') {
        expect(options.method).toBe('POST');
        expect(options.credentials).toBe('same-origin');
        opened = true;
        return new Response(null, { status: 204 });
      }
      return opened ? new Response(JSON.stringify({ path })) : guestRequired();
    });
    vi.stubGlobal('fetch', fetch);
    const lock = vi.fn(async (_name: string, open: () => Promise<void>) => open());
    vi.stubGlobal('navigator', { locks: { request: lock } });

    await expect(Promise.all([api('/cases'), api('/cases/demo/runs')])).resolves.toEqual([
      { path: '/api/v1/cases' },
      { path: '/api/v1/cases/demo/runs' },
    ]);
    expect(fetch.mock.calls.filter(([path]) => path.endsWith('/guest-session'))).toHaveLength(1);
    expect(fetch).toHaveBeenCalledTimes(5);
    expect(lock).toHaveBeenCalledOnce();
  });

  it('does not bootstrap and repeat a write when the guest session is missing', async () => {
    const fetch = vi.fn().mockResolvedValue(guestRequired());
    vi.stubGlobal('fetch', fetch);
    await expect(command('/cases', 'POST', {})).rejects.toMatchObject({
      code: 'GUEST_SESSION_REQUIRED',
      outcomeUnknown: false,
    });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('keeps the server error code, field details and request ID', async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          code: 'CASE_VERSION_CONFLICT',
          message: 'The case changed.',
          details: { expected: 2, actual: 3 },
          request_id: 'request-17',
        }),
        { status: 409 },
      ),
    );
    vi.stubGlobal('fetch', fetch);
    await expect(
      command('/cases/V04', 'PATCH', { expected_case_version: 2, title: 'Draft' }),
    ).rejects.toMatchObject({
      code: 'CASE_VERSION_CONFLICT',
      requestId: 'request-17',
      details: { expected: 2, actual: 3 },
      status: 409,
      outcomeUnknown: false,
    });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('does not retry an uncertain write and preserves its idempotency key', async () => {
    const fetch = vi.fn().mockRejectedValue(new TypeError('connection lost'));
    vi.stubGlobal('fetch', fetch);
    await expect(
      command('/cases/V04/runs', 'POST', { mode: 'replay' }, 'same-attempt'),
    ).rejects.toMatchObject({ code: 'NETWORK_ERROR', outcomeUnknown: true });
    expect(fetch).toHaveBeenCalledTimes(1);
    const [path, options] = fetch.mock.calls[0]!;
    expect(path).toBe('/api/v1/cases/V04/runs');
    expect(new Headers(options.headers).get('Idempotency-Key')).toBe('same-attempt');
  });

  it('marks failed reads as unavailable without claiming a write outcome', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')));
    await expect(api('/cases')).rejects.toMatchObject({ outcomeUnknown: false });
  });

  it('treats a server failure during a write as uncertain', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            code: 'DATABASE_UNAVAILABLE',
            message: 'Unavailable.',
            details: {},
            request_id: 'db-1',
          }),
          { status: 503 },
        ),
      ),
    );
    await expect(
      command('/proposals/p/decision', 'POST', { kind: 'approve' }, 'decision-1'),
    ).rejects.toMatchObject({ outcomeUnknown: true, code: 'DATABASE_UNAVAILABLE' });
  });

  it('preserves cancellation so obsolete reads do not become visible errors', async () => {
    const error = new DOMException('Aborted', 'AbortError');
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(error));
    await expect(api('/cases', { signal: new AbortController().signal })).rejects.toBe(error);
  });

  it('preserves cancellation while the response body is still being read', async () => {
    const error = new DOMException('Aborted', 'AbortError');
    const response = new Response('{}');
    vi.spyOn(response, 'json').mockRejectedValue(error);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response));
    await expect(api('/cases', { signal: new AbortController().signal })).rejects.toBe(error);
  });

  it('does not expose a non-JSON proxy error as source HTML', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response('<h1>Proxy secret</h1>', {
          status: 502,
          headers: { 'X-Request-ID': 'proxy-1' },
        }),
      ),
    );
    try {
      await api('/cases');
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect(error).toMatchObject({ code: 'HTTP_502', requestId: 'proxy-1' });
      expect((error as Error).message).not.toContain('Proxy secret');
    }
  });
});
