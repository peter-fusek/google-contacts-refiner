export default defineNuxtConfig({
  modules: [
    '@nuxt/ui',
    'nuxt-auth-utils',
  ],

  app: {
    head: {
      htmlAttrs: { lang: 'en' },
      title: 'Contact Refiner — AI-Powered Google Contacts Cleanup',
      meta: [
        { name: 'description', content: 'Automatically fix diacritics, formatting, and duplicates in your Google Contacts. AI-powered analysis with human review.' },
        { property: 'og:site_name', content: 'Contact Refiner' },
        { property: 'og:locale', content: 'en_US' },
        { property: 'og:image', content: 'https://contactrefiner.com/og-image.png' },
        { name: 'twitter:card', content: 'summary_large_image' },
      ],
      link: [
        { rel: 'canonical', href: 'https://contactrefiner.com' },
      ],
      script: [
        { src: 'https://www.googletagmanager.com/gtag/js?id=G-QFW0D3J3KV', async: true },
        { innerHTML: "window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments)}gtag('js',new Date());gtag('config','G-QFW0D3J3KV');" },
      ],
    },
  },

  colorMode: {
    preference: 'dark',
    fallback: 'dark',
  },

  runtimeConfig: {
    gcsBucket: process.env.GCS_BUCKET || 'contacts-refiner-data',
    gcsServiceAccount: process.env.GCS_SERVICE_ACCOUNT || '',
    githubToken: process.env.GITHUB_TOKEN || '',
    githubRepo: process.env.GITHUB_REPO || 'peter-fusek/google-contacts-refiner',
    public: {
      appVersion: process.env.npm_package_version || '0.1.0',
      buildDate: new Date().toISOString().slice(0, 10),
      gitSha: process.env.RENDER_GIT_COMMIT?.slice(0, 7) || process.env.GIT_SHA?.slice(0, 7) || '',
    },
  },

  ui: {
    colorMode: false,
    theme: {
      colors: ['primary', 'success', 'warning', 'error', 'info'],
    },
  },

  css: ['~/assets/css/main.css'],

  devtools: {
    enabled: process.env.NODE_ENV !== 'production',
  },

  nitro: {
    routeRules: {
      '/**': {
        headers: {
          'X-Frame-Options': 'DENY',
          'X-Content-Type-Options': 'nosniff',
          'Referrer-Policy': 'strict-origin-when-cross-origin',
          'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
        },
      },
    },
  },

  compatibilityDate: '2025-01-15',
})
