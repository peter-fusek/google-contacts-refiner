/**
 * Simple in-memory rate limiter for API endpoints.
 * Limits requests per IP to prevent GCS quota exhaustion via abuse.
 */
const windowMs = 60_000 // 1 minute window
const maxRequests = 60 // 60 requests per minute per IP

const hits = new Map<string, { count: number; resetAt: number }>()

// Cleanup stale entries every 5 minutes
setInterval(() => {
  const now = Date.now()
  for (const [ip, entry] of hits) {
    if (entry.resetAt < now) hits.delete(ip)
  }
}, 5 * 60_000)

export default defineEventHandler((event) => {
  const path = getRequestURL(event).pathname

  // Only rate-limit API routes
  if (!path.startsWith('/api/')) return

  // Use the LAST X-Forwarded-For entry (injected by the trusted proxy, e.g. Render)
  // The first entry is user-controlled and can be spoofed
  const forwarded = getHeader(event, 'x-forwarded-for')
  const ip = forwarded
    ? forwarded.split(',').at(-1)!.trim()
    : (getRequestIP(event) ?? 'unknown')
  const now = Date.now()

  let entry = hits.get(ip)
  if (!entry || entry.resetAt < now) {
    entry = { count: 0, resetAt: now + windowMs }
    hits.set(ip, entry)
  }

  entry.count++

  setResponseHeader(event, 'X-RateLimit-Limit', String(maxRequests))
  setResponseHeader(event, 'X-RateLimit-Remaining', String(Math.max(0, maxRequests - entry.count)))

  if (entry.count > maxRequests) {
    throw createError({
      statusCode: 429,
      message: 'Too many requests',
    })
  }
})
