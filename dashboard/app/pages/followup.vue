<script setup lang="ts">
useHead({
  title: 'FollowUp — Contact Refiner',
  meta: [
    { name: 'description', content: 'AI-powered reconnect recommendations combining LinkedIn signals and interaction history.' },
  ],
})

import type { FollowUpResponse, FollowUpScore } from '~/server/utils/types'

const { data, status, refresh } = useFetch<FollowUpResponse>('/api/followup-scores')

// Filter state
const filterType = ref<string>('all')
const searchQuery = ref('')
const sortBy = ref<'score' | 'name' | 'gap'>('score')

const filteredScores = computed(() => {
  let scores = data.value?.scores ?? []

  // Filter by LinkedIn signal type
  if (filterType.value === 'job_change') {
    scores = scores.filter(s => s.linkedin?.signal_type === 'job_change')
  } else if (filterType.value === 'with_linkedin') {
    scores = scores.filter(s => s.linkedin !== null)
  } else if (filterType.value === 'no_linkedin') {
    scores = scores.filter(s => s.linkedin === null)
  }

  // Search by name, org, or signal text
  if (searchQuery.value) {
    const q = searchQuery.value.toLowerCase()
    scores = scores.filter(s =>
      s.name.toLowerCase().includes(q)
      || s.contact.org?.toLowerCase().includes(q)
      || s.linkedin?.signal_text?.toLowerCase().includes(q)
      || s.linkedin?.headline?.toLowerCase().includes(q),
    )
  }

  // Sort
  if (sortBy.value === 'name') {
    scores = [...scores].sort((a, b) => a.name.localeCompare(b.name))
  } else if (sortBy.value === 'gap') {
    scores = [...scores].sort((a, b) => b.interaction.months_gap - a.interaction.months_gap)
  }
  // Default 'score' — already sorted by rank from server

  return scores
})

