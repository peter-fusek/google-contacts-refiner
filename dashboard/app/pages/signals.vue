<script setup lang="ts">
useHead({
  title: 'Signals — Contact Refiner',
  meta: [
    { name: 'description', content: 'Business lead signals for fractional CXO and Instarea — promoted execs, new owners, IT modernisation intent.' },
  ],
})

import type { LeadSignalsResponse, LeadSignal, LeadSignalType, LeadDismissalReason, ContactDetailsResponse } from '~/server/utils/types'

const { data, status, refresh } = useFetch<LeadSignalsResponse>('/api/signals')

const filterType = ref<LeadSignalType | 'all'>('all')
const view = ref<'candidates' | 'backlog' | 'dismissed'>('candidates')
const searchQuery = ref('')
const busyIds = ref<Set<string>>(new Set())
// Fix #182: <details>/<summary> with a nested <button> never toggles in
// Chromium — the inner button captures the click. Manage the reason dropdown
// open state manually so the menu actually appears.
const openDismissFor = ref<string | null>(null)

// #183: row-expansion details panel. Fetched lazily; cached per resourceName so
// re-expanding is instant.
const expandedFor = ref<string | null>(null)
const detailsCache = ref<Record<string, ContactDetailsResponse>>({})
const detailsLoading = ref<Set<string>>(new Set())
const detailsError = ref<Record<string, string>>({})

async function toggleExpand(resourceName: string) {
  if (expandedFor.value === resourceName) {
    expandedFor.value = null
    return
  }
  expandedFor.value = resourceName
  if (detailsCache.value[resourceName] || detailsLoading.value.has(resourceName)) return

  detailsLoading.value.add(resourceName)
  delete detailsError.value[resourceName]
  try {
    const d = await $fetch<ContactDetailsResponse>('/api/contact-details', {
      query: { resourceName },
    })
    detailsCache.value = { ...detailsCache.value, [resourceName]: d }
  } catch (e) {
    const msg = (e as { statusMessage?: string; message?: string })?.statusMessage
      || (e as Error)?.message
      || 'Failed to load'
    detailsError.value = { ...detailsError.value, [resourceName]: msg }
  } finally {
    detailsLoading.value.delete(resourceName)
  }
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })
  } catch {
    return iso
  }
}

function toggleDismissMenu(resourceName: string) {
  openDismissFor.value = openDismissFor.value === resourceName ? null : resourceName
}

function handleDocumentPointer(e: MouseEvent) {
  const target = e.target as HTMLElement | null
  if (!target?.closest('[data-dismiss-menu]')) {
    openDismissFor.value = null
  }
}

onMounted(() => {
  document.addEventListener('mousedown', handleDocumentPointer)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', handleDocumentPointer)
})

const activeList = computed<LeadSignal[]>(() => {
  const d = data.value
  if (!d) return []
  let list: LeadSignal[] =
    view.value === 'backlog' ? d.backlog
    : view.value === 'dismissed' ? d.dismissed
    : d.candidates

  if (filterType.value !== 'all') {
    list = list.filter(s => s.signalTypes.includes(filterType.value as LeadSignalType))
  }
  if (searchQuery.value.trim()) {
    const q = searchQuery.value.toLowerCase()
    list = list.filter(s =>
      s.name.toLowerCase().includes(q)
      || s.org.toLowerCase().includes(q)
      || s.title.toLowerCase().includes(q)
      || (s.linkedinHeadline || '').toLowerCase().includes(q),
    )
  }
  return list
})

