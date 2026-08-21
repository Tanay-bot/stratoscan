import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AxiosError, AxiosHeaders } from 'axios'
import { ApiError } from './types'
import { api, getToken, setToken, toApiError } from './client'

describe('token storage', () => {
  const KEY = 'stratoscan_token'

  beforeEach(() => window.localStorage.clear())
  afterEach(() => window.localStorage.clear())

  it('persists and reads the token', () => {
    setToken('abc.def.ghi')
    expect(getToken()).toBe('abc.def.ghi')
    expect(window.localStorage.getItem(KEY)).toBe('abc.def.ghi')
  })

  it('removes the token when set to null', () => {
    setToken('abc')
    setToken(null)
    expect(getToken()).toBeNull()
  })
})

describe('toApiError', () => {
  function makeAxiosError(overrides: Partial<AxiosError>): AxiosError {
    return {
      isAxiosError: true,
      name: 'AxiosError',
      message: '',
      config: { headers: new AxiosHeaders() },
      toJSON: () => ({}),
      ...overrides,
    } as unknown as AxiosError
  }

  it('returns ApiError instances untouched', () => {
    const err = new ApiError('already normalized', 418)
    expect(toApiError(err)).toBe(err)
  })

  it('maps a network failure to an actionable message', () => {
    const err = makeAxiosError({ code: 'ERR_NETWORK', response: undefined })
    const apiErr = toApiError(err)
    expect(apiErr.status).toBe(0)
    expect(apiErr.message).toContain('Cannot reach the Stratoscan API')
  })

  it('extracts the backend detail string', () => {
    const err = makeAxiosError({
      response: { status: 429, data: { detail: 'Rate limit exceeded', retry_after: 30 } } as never,
    })
    const apiErr = toApiError(err)
    expect(apiErr.status).toBe(429)
    expect(apiErr.message).toBe('Rate limit exceeded')
    expect(apiErr.retryAfter).toBe(30)
  })

  it('reads Retry-After seconds from the header (authoritative over the body)', () => {
    const err = makeAxiosError({
      response: {
        status: 429,
        data: { detail: 'Rate limit exceeded', retry_after: 999 },
        headers: { 'retry-after': '45' },
      } as never,
    })
    const apiErr = toApiError(err)
    expect(apiErr.status).toBe(429)
    expect(apiErr.retryAfter).toBe(45)
  })

  it('parses an HTTP-date Retry-After header into whole seconds', () => {
    const later = new Date(Date.now() + 12_000).toUTCString()
    const err = makeAxiosError({
      response: {
        status: 429,
        data: { detail: 'Rate limit exceeded' },
        headers: { 'retry-after': later },
      } as never,
    })
    const apiErr = toApiError(err)
    expect(apiErr.retryAfter).toBeGreaterThanOrEqual(11)
    expect(apiErr.retryAfter).toBeLessThanOrEqual(12)
  })

  it('falls back to the body retry_after when the header is missing', () => {
    const err = makeAxiosError({
      response: { status: 429, data: { detail: 'Rate limit exceeded', retry_after: 30 } } as never,
    })
    expect(toApiError(err).retryAfter).toBe(30)
  })

  it('leaves retryAfter undefined when a 429 carries no wait info', () => {
    const err = makeAxiosError({ response: { status: 429, data: { detail: 'nope' } } as never })
    expect(toApiError(err).retryAfter).toBeUndefined()
  })

  it('never surfaces raw HTML (dev proxy 500 page)', () => {
    const html = '<html><body><h1>502 Bad Gateway</h1></body></html>'
    const err = makeAxiosError({ response: { status: 500, data: html } as never })
    const apiErr = toApiError(err)
    expect(apiErr.message).not.toContain('<html>')
  })

  it('falls back to the axios message', () => {
    const err = makeAxiosError({ message: 'Request failed with status code 404' })
    const apiErr = toApiError(err)
    expect(apiErr.message).toBe('Request failed with status code 404')
  })

  it('handles non-axios errors', () => {
    expect(toApiError(new Error('boom')).message).toBe('boom')
    expect(toApiError('weird').message).toBe('Something went wrong. Please try again.')
  })

  it('normalizes a FastAPI 422 validation array into a readable message', () => {
    const err = makeAxiosError({
      response: {
        status: 422,
        data: {
          detail: [
            { loc: ['body', 'query'], msg: 'Field required', type: 'missing' },
            {
              loc: ['query'],
              msg: 'String should have at least 1 character',
              type: 'string_too_short',
            },
          ],
        },
      } as never,
    })
    const apiErr = toApiError(err)
    expect(apiErr.status).toBe(422)
    expect(apiErr.message).toContain('body.query: Field required')
    expect(apiErr.message).toContain('query: String should have at least 1 character')
  })
})

describe('401 auto-logout', () => {
  const savedAdapter = api.defaults.adapter

  beforeEach(() => {
    window.localStorage.clear()
  })

  afterEach(() => {
    api.defaults.adapter = savedAdapter
    vi.restoreAllMocks()
    window.localStorage.clear()
  })

  it('fires the unauthorized handler on a 401 outside auth paths', async () => {
    setToken('tok')
    const handler = vi.fn()
    const { setUnauthorizedHandler } = await import('./client')
    setUnauthorizedHandler(handler)
    try {
      api.defaults.adapter = async (config) => {
        // Real adapters settle non-2xx into a rejected AxiosError; custom
        // adapters must do the same or the response interceptor never fires.
        const response = {
          data: { detail: 'expired' },
          status: 401,
          statusText: 'Unauthorized',
          headers: {},
          config,
        }
        throw new AxiosError(
          'Request failed with status code 401',
          AxiosError.ERR_BAD_REQUEST,
          config,
          null,
          response as never,
        )
      }

      await expect(api.get('/stocks')).rejects.toThrow()
      expect(handler).toHaveBeenCalledTimes(1)
    } finally {
      setUnauthorizedHandler(null)
    }
  })

  it('never fires the handler for a 401 on an auth path (normal response)', async () => {
    setToken('tok')
    const handler = vi.fn()
    const { setUnauthorizedHandler } = await import('./client')
    setUnauthorizedHandler(handler)
    try {
      api.defaults.adapter = async (config) => {
        const response = {
          data: { detail: 'Invalid email or password' },
          status: 401,
          statusText: 'Unauthorized',
          headers: {},
          config,
        }
        throw new AxiosError(
          'Request failed with status code 401',
          AxiosError.ERR_BAD_REQUEST,
          config,
          null,
          response as never,
        )
      }

      await expect(api.post('/auth/login', { email: 'a@b.co', password: 'nope' })).rejects.toThrow()
      expect(handler).not.toHaveBeenCalled()
    } finally {
      setUnauthorizedHandler(null)
    }
  })
})
