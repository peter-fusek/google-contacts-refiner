<script setup lang="ts">
const route = useRoute()
const { user, loggedIn, clear: logout } = useUserSession()

const isDemo = computed(() => !loggedIn.value)
const sidebarOpen = ref(false)

const navItems = [
  { label: 'Status', icon: 'i-lucide-activity', to: '/dashboard' },
  { label: 'Review', icon: 'i-lucide-check-circle', to: '/review', highlight: true },
  { label: 'Changelog', icon: 'i-lucide-file-diff', to: '/changelog' },
  { label: 'Analytics', icon: 'i-lucide-bar-chart-3', to: '/analytics' },
  { label: 'Social Signals', icon: 'i-lucide-radar', to: '/social-signals' },
  { label: 'FollowUp', icon: 'i-lucide-user-round-check', to: '/followup' },
  { label: 'Runs', icon: 'i-lucide-play-circle', to: '/runs' },
  { label: 'Config', icon: 'i-lucide-settings', to: '/config' },
]

function isActive(to: string) {
  return to === '/dashboard' ? route.path === '/dashboard' : route.path.startsWith(to)
}

// Close sidebar on route change (mobile)
watch(() => route.path, () => {
  sidebarOpen.value = false
})
</script>

<template>
  <div class="flex min-h-screen bg-neutral-950">
    <!-- Mobile header -->
    <div class="fixed top-0 inset-x-0 z-40 md:hidden bg-neutral-950 border-b border-neutral-800 flex items-center justify-between px-4 h-12">
      <button
        class="text-neutral-400 hover:text-neutral-200 transition-colors"
        aria-label="Toggle navigation"
        @click="sidebarOpen = !sidebarOpen"
      >
        <UIcon :name="sidebarOpen ? 'i-lucide-x' : 'i-lucide-menu'" class="size-5" />
      </button>
      <span class="text-sm font-semibold text-primary-400">Mission Control</span>
      <div class="w-5" />
    </div>

    <!-- Sidebar overlay (mobile) -->
    <div
      v-if="sidebarOpen"
      class="fixed inset-0 z-30 bg-black/60 md:hidden"
      @click="sidebarOpen = false"
    />

    <!-- Sidebar -->
    <aside
      class="fixed md:static z-30 top-0 left-0 h-full w-56 shrink-0 border-r border-neutral-800 bg-neutral-950 flex flex-col transition-transform duration-200"
      :class="sidebarOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'"
    >
      <!-- Header -->
      <div class="flex items-center gap-2 p-4 border-b border-neutral-800/50">
        <img src="/favicon.svg" alt="Contact Refiner" class="size-8" />
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
          class="flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-all duration-200"
          :class="[
            isActive(item.to)
              ? 'bg-primary-500/15 text-primary-400 nav-active'
              : item.highlight
                ? 'text-primary-300 hover:text-primary-200 hover:bg-primary-500/10 border border-primary-500/20'
                : 'text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/50',
          ]"
        >
          <UIcon :name="item.icon" class="size-4" />
          {{ item.label }}
          <span v-if="item.highlight && !isActive(item.to)" class="ml-auto text-[9px] uppercase tracking-wider text-primary-500/60 font-semibold">Action</span>
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
        <div class="text-[10px] text-neutral-600 px-1 space-y-0.5">
          <div>v{{ useRuntimeConfig().public.appVersion }}</div>
          <div class="text-neutral-700">
            {{ useRuntimeConfig().public.buildDate }}
            <span v-if="useRuntimeConfig().public.gitSha" class="font-mono">{{ useRuntimeConfig().public.gitSha }}</span>
          </div>
        </div>
      </div>
    </aside>

    <!-- Main -->
    <main class="flex-1 overflow-y-auto pt-12 md:pt-0">
      <!-- Demo banner -->
      <div v-if="isDemo" class="bg-amber-500/10 border-b border-amber-500/20 px-4 md:px-6 py-2.5 flex items-center justify-between">
        <div class="flex items-center gap-2 text-sm text-amber-400">
          <UIcon name="i-lucide-eye" class="size-4" />
          <span class="font-medium">DEMO MODE</span>
          <span class="text-amber-500/70 hidden sm:inline">— Read-only view with masked personal data</span>
        </div>
        <a
          href="/login"
          class="text-xs px-3 py-1 rounded-lg border border-amber-500/30 text-amber-400 hover:bg-amber-500/10 transition-colors"
        >
          Sign in
        </a>
      </div>
      <div class="max-w-7xl mx-auto p-4 md:p-6">
        <slot />
      </div>
    </main>
  </div>
</template>