const generatedDate = computed(() => {
  const gen = data.value?.generated
  if (!gen) return null
  return new Date(gen).toLocaleDateString('en-US', {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
})

const SIGNAL_META: Record<LeadSignalType, { label: string; color: string; icon: string }> = {
  promoted_ceo: { label: 'CEO', color: 'bg-amber-500/15 text-amber-300 border-amber-500/30', icon: 'i-lucide-crown' },
  new_c_level: { label: 'NEW C-LEVEL', color: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30', icon: 'i-lucide-trending-up' },
  exec_title: { label: 'EXEC', color: 'bg-cyan-500/15 text-cyan-300 border-cyan-500/30', icon: 'i-lucide-briefcase' },
  bank_finance: { label: 'BANK', color: 'bg-blue-500/15 text-blue-300 border-blue-500/30', icon: 'i-lucide-landmark' },
  it_modernisation: { label: 'AI/MODERN', color: 'bg-purple-500/15 text-purple-300 border-purple-500/30', icon: 'i-lucide-cpu' },
  vibecoding_agentic: { label: 'AGENTIC', color: 'bg-pink-500/15 text-pink-300 border-pink-500/30', icon: 'i-lucide-sparkles' },
  recent_job_change: { label: 'JOB CHANGE', color: 'bg-green-500/15 text-green-300 border-green-500/30', icon: 'i-lucide-arrow-right-left' },
  dm_awaiting_reply: { label: 'REPLY DUE', color: 'bg-red-500/15 text-red-300 border-red-500/30', icon: 'i-lucide-reply' },
}

const filterOptions: { value: LeadSignalType | 'all'; label: string }[] = [
  { value: 'all', label: 'All signals' },
  { value: 'promoted_ceo', label: 'CEO' },
  { value: 'new_c_level', label: 'New C-Level' },
  { value: 'exec_title', label: 'Exec Title' },
  { value: 'bank_finance', label: 'Bank / Finance' },
  { value: 'it_modernisation', label: 'IT Modernisation' },
  { value: 'vibecoding_agentic', label: 'Agentic / Vibecoding' },
  { value: 'recent_job_change', label: 'Recent Job Change' },
  { value: 'dm_awaiting_reply', label: 'Awaiting My Reply (DM)' },
]

const DISMISSAL_OPTIONS: { value: LeadDismissalReason; label: string }[] = [
  { value: 'not_a_fit', label: 'Not a fit' },
  { value: 'already_talked', label: 'Already talked' },
  { value: 'stale_signal', label: 'Stale signal' },
  { value: 'wrong_geo', label: 'Wrong geo' },
  { value: 'other', label: 'Other' },
]

async function accept(signal: LeadSignal) {
  if (busyIds.value.has(signal.resourceName)) return
  busyIds.value.add(signal.resourceName)
  try {
    await $fetch('/api/signals/accept', {
      method: 'POST',
      body: { resourceName: signal.resourceName, name: signal.name },
    })
    await refresh()
  } catch (e) {
    console.error('Accept failed', e)
    alert('Accept failed — check console')
  } finally {
    busyIds.value.delete(signal.resourceName)
  }
}

async function dismiss(signal: LeadSignal, reason: LeadDismissalReason) {
  if (busyIds.value.has(signal.resourceName)) return
  busyIds.value.add(signal.resourceName)

  // Optimistic update — move the contact from candidates/backlog to dismissed
  // BEFORE the network round-trip. Fixes #143: without this, the contact is
  // still visible in the list while refresh() is in flight, so a second
  // Dismiss click lands in the `busyIds` guard and silently no-ops.
  // Reassign `data.value` with a new object per Nuxt useFetch gotcha #147 —
  // nested-property mutation can miss reactivity after hydration.
  const snapshot = data.value
  if (snapshot) {
    const nowIso = new Date().toISOString()
    const nextSignal: LeadSignal = {
      ...signal,
      stage: 'dismissed',
      dismissal: { reason, note: '', dismissedAt: nowIso },
    }
    data.value = {
      ...snapshot,
      candidates: snapshot.candidates.filter((s: LeadSignal) => s.resourceName !== signal.resourceName),
      backlog: snapshot.backlog.filter((s: LeadSignal) => s.resourceName !== signal.resourceName),
      dismissed: [nextSignal, ...snapshot.dismissed.filter((s: LeadSignal) => s.resourceName !== signal.resourceName)],
      stats: {
        ...snapshot.stats,
        candidates: Math.max(0, snapshot.stats.candidates - (signal.stage === 'candidate' ? 1 : 0)),
        dismissed: snapshot.stats.dismissed + (signal.stage === 'dismissed' ? 0 : 1),
      },
    }
  }

  try {
    await $fetch('/api/signals/dismiss', {
      method: 'POST',
      body: { resourceName: signal.resourceName, reason, note: '' },
    })
    // Post-write refresh reconciles any server-computed fields (rank, etc.)
    // but the user's next interaction is no longer blocked by it.
    refresh().catch(e => console.warn('Signals refresh after dismiss failed', e))
  } catch (e) {
    console.error('Dismiss failed', e)
    alert('Dismiss failed — check console')
    // Restore the snapshot so the UI reflects the actual server state.
    if (snapshot) data.value = snapshot
  } finally {
    busyIds.value.delete(signal.resourceName)
    openDismissFor.value = null
  }
}
</script>

<template>
  <div class="p-4 md:p-6 space-y-4">
    <!-- Header -->
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 class="text-xl font-semibold text-neutral-100 flex items-center gap-2">
          <UIcon name="i-lucide-radar" class="size-5 text-primary-400" />
          Signals
        </h1>
        <p class="text-xs text-neutral-500 mt-0.5">
          Business leads — promoted execs, new owners, IT modernisation. Capped at {{ data?.weeklyCap ?? 100 }}/week.
        </p>
        <p v-if="generatedDate" class="text-[10px] text-neutral-600 mt-0.5">
          Scored {{ generatedDate }}
        </p>
      </div>

      <UButton
        variant="ghost"
        color="neutral"
        icon="i-lucide-refresh-cw"
        size="xs"
        :loading="status === 'pending'"
        @click="refresh()"
      >
        Refresh
      </UButton>
    </div>

    <!-- Stats row -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-2">
      <div class="border border-neutral-800 rounded-lg px-3 py-2 bg-neutral-900/40">
        <div class="text-[10px] uppercase text-neutral-500">Candidates</div>
        <div class="text-lg font-semibold text-neutral-100">{{ data?.stats?.candidates ?? 0 }}</div>
      </div>
      <div class="border border-neutral-800 rounded-lg px-3 py-2 bg-neutral-900/40">
        <div class="text-[10px] uppercase text-neutral-500">Accepted</div>
        <div class="text-lg font-semibold text-emerald-300">{{ data?.stats?.accepted ?? 0 }}</div>
      </div>
      <div class="border border-neutral-800 rounded-lg px-3 py-2 bg-neutral-900/40">
        <div class="text-[10px] uppercase text-neutral-500">Backlog</div>
        <div class="text-lg font-semibold text-neutral-300">{{ data?.backlog?.length ?? 0 }}</div>
      </div>
      <div class="border border-neutral-800 rounded-lg px-3 py-2 bg-neutral-900/40">
        <div class="text-[10px] uppercase text-neutral-500">Dismissed</div>
        <div class="text-lg font-semibold text-neutral-500">{{ data?.stats?.dismissed ?? 0 }}</div>
      </div>
    </div>

    <!-- View tabs + filters -->
    <div class="flex flex-wrap items-center gap-2">
      <div class="flex rounded-lg border border-neutral-800 p-0.5 bg-neutral-900/40">
        <button
          v-for="tab in [
            { key: 'candidates', label: 'Candidates', count: data?.candidates?.length ?? 0 },
            { key: 'backlog', label: 'Backlog', count: data?.backlog?.length ?? 0 },
            { key: 'dismissed', label: 'Dismissed', count: data?.dismissed?.length ?? 0 },
          ]"
          :key="tab.key"
          class="px-3 py-1 text-xs rounded-md transition-colors"
          :class="view === tab.key
            ? 'bg-primary-500/20 text-primary-300'
            : 'text-neutral-400 hover:text-neutral-200'"
          @click="view = tab.key as typeof view"
        >
          {{ tab.label }}
          <span class="text-[10px] text-neutral-500 ml-1">{{ tab.count }}</span>
        </button>
      </div>

      <select
        v-model="filterType"
        class="bg-neutral-900 border border-neutral-800 rounded-md px-2 py-1 text-xs text-neutral-300"
      >
        <option v-for="opt in filterOptions" :key="opt.value" :value="opt.value">
          {{ opt.label }}
        </option>
      </select>

      <input
        v-model="searchQuery"
        type="search"
        placeholder="Search name / org / title…"
        class="bg-neutral-900 border border-neutral-800 rounded-md px-2 py-1 text-xs text-neutral-300 flex-1 min-w-[180px] max-w-sm"
      />
    </div>

    <!-- List -->
    <div v-if="status === 'pending' && !data" class="text-neutral-500 text-sm">Loading…</div>

    <div v-else-if="activeList.length === 0" class="text-neutral-500 text-sm border border-dashed border-neutral-800 rounded-lg p-6 text-center">
      No signals in this view.
    </div>

    <div v-else class="space-y-2">
      <div
        v-for="sig in activeList"
        :key="sig.resourceName"
        class="border border-neutral-800 rounded-lg p-3 bg-neutral-900/30 hover:bg-neutral-900/60 transition-colors"
      >
        <div class="flex flex-wrap items-start justify-between gap-3">
          <button
            type="button"
            class="flex-1 min-w-0 text-left"
            :aria-expanded="expandedFor === sig.resourceName"
            :aria-controls="`details-${sig.resourceName.replace('/', '-')}`"
            @click="toggleExpand(sig.resourceName)"
          >
            <div class="flex items-center gap-2 flex-wrap">
              <UIcon
                :name="expandedFor === sig.resourceName ? 'i-lucide-chevron-down' : 'i-lucide-chevron-right'"
                class="size-3.5 text-neutral-500"
              />
              <span class="text-[10px] text-neutral-500 font-mono">#{{ sig.rank }}</span>
              <span class="text-sm font-medium text-neutral-100 truncate">{{ sig.name }}</span>
              <span class="text-sm font-semibold text-primary-400">{{ sig.score.toFixed(1) }}</span>
            </div>
            <div class="text-xs text-neutral-400 mt-0.5 flex flex-wrap gap-x-2">
              <span v-if="sig.title" class="text-neutral-300">{{ sig.title }}</span>
              <span v-if="sig.title && sig.org" class="text-neutral-600">·</span>
              <span v-if="sig.org">{{ sig.org }}</span>
            </div>
            <div v-if="sig.linkedinHeadline" class="text-[11px] text-neutral-500 mt-0.5 italic truncate">
              "{{ sig.linkedinHeadline }}"
            </div>

            <div class="flex flex-wrap gap-1 mt-1.5">
              <span
                v-for="t in sig.signalTypes"
                :key="t"
                class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] uppercase tracking-wide"
                :class="SIGNAL_META[t].color"
              >
                <UIcon :name="SIGNAL_META[t].icon" class="size-3" />
                {{ SIGNAL_META[t].label }}
              </span>
              <span
                v-if="sig.monthsSinceContact !== null"
                class="text-[10px] text-neutral-500 px-1.5 py-0.5"
              >
                {{ sig.monthsSinceContact }}mo silent
              </span>
            </div>

            <div v-if="sig.dismissal" class="text-[10px] text-neutral-500 mt-1">
              Dismissed: {{ sig.dismissal.reason.replace('_', ' ') }}
              <span v-if="sig.dismissal.note">— {{ sig.dismissal.note }}</span>
            </div>
          </button>

          <div v-if="view !== 'dismissed'" class="flex items-center gap-1 flex-wrap justify-end">
            <a
              v-if="sig.linkedinUrl"
              :href="sig.linkedinUrl"
              target="_blank"
              rel="noopener"
              class="text-neutral-500 hover:text-primary-400 p-1.5 rounded"
              title="Open LinkedIn"
            >
              <UIcon name="i-lucide-external-link" class="size-4" />
            </a>
            <UButton
              size="xs"
              color="primary"
              variant="soft"
              icon="i-lucide-check"
              :loading="busyIds.has(sig.resourceName)"
              :disabled="sig.stage === 'accepted'"
              @click="accept(sig)"
            >
              {{ sig.stage === 'accepted' ? 'In CRM' : 'Accept → CRM' }}
            </UButton>

            <div class="relative" data-dismiss-menu>
              <UButton
                size="xs"
                color="neutral"
                variant="ghost"
                icon="i-lucide-x"
                :loading="busyIds.has(sig.resourceName)"
                @click="toggleDismissMenu(sig.resourceName)"
              >
                Dismiss
              </UButton>
              <div
                v-if="openDismissFor === sig.resourceName"
                class="absolute right-0 mt-1 w-40 z-10 bg-neutral-900 border border-neutral-800 rounded-lg shadow-xl p-1 space-y-0.5"
              >
                <button
                  v-for="opt in DISMISSAL_OPTIONS"
                  :key="opt.value"
                  class="w-full text-left px-2 py-1 text-xs rounded hover:bg-neutral-800 text-neutral-300"
                  @click="dismiss(sig, opt.value)"
                >
                  {{ opt.label }}
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- #183: expanded details panel -->
        <div
          v-if="expandedFor === sig.resourceName"
          :id="`details-${sig.resourceName.replace('/', '-')}`"
          class="mt-3 pt-3 border-t border-neutral-800 space-y-3"
        >
          <div v-if="detailsLoading.has(sig.resourceName)" class="text-xs text-neutral-500">
            Loading details…
          </div>
          <div v-else-if="detailsError[sig.resourceName]" class="text-xs text-red-400">
            Failed to load details: {{ detailsError[sig.resourceName] }}
          </div>
          <template v-else-if="detailsCache[sig.resourceName]">
            <!-- External links row -->
            <div class="flex flex-wrap gap-2">
              <a
                :href="detailsCache[sig.resourceName]!.googleContactsUrl"
                target="_blank"
                rel="noopener"
                class="inline-flex items-center gap-1.5 px-2 py-1 text-[11px] rounded border border-neutral-700 bg-neutral-900 text-neutral-200 hover:border-primary-600 hover:text-primary-300"
              >
                <UIcon name="i-lucide-user" class="size-3.5" />
                Open in Google Contacts
                <UIcon name="i-lucide-external-link" class="size-3" />
              </a>
              <a
                v-if="detailsCache[sig.resourceName]!.linkedinSignal?.linkedin_url || sig.linkedinUrl"
                :href="detailsCache[sig.resourceName]!.linkedinSignal?.linkedin_url || sig.linkedinUrl!"
                target="_blank"
                rel="noopener"
                class="inline-flex items-center gap-1.5 px-2 py-1 text-[11px] rounded border border-neutral-700 bg-neutral-900 text-neutral-200 hover:border-blue-600 hover:text-blue-300"
              >
                <UIcon name="i-lucide-linkedin" class="size-3.5" />
                Open LinkedIn
                <UIcon name="i-lucide-external-link" class="size-3" />
              </a>
            </div>

            <!-- CRM / Score / Interaction grid -->
            <div class="grid md:grid-cols-2 gap-3 text-xs">
              <!-- CRM state -->
              <div class="border border-neutral-800 rounded-md p-2 bg-neutral-900/50">
                <div class="text-[10px] uppercase text-neutral-500 mb-1">CRM</div>
                <div v-if="detailsCache[sig.resourceName]!.crmState" class="space-y-1">
                  <div>
                    Stage:
                    <span class="text-neutral-200 font-medium">{{ detailsCache[sig.resourceName]!.crmState!.stage }}</span>
                    <span v-if="detailsCache[sig.resourceName]!.crmState!.stageChangedAt" class="text-neutral-600 ml-1">
                      ({{ formatDate(detailsCache[sig.resourceName]!.crmState!.stageChangedAt) }})
                    </span>
                  </div>
                  <div v-if="detailsCache[sig.resourceName]!.crmState!.tags?.length" class="flex flex-wrap gap-1">
                    <span
                      v-for="t in detailsCache[sig.resourceName]!.crmState!.tags"
                      :key="t"
                      class="px-1.5 py-0.5 rounded bg-neutral-800 text-[10px] text-neutral-300"
                    >{{ t }}</span>
                  </div>
                  <div v-if="detailsCache[sig.resourceName]!.crmState!.notes" class="text-neutral-400 whitespace-pre-wrap">
                    {{ detailsCache[sig.resourceName]!.crmState!.notes }}
                  </div>
                </div>
                <div v-else class="text-neutral-500 italic">Not in CRM yet</div>
              </div>

              <!-- FollowUp score breakdown -->
              <div class="border border-neutral-800 rounded-md p-2 bg-neutral-900/50">
                <div class="text-[10px] uppercase text-neutral-500 mb-1">FollowUp score</div>
                <div v-if="detailsCache[sig.resourceName]!.followupScore" class="space-y-0.5">
                  <div>
                    Total: <span class="text-primary-400 font-semibold">{{ detailsCache[sig.resourceName]!.followupScore!.score_total.toFixed(1) }}</span>
                  </div>
                  <div class="grid grid-cols-2 gap-x-2 text-neutral-400">
                    <div>Interaction: {{ detailsCache[sig.resourceName]!.followupScore!.score_breakdown.interaction.toFixed(1) }}</div>
                    <div>LinkedIn: {{ detailsCache[sig.resourceName]!.followupScore!.score_breakdown.linkedin.toFixed(1) }}</div>
                    <div>Completeness: {{ detailsCache[sig.resourceName]!.followupScore!.score_breakdown.completeness.toFixed(1) }}</div>
                    <div v-if="detailsCache[sig.resourceName]!.followupScore!.score_breakdown.exec_bonus">
                      Exec bonus: +{{ detailsCache[sig.resourceName]!.followupScore!.score_breakdown.exec_bonus!.toFixed(1) }}
                    </div>
                    <div v-if="detailsCache[sig.resourceName]!.followupScore!.score_breakdown.beeper">
                      Beeper: {{ detailsCache[sig.resourceName]!.followupScore!.score_breakdown.beeper!.toFixed(1) }}
                    </div>
                    <div v-if="(detailsCache[sig.resourceName]!.followupScore!.score_breakdown.personal_multiplier ?? 1) !== 1">
                      Personal ×{{ detailsCache[sig.resourceName]!.followupScore!.score_breakdown.personal_multiplier }}
                    </div>
                  </div>
                </div>
                <div v-else class="text-neutral-500 italic">No score record</div>
              </div>

              <!-- Interaction -->
              <div class="border border-neutral-800 rounded-md p-2 bg-neutral-900/50">
                <div class="text-[10px] uppercase text-neutral-500 mb-1">Interaction</div>
                <div v-if="detailsCache[sig.resourceName]!.followupScore?.interaction" class="space-y-0.5 text-neutral-300">
                  <div>Last: {{ formatDate(detailsCache[sig.resourceName]!.followupScore!.interaction.last_date) }}</div>
                  <div>Months gap: {{ detailsCache[sig.resourceName]!.followupScore!.interaction.months_gap }}</div>
                  <div>Count: {{ detailsCache[sig.resourceName]!.followupScore!.interaction.count }}</div>
                </div>
                <div v-else class="text-neutral-500 italic">No interaction history</div>
              </div>

              <!-- LinkedIn -->
              <div class="border border-neutral-800 rounded-md p-2 bg-neutral-900/50">
                <div class="text-[10px] uppercase text-neutral-500 mb-1">LinkedIn</div>
                <div v-if="detailsCache[sig.resourceName]!.linkedinSignal" class="space-y-0.5">
                  <div class="text-neutral-300 font-medium">{{ detailsCache[sig.resourceName]!.linkedinSignal!.current_role || detailsCache[sig.resourceName]!.linkedinSignal!.headline }}</div>
                  <div v-if="detailsCache[sig.resourceName]!.linkedinSignal!.signal_text" class="text-neutral-400 italic">
                    "{{ detailsCache[sig.resourceName]!.linkedinSignal!.signal_text }}"
                  </div>
                  <div class="text-neutral-500 text-[10px]">
                    Signal: {{ detailsCache[sig.resourceName]!.linkedinSignal!.signal_type }} · scanned {{ formatDate(detailsCache[sig.resourceName]!.linkedinSignal!.scanned_at) }}
                  </div>
                </div>
                <div v-else class="text-neutral-500 italic">No LinkedIn scan</div>
              </div>

              <!-- Beeper / omnichannel KPIs -->
              <div v-if="detailsCache[sig.resourceName]!.followupScore?.beeper" class="border border-neutral-800 rounded-md p-2 bg-neutral-900/50 md:col-span-2">
                <div class="text-[10px] uppercase text-neutral-500 mb-1">Omnichannel (Beeper)</div>
                <div class="flex flex-wrap gap-x-4 gap-y-1 text-neutral-300">
                  <div>Primary: {{ detailsCache[sig.resourceName]!.followupScore!.beeper!.channel_primary || '—' }}</div>
                  <div>Awaiting reply: {{ detailsCache[sig.resourceName]!.followupScore!.beeper!.awaiting_reply_side || '—' }}</div>
                  <div>30d in: {{ detailsCache[sig.resourceName]!.followupScore!.beeper!.messages_30d_in }}</div>
                  <div>30d out: {{ detailsCache[sig.resourceName]!.followupScore!.beeper!.messages_30d_out }}</div>
                  <div>Channels 30d: {{ detailsCache[sig.resourceName]!.followupScore!.beeper!.channels_30d }}</div>
                </div>
              </div>

              <!-- Contact completeness (emails, urls) -->
              <div v-if="detailsCache[sig.resourceName]!.followupScore?.contact" class="border border-neutral-800 rounded-md p-2 bg-neutral-900/50 md:col-span-2">
                <div class="text-[10px] uppercase text-neutral-500 mb-1">Contact fields</div>
                <div class="flex flex-wrap gap-x-4 gap-y-1 text-neutral-300">
                  <div v-if="detailsCache[sig.resourceName]!.followupScore!.contact.emails?.length">
                    Emails: <span class="font-mono text-[11px]">{{ detailsCache[sig.resourceName]!.followupScore!.contact.emails.join(', ') }}</span>
                  </div>
                  <div>Phone on file: {{ detailsCache[sig.resourceName]!.followupScore!.contact.has_phone ? 'yes' : 'no' }}</div>
                  <div>Completeness: {{ detailsCache[sig.resourceName]!.followupScore!.contact.completeness }}%</div>
                </div>
                <div v-if="detailsCache[sig.resourceName]!.followupScore!.contact.urls?.length" class="mt-1 flex flex-wrap gap-2 text-[11px]">
                  <a
                    v-for="u in detailsCache[sig.resourceName]!.followupScore!.contact.urls"
                    :key="u.url"
                    :href="u.url"
                    target="_blank"
                    rel="noopener"
                    class="text-neutral-400 hover:text-primary-400 underline-offset-2 hover:underline"
                  >
                    {{ u.type || 'link' }}
                  </a>
                </div>
              </div>

              <!-- Lead signal record (dismissal context) -->
              <div v-if="detailsCache[sig.resourceName]!.leadSignalRecord?.dismissal" class="border border-neutral-800 rounded-md p-2 bg-neutral-900/50 md:col-span-2">
                <div class="text-[10px] uppercase text-neutral-500 mb-1">Dismissal</div>
                <div class="text-neutral-300">
                  Reason: {{ detailsCache[sig.resourceName]!.leadSignalRecord!.dismissal!.reason.replace('_', ' ') }}
                  <span v-if="detailsCache[sig.resourceName]!.leadSignalRecord!.dismissal!.note" class="text-neutral-400">
                    — {{ detailsCache[sig.resourceName]!.leadSignalRecord!.dismissal!.note }}
                  </span>
                </div>
                <div class="text-neutral-500 text-[10px]">
                  {{ formatDate(detailsCache[sig.resourceName]!.leadSignalRecord!.dismissal!.dismissedAt) }}
                </div>
              </div>
            </div>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>
