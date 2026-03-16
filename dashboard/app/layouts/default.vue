<script setup lang="ts">
const route = useRoute()
const { user, loggedIn, clear: logout } = useUserSession()

const isDemo = computed(() => !loggedIn.value)

const navItems = [
  { label: 'Status', icon: 'i-lucide-activity', to: '/dashboard' },
  { label: 'Changelog', icon: 'i-lucide-file-diff', to: '/changelog' },
  { label: 'Analytics', icon: 'i-lucide-bar-chart-3', to: '/analytics' },
  { label: 'Review', icon: 'i-lucide-check-circle', to: '/review' },
  { label: 'Runs', icon: 'i-lucide-play-circle', to: '/runs' },
  { label: 'Config', icon: 'i-lucide-settings', to: '/config' },
]

function isActive(to: string) {
  return to === '/dashboard' ? route.path === '/dashboard' : route.path.startsWith(to)
}
</script>

<template>
  <div class="flex min-h-screen bg-neutral-950">
    <!-- Sidebar -->
    <aside class="w-56 shrink-0 border-r border-neutral-800 bg-neutral-950 flex flex-col">
      <!-- Header -->
      <div class="flex items-center gap-2 p-4 border-b border-neutral-800/50">
        <div class="size-8 rounded-lg bg-primary-500/20 flex items-center justify-center">
          <UIcon name="i-lucide-radar" class="size-5 text-primary-400" />
        </div>
        <div class="min-w-0">
          <p class="text-sm font-semibold text-primary-400 truncate">
            Mission Control
          </p>
          <p class="text-[10px] text-neutral-500 truncate">
            Contacts Refiner
          </p>
        </div>
      </div>

      <!-- Nav -->
      <nav class="flex-1 p-3 space-y-1">
        <NuxtLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors"
          :class="isActive(item.to)
            ? 'bg-primary-500/15 text-primary-400'
            : 'text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/50'"
        >
          <UIcon :name="item.icon" class="size-4" />
          {{ item.label }}
        </NuxtLink>
      </nav>

      <!-- User + Footer -->
      <div class="p-3 border-t border-neutral-800/50 space-y-2">
        <div v-if="user" class="flex items-center gap-2 px-1">
          <img
            v-if="user.picture"
            :src="user.picture"
            :alt="user.name"
            class="size-6 rounded-full"
          />
          <span class="text-xs text-neutral-400 truncate flex-1">{{ user.name }}</span>
          <button
            class="text-xs text-neutral-600 hover:text-neutral-400 transition-colors"
            @click="logout()"
          >
            <UIcon name="i-lucide-log-out" class="size-3.5" />
          </button>
        </div>
        <div v-else class="px-1">
          <a
            href="/login"
            class="flex items-center gap-2 text-xs text-primary-400 hover:text-primary-300 transition-colors"
          >
            <UIcon name="i-lucide-log-in" class="size-3.5" />
            Sign in
          </a>
        </div>
        <div class="text-xs text-neutral-600 px-1">
          v{{ useRuntimeConfig().public.appVersion }}
        </div>
      </div>
    </aside>

    <!-- Main -->
    <main class="flex-1 overflow-y-auto">
      <!-- Demo banner -->
      <div v-if="isDemo" class="bg-amber-500/10 border-b border-amber-500/20 px-6 py-2.5 flex items-center justify-between">
        <div class="flex items-center gap-2 text-sm text-amber-400">
          <UIcon name="i-lucide-eye" class="size-4" />
          <span class="font-medium">DEMO MODE</span>
          <span class="text-amber-500/70">— Read-only view with masked personal data</span>
        </div>
        <a
          href="/login"
          class="text-xs px-3 py-1 rounded-lg border border-amber-500/30 text-amber-400 hover:bg-amber-500/10 transition-colors"
        >
          Sign in
        </a>
      </div>
      <div class="max-w-7xl mx-auto p-6">
        <slot />
      </div>
    </main>
  </div>
</template>
