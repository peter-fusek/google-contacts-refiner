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

  // All API routes and pages require auth
  const session = await getUserSession(event)
  if (!session?.user) {
    // API requests get 401, page requests redirect to login
    if (path.startsWith('/api/')) {
      throw createError({ statusCode: 401, message: 'Unauthorized' })
    }
    return sendRedirect(event, '/login')
  }
})
