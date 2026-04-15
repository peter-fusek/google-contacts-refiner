<script setup lang="ts">
import type { LICRMResponse, LIContact, LIInstitutionTier } from '~/server/utils/types'

useHead({
  title: 'LinkedIn CRM — Contact Refiner',
  meta: [{ name: 'description', content: 'LinkedIn outreach pipeline — contacts, DMs, posts, and institutions' }],
})

const { data, status, refresh } = useFetch<LICRMResponse>('/api/linkedin-crm')

const activeTab = ref(0)
const tabs = [
  { label: 'Pipeline', icon: 'i-lucide-kanban' },
  { label: 'Posts & Mining', icon: 'i-lucide-bar-chart-3' },
  { label: 'DM Outreach', icon: 'i-lucide-send' },
  { label: 'Institutions', icon: 'i-lucide-building-2' },
]

const search = ref('')
const selectedContact = ref<LIContact | null>(null)

// Pipeline tab — group contacts by tier
const tierGroups = computed(() => {
  if (!data.value) return { T0: [], T1: [], T2: [], T3: [] }
  const contacts = data.value.data.contacts.filter(c =>
    !search.value || c.name.toLowerCase().includes(search.value.toLowerCase()) || c.role.toLowerCase().includes(search.value.toLowerCase()),
  )
  return {
    T0: contacts.filter(c => c.tier === 'T0'),
    T1: contacts.filter(c => c.tier === 'T1'),
    T2: contacts.filter(c => c.tier === 'T2'),
    T3: contacts.filter(c => c.tier === 'T3'),
  }
})

// DM tab — stats
const dmStats = computed(() => {
  if (!data.value) return { sent: 0, skipped: 0, responseRate: '0%', followUpsDue: 0 }
  const log = data.value.data.dmLog
  const sent = log.filter(d => d.status === 'SENT').length
  const skipped = log.filter(d => d.status === 'SKIPPED').length
  const responded = data.value.data.contacts.filter(c => c.status === 'RESPONDED').length
  const today = new Date()
  const followUpsDue = log.filter(d => d.followUpDate && new Date(d.followUpDate) <= today && d.status === 'SENT' && !d.response).length
  return {
    sent,
    skipped,
    responseRate: sent > 0 ? `${Math.round((responded / sent) * 100)}%` : '0%',
    followUpsDue,
  }
})

// Institutions tab — group by tier
const institutionGroups = computed(() => {
  if (!data.value) return { A: [], B: [], C: [] }
  const insts = data.value.data.institutions
  return {
    A: insts.filter(i => i.tier === 'A'),
    B: insts.filter(i => i.tier === 'B'),
    C: insts.filter(i => i.tier === 'C'),
  }
})

const institutionTierLabels: Record<LIInstitutionTier, string> = {
  A: 'Primary Targets',
  B: 'Secondary Targets',
  C: 'Ecosystem Players',
}

const institutionTierColors: Record<LIInstitutionTier, string> = {
  A: 'text-green-400',
  B: 'text-amber-400',
  C: 'text-neutral-400',
}

function categoryLabel(cat: string): string {
  return cat.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
}

function selectContact(contact: LIContact) {
  selectedContact.value = contact
}
</script>

