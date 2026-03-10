<script setup lang="ts">
definePageMeta({ layout: false })

const { loggedIn } = useUserSession()

// If already logged in, redirect to dashboard
if (loggedIn.value) {
  navigateTo('/dashboard')
}
</script>

<template>
  <div class="min-h-screen bg-neutral-950 flex items-center justify-center">
    <div class="text-center space-y-6">
      <div class="flex items-center justify-center gap-3">
        <div class="size-12 rounded-xl bg-primary-500/20 flex items-center justify-center">
          <UIcon name="i-lucide-radar" class="size-7 text-primary-400" />
        </div>
        <div class="text-left">
          <p class="text-lg font-semibold text-primary-400">Mission Control</p>
          <p class="text-xs text-neutral-500">Contacts Refiner</p>
        </div>
      </div>

      <p class="text-sm text-neutral-400">Sign in to access the dashboard</p>

      <a
        href="/auth/google"
        class="inline-flex items-center gap-3 px-6 py-3 rounded-xl bg-neutral-800 hover:bg-neutral-700 border border-neutral-700 hover:border-neutral-600 text-neutral-200 text-sm font-medium transition-all"
      >
        <svg class="size-5" viewBox="0 0 24 24">
          <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" />
          <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
          <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
          <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
        </svg>
        Sign in with Google
      </a>
    </div>
  </div>
</template>
