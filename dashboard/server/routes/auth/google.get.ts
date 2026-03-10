export default defineOAuthGoogleEventHandler({
  config: {
    scope: ['openid', 'email', 'profile'],
  },
  async onSuccess(event, { user }) {
    // Only allow the owner's email
    const allowed = 'peterfusek1980@gmail.com'
    if (user.email !== allowed) {
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
