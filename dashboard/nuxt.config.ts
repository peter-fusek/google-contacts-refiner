export default defineNuxtConfig({
  modules: [
    '@nuxt/ui',
    'nuxt-auth-utils',
    '@nuxtjs/plausible',
  ],

  plausible: {
    domain: 'contactrefiner.com',
    apiHost: 'https://plausible.io',
    autoPageviews: true,
  },

  app: {
    head: {
      htmlAttrs: { lang: 'en' },
      title: 'Contact Refiner — AI-Powered Google Contacts Cleanup',
      meta: [
        { name: 'description', content: 'Automatically fix diacritics, formatting, and duplicates in your Google Contacts. AI-powered analysis with human review.' },
        { property: 'og:site_name', content: 'Contact Refiner' },
        { property: 'og:locale', content: 'en_US' },
        { name: 'twitter:card', content: 'summary_large_image' },
      ],
      link: [
        { rel: 'canonical', href: 'https://contactrefiner.com' },
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
    public: {
      appVersion: process.env.npm_package_version || '0.1.0',
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

  compatibilityDate: '2025-01-15',
})