<template>
  <div>
    <!-- Header -->
    <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-6">
      <div>
        <h1 class="text-xl font-bold text-neutral-100">LinkedIn CRM</h1>
        <p class="text-sm text-neutral-500">Oncofiles outreach pipeline</p>
      </div>
      <div class="flex items-center gap-2">
        <UInput
          v-model="search"
          placeholder="Search contacts..."
          icon="i-lucide-search"
          size="sm"
          class="w-48"
        />
        <button
          class="p-2 rounded-lg text-neutral-500 hover:text-neutral-300 hover:bg-neutral-800 transition-colors"
          title="Refresh"
          @click="refresh()"
        >
          <UIcon name="i-lucide-refresh-cw" class="size-4" />
        </button>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="status === 'pending'" class="flex items-center justify-center py-20">
      <UIcon name="i-lucide-loader-2" class="size-6 text-primary-400 animate-spin" />
    </div>

    <!-- Error -->
    <div v-else-if="status === 'error'" class="text-center py-20">
      <p class="text-red-400 mb-2">Failed to load LinkedIn CRM data</p>
      <button class="text-sm text-primary-400 hover:text-primary-300" @click="refresh()">Retry</button>
    </div>

    <!-- Content -->
    <template v-else-if="data">
      <!-- Metrics bar -->
      <LIMetricsBar :stats="data.stats" class="mb-6" />

      <!-- Tabs -->
      <div class="flex items-center gap-1 mb-6 border-b border-neutral-800 pb-px overflow-x-auto">
        <button
          v-for="(tab, idx) in tabs"
          :key="tab.label"
          class="flex items-center gap-1.5 px-3 py-2 text-sm rounded-t-lg transition-colors whitespace-nowrap"
          :class="activeTab === idx ? 'text-primary-400 bg-primary-500/10 border-b-2 border-primary-400' : 'text-neutral-500 hover:text-neutral-300'"
          @click="activeTab = idx"
        >
          <UIcon :name="tab.icon" class="size-4" />
          {{ tab.label }}
        </button>
      </div>

      <!-- Tab 1: Pipeline -->
      <div v-if="activeTab === 0" class="flex gap-4 overflow-x-auto pb-4">
        <LITierColumn
          tier="T0"
          label="T0 · Inner Circle"
          :contacts="tierGroups.T0"
          color="bg-primary-500/10 text-primary-400"
          @select="selectContact"
        />
        <LITierColumn
          tier="T1"
          label="T1 · Domain Experts"
          :contacts="tierGroups.T1"
          color="bg-cyan-500/10 text-cyan-400"
          @select="selectContact"
        />
        <LITierColumn
          tier="T2"
          label="T2 · Institutional"
          :contacts="tierGroups.T2"
          color="bg-amber-500/10 text-amber-400"
          @select="selectContact"
        />
        <LITierColumn
          tier="T3"
          label="T3 · Amplifiers"
          :contacts="tierGroups.T3"
          color="bg-neutral-700/30 text-neutral-400"
          @select="selectContact"
        />
      </div>

      <!-- Tab 2: Posts & Mining -->
      <div v-if="activeTab === 1" class="space-y-6">
        <!-- Content performance -->
        <div class="rounded-xl border border-neutral-800 bg-neutral-900/50 p-5 scanlines">
          <h3 class="label-refined mb-4">Content Performance</h3>
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="text-left text-neutral-500 border-b border-neutral-800">
                  <th class="pb-2 pr-4 font-medium">Date</th>
                  <th class="pb-2 pr-4 font-medium">Post</th>
                  <th class="pb-2 pr-4 font-medium">Lang</th>
                  <th class="pb-2 pr-4 font-medium text-right">Reactions</th>
                  <th class="pb-2 pr-4 font-medium text-right">Impressions</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="post in data.data.posts" :key="post.activityUrn" class="border-b border-neutral-800/50 hover:bg-neutral-800/20">
                  <td class="py-2 pr-4 tabular-nums text-neutral-400">{{ post.date }}</td>
                  <td class="py-2 pr-4 text-neutral-200">{{ post.description }}</td>
                  <td class="py-2 pr-4">
                    <span class="px-1.5 py-0.5 rounded text-[10px] font-medium" :class="post.language === 'SK' ? 'text-blue-400 bg-blue-500/15' : 'text-purple-400 bg-purple-500/15'">
                      {{ post.language }}
                    </span>
                  </td>
                  <td class="py-2 pr-4 text-right tabular-nums text-neutral-200">{{ post.reactions }}</td>
                  <td class="py-2 pr-4 text-right tabular-nums text-neutral-400">{{ post.impressions?.toLocaleString() ?? '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Mining runs -->
        <div class="rounded-xl border border-neutral-800 bg-neutral-900/50 p-5 scanlines">
          <h3 class="label-refined mb-4">Connection Mining Runs</h3>
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="text-left text-neutral-500 border-b border-neutral-800">
                  <th class="pb-2 pr-4 font-medium">Date</th>
                  <th class="pb-2 pr-4 font-medium">Run</th>
                  <th class="pb-2 pr-4 font-medium">Post</th>
                  <th class="pb-2 pr-4 font-medium text-right">Reactions</th>
                  <th class="pb-2 pr-4 font-medium text-right">Non-1st</th>
                  <th class="pb-2 pr-4 font-medium text-right">Sent</th>
                  <th class="pb-2 pr-4 font-medium text-right">Rate</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="run in data.data.miningRuns" :key="run.run" class="border-b border-neutral-800/50 hover:bg-neutral-800/20">
                  <td class="py-2 pr-4 tabular-nums text-neutral-400">{{ run.date }}</td>
                  <td class="py-2 pr-4 text-neutral-200">#{{ run.run }}</td>
                  <td class="py-2 pr-4 text-neutral-300">{{ run.post }}</td>
                  <td class="py-2 pr-4 text-right tabular-nums text-neutral-200">{{ run.reactions || '—' }}</td>
                  <td class="py-2 pr-4 text-right tabular-nums text-neutral-200">{{ run.nonFirst }}</td>
                  <td class="py-2 pr-4 text-right tabular-nums text-cyan-400">{{ run.sent }}</td>
                  <td class="py-2 pr-4 text-right tabular-nums text-neutral-400">{{ run.rate ?? '—' }}</td>
                </tr>
              </tbody>
              <tfoot>
                <tr class="border-t border-neutral-700 font-medium">
                  <td colspan="5" class="py-2 pr-4 text-neutral-400">Total</td>
                  <td class="py-2 pr-4 text-right tabular-nums text-cyan-400">{{ data.data.miningRuns.reduce((s, r) => s + r.sent, 0) }}</td>
                  <td class="py-2 pr-4 text-right tabular-nums text-green-400">~88%</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </div>

        <!-- Follower growth -->
        <div class="rounded-xl border border-neutral-800 bg-neutral-900/50 p-5 scanlines">
          <h3 class="label-refined mb-4">Follower Growth</h3>
          <div class="flex items-end gap-1 h-32 px-2">
            <div
              v-for="snap in data.data.followerSnapshots"
              :key="snap.date"
              class="flex-1 flex flex-col items-center gap-1"
            >
              <span class="text-[9px] tabular-nums text-neutral-500">{{ snap.followers.toLocaleString() }}</span>
              <div
                class="w-full bg-primary-500/30 rounded-t"
                :style="{ height: `${Math.max(8, ((snap.followers - 18500) / 150) * 100)}%` }"
              />
              <span class="text-[9px] tabular-nums text-neutral-600">{{ snap.date.slice(5) }}</span>
            </div>
          </div>
        </div>

        <!-- Milestones -->
        <div class="rounded-xl border border-neutral-800 bg-neutral-900/50 p-5 scanlines">
          <h3 class="label-refined mb-4">Milestones</h3>
          <div class="space-y-2">
            <div
              v-for="m in data.data.milestones"
              :key="m.name"
              class="flex items-center gap-3 text-sm"
            >
              <span
                class="inline-flex items-center justify-center size-5 rounded-full text-[10px] font-bold"
                :class="{
                  'bg-green-500/20 text-green-400': m.status === 'DONE' || m.status === 'EXCEEDED',
                  'bg-amber-500/20 text-amber-400': m.status === 'IN_PROGRESS',
                  'bg-neutral-800 text-neutral-500': m.status === 'TODO',
                }"
              >
                <UIcon v-if="m.status === 'DONE' || m.status === 'EXCEEDED'" name="i-lucide-check" class="size-3" />
                <UIcon v-else-if="m.status === 'TODO'" name="i-lucide-circle" class="size-3" />
                <UIcon v-else name="i-lucide-loader-2" class="size-3" />
              </span>
              <span class="text-neutral-200 flex-1">{{ m.name }}</span>
              <span class="text-neutral-500 tabular-nums text-xs">{{ m.targetDate }}</span>
              <span class="text-neutral-400 text-xs">{{ m.metric }}</span>
              <span
                v-if="m.status === 'EXCEEDED'"
                class="text-[10px] font-medium text-green-400 bg-green-500/15 px-1.5 py-0.5 rounded"
              >EXCEEDED</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Tab 3: DM Outreach -->
      <div v-if="activeTab === 2" class="space-y-6">
        <!-- DM stats -->
        <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <StatsCard label="DMs Sent" :value="dmStats.sent" icon="i-lucide-send" color="cyan" />
          <StatsCard label="Skipped" :value="dmStats.skipped" icon="i-lucide-x-circle" />
          <StatsCard label="Response Rate" :value="dmStats.responseRate" icon="i-lucide-message-circle" color="green" />
          <StatsCard
            label="Follow-ups Due"
            :value="dmStats.followUpsDue"
            icon="i-lucide-alert-circle"
            :color="dmStats.followUpsDue > 0 ? 'amber' : undefined"
          />
        </div>

        <!-- DM log -->
        <div class="rounded-xl border border-neutral-800 bg-neutral-900/50 p-5 scanlines">
          <h3 class="label-refined mb-4">DM Log</h3>
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="text-left text-neutral-500 border-b border-neutral-800">
                  <th class="pb-2 pr-4 font-medium">Date</th>
                  <th class="pb-2 pr-4 font-medium">Contact</th>
                  <th class="pb-2 pr-4 font-medium">Tier</th>
                  <th class="pb-2 pr-4 font-medium">Template</th>
                  <th class="pb-2 pr-4 font-medium">Status</th>
                  <th class="pb-2 pr-4 font-medium">Follow-up</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(dm, idx) in data.data.dmLog"
                  :key="idx"
                  class="border-b border-neutral-800/50 hover:bg-neutral-800/20"
                  :class="{ 'bg-amber-500/5': dm.followUpDate && new Date(dm.followUpDate) <= new Date() && dm.status === 'SENT' && !dm.response }"
                >
                  <td class="py-2 pr-4 tabular-nums text-neutral-400">{{ dm.date }}</td>
                  <td class="py-2 pr-4 text-neutral-200">{{ dm.contactName }}</td>
                  <td class="py-2 pr-4">
                    <span class="text-[10px] font-mono font-bold text-neutral-500">{{ dm.tier }}</span>
                  </td>
                  <td class="py-2 pr-4 text-neutral-400 text-xs">{{ dm.template }}</td>
                  <td class="py-2 pr-4">
                    <span
                      class="inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium"
                      :class="dm.status === 'SENT' ? 'text-cyan-400 bg-cyan-500/15' : 'text-neutral-500 bg-neutral-800'"
                    >
                      {{ dm.status }}
                    </span>
                  </td>
                  <td class="py-2 pr-4 tabular-nums text-neutral-500 text-xs">
                    <span v-if="dm.skipReason" class="text-red-400/60 italic">{{ dm.skipReason }}</span>
                    <span v-else-if="dm.followUpDate">{{ dm.followUpDate }}</span>
                    <span v-else>—</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- Tab 4: Institutions -->
      <div v-if="activeTab === 3" class="space-y-6">
        <div
          v-for="tierKey in (['A', 'B', 'C'] as LIInstitutionTier[])"
          :key="tierKey"
          class="rounded-xl border border-neutral-800 bg-neutral-900/50 p-5 scanlines"
        >
          <div class="flex items-center gap-2 mb-4">
            <span class="label-refined" :class="institutionTierColors[tierKey]">
              Tier {{ tierKey }} — {{ institutionTierLabels[tierKey] }}
            </span>
            <span class="text-[10px] text-neutral-600 tabular-nums">({{ institutionGroups[tierKey].length }})</span>
          </div>
          <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <div
              v-for="inst in institutionGroups[tierKey]"
              :key="inst.id"
              class="bg-neutral-900 border border-neutral-800 rounded-lg p-3 hover:border-neutral-700 transition-colors"
            >
              <div class="flex items-start justify-between gap-2 mb-1">
                <p class="text-sm font-medium text-neutral-200">{{ inst.name }}</p>
                <span class="text-[10px] px-1.5 py-0.5 rounded font-medium" :class="inst.status === 'TODO' ? 'text-neutral-500 bg-neutral-800' : 'text-green-400 bg-green-500/15'">
                  {{ inst.status }}
                </span>
              </div>
              <p v-if="inst.city" class="text-[11px] text-neutral-500 mb-1">{{ inst.city }}</p>
              <span class="inline-flex text-[10px] text-neutral-500 bg-neutral-800 px-1.5 py-0.5 rounded mb-1.5">
                {{ categoryLabel(inst.category) }}
              </span>
              <p v-if="inst.contactStrategy" class="text-[10px] text-neutral-500 mt-1">{{ inst.contactStrategy }}</p>
              <p v-if="inst.notes" class="text-[10px] text-neutral-600 italic mt-0.5">{{ inst.notes }}</p>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- Contact detail panel -->
    <div v-if="selectedContact" class="fixed inset-0 z-50 flex justify-end">
      <div class="absolute inset-0 bg-black/60" @click="selectedContact = null" />
      <div class="relative w-full max-w-sm bg-neutral-950 border-l border-neutral-800 overflow-y-auto p-6 space-y-4">
        <div class="flex items-start justify-between">
          <div>
            <h2 class="text-lg font-bold text-neutral-100">{{ selectedContact.name }}</h2>
            <p class="text-sm text-neutral-400">{{ selectedContact.role }}</p>
          </div>
          <button class="p-1 text-neutral-500 hover:text-neutral-300" @click="selectedContact = null">
            <UIcon name="i-lucide-x" class="size-4" />
          </button>
        </div>
        <div class="space-y-3 text-sm">
          <div class="flex items-center justify-between">
            <span class="text-neutral-500">Tier</span>
            <span class="font-mono font-bold text-neutral-300">{{ selectedContact.tier }}</span>
          </div>
          <div class="flex items-center justify-between">
            <span class="text-neutral-500">Status</span>
            <span class="text-neutral-300">{{ selectedContact.status.replace(/_/g, ' ') }}</span>
          </div>
          <div class="flex items-center justify-between">
            <span class="text-neutral-500">Source</span>
            <span class="text-neutral-300">{{ selectedContact.source }}</span>
          </div>
          <div v-if="selectedContact.dmSentDate" class="flex items-center justify-between">
            <span class="text-neutral-500">DM Sent</span>
            <span class="text-neutral-300 tabular-nums">{{ selectedContact.dmSentDate }}</span>
          </div>
          <div v-if="selectedContact.dmTemplate" class="flex items-center justify-between">
            <span class="text-neutral-500">Template</span>
            <span class="text-neutral-300">{{ selectedContact.dmTemplate }}</span>
          </div>
          <div v-if="selectedContact.skipReason" class="flex items-center justify-between">
            <span class="text-neutral-500">Skip Reason</span>
            <span class="text-red-400">{{ selectedContact.skipReason }}</span>
          </div>
          <div v-if="selectedContact.notes">
            <span class="text-neutral-500 block mb-1">Notes</span>
            <p class="text-neutral-300 text-xs bg-neutral-800/50 rounded p-2">{{ selectedContact.notes }}</p>
          </div>
          <div v-if="selectedContact.linkedinUrl">
            <a
              :href="selectedContact.linkedinUrl"
              target="_blank"
              class="inline-flex items-center gap-1.5 text-blue-400 hover:text-blue-300 text-sm"
            >
              <UIcon name="i-lucide-external-link" class="size-3.5" />
              LinkedIn Profile
            </a>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
