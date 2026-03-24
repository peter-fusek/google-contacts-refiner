export default defineEventHandler(async (event) => {
  // Skip auth for the OAuth callback route itself and the login page
  const path = getRequestURL(event).pathname
  if (path === '/' || path.startsWith('/auth/') || path === '/login' || path.startsWith('/_nuxt/')) {
    return
  }

  // Skip non-page API routes that nuxt-auth-utils needs
  if (path === '/api/_auth/session') {
    return
  }

  // Check session
  const session = await getUserSession(event)
  if (!session?.user) {
    // Write API routes require auth — no demo access
    if (path.startsWith('/api/') && event.method !== 'GET') {
      throw createError({ statusCode: 401, message: 'Unauthorized' })
    }

    // Read-only API routes and pages: allow through for demo mode
    // API handlers will check isDemoMode() and mask PII
    // Page components will detect missing session and show demo banner
    return
  }
})
