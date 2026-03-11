export default defineOAuthGoogleEventHandler({
  config: {
    scope: ['openid', 'email', 'profile'],
  },
  async onSuccess(event, { user }) {
    // Only allow configured owner emails (comma-separated env var)
    const allowedEmails = (process.env.ALLOWED_EMAILS || 'peterfusek1980@gmail.com')
      .split(',')
      .map(e => e.trim().toLowerCase())
    if (!allowedEmails.includes(user.email?.toLowerCase())) {
      throw createError({ statusCode: 403, message: 'Access denied' })
    }

    await setUserSession(event, {
      user: {
        email: user.email,
        name: user.name,
        picture: user.picture,
      },
    })

    return sendRedirect(event, '/dashboard')
  },
})