const generatedDate = computed(() => {
  const gen = data.value?.generated
  if (!gen) return null
  return new Date(gen).toLocaleDateString('en-US', {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
})

function signalBadge(type: string | undefined) {
  switch (type) {
    case 'job_change': return { label: 'Job Change', color: 'text-green-400', bg: 'bg-green-500/10 border-green-500/20', icon: 'i-lucide-arrow-right-left' }
    case 'active': return { label: 'Active', color: 'text-yellow-400', bg: 'bg-yellow-500/10 border-yellow-500/20', icon: 'i-lucide-message-circle' }
    case 'profile': return { label: 'Profile', color: 'text-neutral-400', bg: 'bg-neutral-500/10 border-neutral-500/20', icon: 'i-lucide-user' }
    default: return null
  }
}

function scoreColor(score: FollowUpScore): string {
  if (score.score_breakdown.linkedin >= 30) return 'text-green-400'
  if (score.score_breakdown.linkedin >= 10) return 'text-yellow-400'
  if (score.score_total >= 100) return 'text-cyan-400'
  return 'text-neutral-300'
}

function completenessBar(score: FollowUpScore): string[] {
  const fields = [
    { has: score.contact.has_email, label: 'Email' },
    { has: score.contact.has_phone, label: 'Phone' },
    { has: score.contact.has_org, label: 'Org' },
    { has: score.contact.has_linkedin_url, label: 'LinkedIn' },
  ]
  return fields.filter(f => f.has).map(f => f.label)
}

function formatGap(months: number): string {
  if (months >= 24) return `${Math.round(months / 12)}y`
  return `${Math.round(months)}mo`
}

// Auto-refresh every 120s
let interval: ReturnType<typeof setInterval>
onMounted(() => { interval = setInterval(refresh, 120_000) })
onUnmounted(() => clearInterval(interval))
</script>

<template>
  <div class="space-y-6">
    <!-- Header -->
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-xl font-bold text-neutral-100">
          FollowUp
        </h1>
        <p class="text-xs text-neutral-500 mt-1">
          AI-powered reconnect recommendations
          <span v-if="generatedDate"> &middot; {{ generatedDate }}</span>
        </p>
      </div>
      <UButton
        icon="i-lucide-refresh-cw"
        size="xs"
        variant="ghost"
        color="neutral"
        :loading="status === 'pending'"
        @click="refresh()"
      />
    </div>

    <!-- Loading -->
    <div v-if="status === 'pending' && !data" class="text-center py-16">
      <UIcon name="i-lucide-loader" class="size-8 text-neutral-500 mx-auto mb-3 animate-spin" />
      <p class="text-neutral-500">Loading FollowUp scores...</p>
    </div>

    <!-- Error -->
    <div v-else-if="status === 'error'" class="text-center py-16">
      <UIcon name="i-lucide-alert-triangle" class="size-8 text-red-500 mx-auto mb-3" />
      <p class="text-red-400">Failed to load data</p>
      <UButton label="Retry" size="sm" variant="soft" class="mt-3" @click="refresh()" />
    </div>

    <template v-else-if="data?.scores?.length">
      <!-- Stats Cards -->
      <div class="grid grid-cols-2 md:grid-cols-5 gap-4">
        <StatsCard
          label="Top Contacts"
          :value="data.scores.length"
          icon="i-lucide-users"
          color="cyan"
        />
        <StatsCard
          label="Job Changes"
          :value="data.stats?.job_change ?? 0"
          icon="i-lucide-arrow-right-left"
          color="green"
        />
        <StatsCard
          label="Active"
          :value="data.stats?.active ?? 0"
          icon="i-lucide-message-circle"
          color="amber"
        />
        <StatsCard
          label="No LinkedIn"
          :value="data.stats?.no_linkedin ?? 0"
          icon="i-lucide-user-x"
        />
        <StatsCard
          label="Avg Completeness"
          :value="`${data.stats?.avg_completeness ?? 0}/4`"
          icon="i-lucide-check-circle"
          color="cyan"
        />
      </div>

      <!-- Filters -->
      <div class="flex flex-wrap gap-3 items-center">
        <div class="relative flex-1 min-w-48">
          <UIcon name="i-lucide-search" class="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-neutral-500" />
          <input
            v-model="searchQuery"
            type="text"
            placeholder="Search by name, org, or signal..."
            class="w-full bg-neutral-900 border border-neutral-800 rounded-lg pl-9 pr-3 py-2 text-sm text-neutral-200 placeholder-neutral-600 focus:outline-none focus:border-neutral-600"
          />
        </div>

        <div class="flex gap-1">
          <button
            v-for="opt in [
              { value: 'all', label: 'All' },
              { value: 'job_change', label: 'Job Changes' },
              { value: 'with_linkedin', label: 'With LinkedIn' },
              { value: 'no_linkedin', label: 'No LinkedIn' },
            ]"
            :key="opt.value"
            class="px-3 py-1.5 text-xs rounded-lg border transition-colors"
            :class="filterType === opt.value
              ? 'bg-primary-500/15 border-primary-500/30 text-primary-400'
              : 'border-neutral-800 text-neutral-500 hover:text-neutral-300 hover:border-neutral-700'"
            @click="filterType = opt.value"
          >
            {{ opt.label }}
          </button>
        </div>

        <select
          v-model="sortBy"
          class="bg-neutral-900 border border-neutral-800 rounded-lg px-3 py-1.5 text-xs text-neutral-400 focus:outline-none focus:border-neutral-600"
        >
          <option value="score">Sort: Score</option>
          <option value="name">Sort: Name</option>
          <option value="gap">Sort: Longest Gap</option>
        </select>
      </div>

      <!-- Results count -->
      <p class="text-xs text-neutral-500">
        {{ filteredScores.length }} of {{ data.scores.length }} contacts
      </p>

      <!-- Score Cards -->
      <div class="space-y-3">
        <div
          v-for="score in filteredScores"
          :key="score.resourceName"
          class="rounded-xl border border-neutral-800 bg-neutral-900/50 p-4 hover:border-neutral-700 card-hover"
        >
          <div class="flex items-start justify-between gap-4">
            <div class="min-w-0 flex-1">
              <!-- Name + rank + badge -->
              <div class="flex items-center gap-2 mb-1">
                <span class="text-[10px] text-neutral-600 font-mono tabular-nums w-6 shrink-0">#{{ score.rank }}</span>
                <h3 class="font-semibold text-neutral-100 truncate">{{ score.name }}</h3>
                <span
                  v-if="signalBadge(score.linkedin?.signal_type)"
                  class="shrink-0 text-[10px] px-2 py-0.5 rounded-full border font-medium"
                  :class="signalBadge(score.linkedin?.signal_type)!.bg + ' ' + signalBadge(score.linkedin?.signal_type)!.color"
                >
                  {{ signalBadge(score.linkedin?.signal_type)!.label }}
                </span>
              </div>

              <!-- Org + title -->
              <p v-if="score.contact.org" class="text-sm text-neutral-400 truncate">
                {{ score.contact.org }}
                <span v-if="score.contact.title" class="text-neutral-600"> &middot; {{ score.contact.title }}</span>
              </p>

              <!-- LinkedIn signal text -->
              <p v-if="score.linkedin?.signal_text" class="text-sm text-neutral-300 mt-2">
                <UIcon
                  v-if="score.linkedin.signal_type === 'job_change'"
                  name="i-lucide-arrow-right-left"
                  class="size-3.5 text-green-500 inline mr-1"
                />
                {{ score.linkedin.signal_text }}
              </p>

              <!-- FollowUp prompt -->
              <div v-if="score.followup_prompt" class="mt-2 pl-3 border-l-2 border-primary-500/30">
                <p class="text-xs text-neutral-400 italic">{{ score.followup_prompt }}</p>
              </div>

              <!-- Score breakdown -->
              <div class="mt-3 flex flex-wrap gap-3 text-[10px]">
                <span class="text-neutral-500">
                  Score: <span class="font-semibold tabular-nums" :class="scoreColor(score)">{{ score.score_total }}</span>
                </span>
                <span class="text-neutral-600">
                  Interaction: {{ score.score_breakdown.interaction }}
                </span>
                <span v-if="score.score_breakdown.linkedin > 0" class="text-green-500/70">
                  LinkedIn: +{{ score.score_breakdown.linkedin }}
                </span>
                <span class="text-neutral-600">
                  Completeness: +{{ score.score_breakdown.completeness }}
                </span>
                <span class="text-neutral-600">
                  Gap: {{ formatGap(score.interaction.months_gap) }}
                </span>
                <span class="text-neutral-600">
                  {{ completenessBar(score).join(' · ') }}
                </span>
              </div>
            </div>

            <!-- Action buttons -->
            <div class="flex flex-col gap-1.5 shrink-0">
              <a
                v-if="score.linkedin?.url"
                :href="score.linkedin.url"
                target="_blank"
                rel="noopener noreferrer"
                class="size-8 rounded-lg bg-neutral-800 hover:bg-neutral-700 flex items-center justify-center text-neutral-400 hover:text-neutral-200 transition-colors"
                title="View on LinkedIn"
              >
                <UIcon name="i-lucide-linkedin" class="size-4" />
              </a>
              <a
                :href="`https://contacts.google.com/person/${score.resourceName.replace('people/', '')}`"
                target="_blank"
                rel="noopener noreferrer"
                class="size-8 rounded-lg bg-neutral-800 hover:bg-neutral-700 flex items-center justify-center text-neutral-400 hover:text-neutral-200 transition-colors"
                title="View in Google Contacts"
              >
                <UIcon name="i-lucide-contact" class="size-4" />
              </a>
            </div>
          </div>
        </div>
      </div>

      <!-- Empty filter state -->
      <div v-if="!filteredScores.length" class="text-center py-12">
        <UIcon name="i-lucide-search-x" class="size-8 text-neutral-600 mx-auto mb-3" />
        <p class="text-neutral-500">No contacts match your filters</p>
      </div>
    </template>

    <!-- Empty state: no data at all -->
    <div v-else-if="data && !data.scores?.length" class="text-center py-16">
      <UIcon name="i-lucide-user-round-check" class="size-8 text-neutral-600 mx-auto mb-3" />
      <p class="text-neutral-500">No FollowUp scores yet</p>
      <p class="text-xs text-neutral-600 mt-1">Run <code class="text-neutral-400">python main.py followup</code> to generate scores</p>
    </div>
  </div>
</template>
