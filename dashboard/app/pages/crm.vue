<script setup lang="ts">
import type { CRMContact, CRMResponse, CRMStage } from '~/server/utils/types'

useHead({
  title: 'CRM — Contact Refiner',
  meta: [
    { name: 'description', content: 'Personal CRM — manage reconnections, track contacts through your sales pipeline.' },
  ],
})

const { data, status, refresh } = useFetch<CRMResponse>('/api/crm')

const viewMode = ref<'kanban' | 'list'>('kanban')
const searchQuery = ref('')
const filterStage = ref<CRMStage | 'all'>('all')
const sortBy = ref<'score' | 'name' | 'gap'>('score')

// Selected contact for detail panel
const selectedContact = ref<CRMContact | null>(null)
const editNotes = ref('')
const editTags = ref('')
const isSaving = ref(false)
const isCopied = ref(false)
const toast = useToast()

const stageConfig: Array<{ stage: CRMStage; label: string; color: string }> = [
  { stage: 'inbox', label: 'Inbox', color: 'bg-blue-400' },
  { stage: 'reached_out', label: 'Reached Out', color: 'bg-amber-400' },
  { stage: 'in_conversation', label: 'In Conversation', color: 'bg-purple-400' },
  { stage: 'opportunity', label: 'Opportunity', color: 'bg-green-400' },
  { stage: 'converted', label: 'Converted', color: 'bg-cyan-400' },
  { stage: 'dormant', label: 'Dormant', color: 'bg-neutral-500' },
  { stage: 'unknown', label: 'Unknown', color: 'bg-neutral-700' },
  { stage: 'ready_to_delete', label: 'Ready to Delete', color: 'bg-red-500' },
]

const filteredContacts = computed(() => {
  let contacts = data.value?.contacts ?? []

  if (searchQuery.value) {
    const q = searchQuery.value.toLowerCase()
    contacts = contacts.filter(c =>
      c.name.toLowerCase().includes(q)
      || c.contact.org?.toLowerCase().includes(q)
      || c.linkedin?.signal_text?.toLowerCase().includes(q)
      || c.linkedin?.headline?.toLowerCase().includes(q),
    )
  }

  if (filterStage.value !== 'all') {
    contacts = contacts.filter(c => c.stage === filterStage.value)
  }

  if (sortBy.value === 'name') {
    contacts = [...contacts].sort((a, b) => a.name.localeCompare(b.name))
  } else if (sortBy.value === 'gap') {
    contacts = [...contacts].sort((a, b) => b.interaction.months_gap - a.interaction.months_gap)
  }
  // Default 'score' — already sorted by score_total desc from server

  return contacts
})

function contactsByStage(stage: CRMStage): CRMContact[] {
  return filteredContacts.value.filter(c => c.stage === stage)
}

async function handleDrop(resourceName: string, stage: CRMStage) {
  // Optimistic update
  const contact = data.value?.contacts.find(c => c.resourceName === resourceName)
  if (contact && contact.stage !== stage) {
    const oldStage = contact.stage
    contact.stage = stage
    try {
      await $fetch('/api/crm/update', { method: 'POST', body: { resourceName, stage } })
      // Refetch to auto-refill inbox with next top-scored contacts
      if (oldStage === 'inbox' || stage === 'inbox') refresh()
    } catch {
      contact.stage = oldStage
      toast.add({ title: 'Failed to move contact', color: 'error', icon: 'i-lucide-alert-triangle' })
    }
  }
}

async function handleReachOut(resourceName: string) {
  const contact = data.value?.contacts.find(c => c.resourceName === resourceName)
  if (!contact || contact.stage === 'reached_out') return
  const oldStage = contact.stage
  contact.stage = 'reached_out'
  try {
    await $fetch('/api/crm/update', { method: 'POST', body: { resourceName, stage: 'reached_out' } })
    if (oldStage === 'inbox') refresh()
  } catch {
    contact.stage = oldStage
    toast.add({ title: 'Failed to move contact', color: 'error', icon: 'i-lucide-alert-triangle' })
  }
}

async function copyPrompt() {
  if (!selectedContact.value?.followup_prompt) return
  try {
    await navigator.clipboard.writeText(selectedContact.value.followup_prompt)
    isCopied.value = true
    setTimeout(() => { isCopied.value = false }, 2000)
  } catch {
    toast.add({ title: 'Copy failed', color: 'error', icon: 'i-lucide-alert-triangle' })
  }
}

function selectContact(contact: CRMContact) {
  selectedContact.value = contact
  editNotes.value = contact.notes
  editTags.value = contact.tags.join(', ')
  isCopied.value = false
}

function closeDetail() {
  selectedContact.value = null
}

async function saveContact() {
  if (!selectedContact.value) return
  isSaving.value = true
  try {
    const tags = editTags.value.split(',').map(t => t.trim()).filter(Boolean)
    const result = await $fetch('/api/crm/update', {
      method: 'POST',
      body: {
        resourceName: selectedContact.value.resourceName,
        notes: editNotes.value,
        tags,
      },
    })
    // Server merges #hashtags from notes into tags — use the merged result
    const mergedTags = (result as { tags?: string[] }).tags ?? tags
    selectedContact.value.notes = editNotes.value
    selectedContact.value.tags = mergedTags
    editTags.value = mergedTags.join(', ')
    toast.add({ title: 'Contact saved', color: 'success', icon: 'i-lucide-check' })
  } catch {
    toast.add({ title: 'Failed to save contact', color: 'error', icon: 'i-lucide-alert-triangle' })
  } finally {
    isSaving.value = false
  }
}

async function moveContact(stage: CRMStage) {
  if (!selectedContact.value) return
  const oldStage = selectedContact.value.stage
  selectedContact.value.stage = stage
  try {
    await $fetch('/api/crm/update', {
      method: 'POST',
      body: { resourceName: selectedContact.value.resourceName, stage },
    })
    // Refetch to auto-refill inbox with next top-scored contacts
    if (oldStage === 'inbox' || stage === 'inbox') refresh()
  } catch {
    selectedContact.value.stage = oldStage
    toast.add({ title: 'Failed to move contact', color: 'error', icon: 'i-lucide-alert-triangle' })
  }
}

function signalColor(type: string | undefined): string {
  if (type === 'job_change') return 'text-green-400'
  if (type === 'active') return 'text-yellow-400'
  return 'text-neutral-500'
}
</script>

<template>
  <div class="space-y-4">
    <!-- Header -->
    <div class="flex items-center justify-between">
      <h1 class="text-xl font-bold text-neutral-100">CRM</h1>
      <div class="flex items-center gap-2">
        <!-- View toggle -->
        <div class="flex rounded-lg border border-neutral-800 overflow-hidden">
          <button
            class="px-3 py-1.5 text-xs transition-colors"
            :class="viewMode === 'kanban' ? 'bg-primary-500/20 text-primary-400' : 'text-neutral-500 hover:text-neutral-300'"
            @click="viewMode = 'kanban'"
          >
            <UIcon name="i-lucide-kanban" class="size-3.5" />
          </button>
          <button
            class="px-3 py-1.5 text-xs transition-colors"
            :class="viewMode === 'list' ? 'bg-primary-500/20 text-primary-400' : 'text-neutral-500 hover:text-neutral-300'"
            @click="viewMode = 'list'"
          >
            <UIcon name="i-lucide-list" class="size-3.5" />
          </button>
        </div>
      </div>
    </div>

    <!-- Stats -->
    <div v-if="data?.stages" class="flex gap-2 flex-wrap">
      <span
        v-for="sc in stageConfig"
        :key="sc.stage"
        class="text-[10px] px-2 py-1 rounded-lg border border-neutral-800 text-neutral-400 tabular-nums"
      >
        <span class="inline-block size-1.5 rounded-full mr-1" :class="sc.color" />
        {{ sc.label }}: {{ contactsByStage(sc.stage).length }}
      </span>
    </div>

    <!-- Filters -->
    <div class="flex items-center gap-3 flex-wrap">
      <input
        v-model="searchQuery"
        type="text"
        placeholder="Search contacts..."
        class="bg-neutral-900 border border-neutral-800 rounded-lg px-3 py-1.5 text-sm text-neutral-300 placeholder-neutral-600 w-64 focus:outline-none focus:border-neutral-700"
      />
      <select
        v-if="viewMode === 'list'"
        v-model="filterStage"
        class="bg-neutral-900 border border-neutral-800 rounded-lg px-3 py-1.5 text-sm text-neutral-300"
      >
        <option value="all">All stages</option>
        <option v-for="sc in stageConfig" :key="sc.stage" :value="sc.stage">{{ sc.label }}</option>
      </select>
      <select
        v-model="sortBy"
        class="bg-neutral-900 border border-neutral-800 rounded-lg px-3 py-1.5 text-sm text-neutral-300"
      >
        <option value="score">Score</option>
        <option value="name">Name</option>
        <option value="gap">Longest gap</option>
      </select>
      <span class="text-[10px] text-neutral-600 ml-auto">{{ filteredContacts.length }} contacts</span>
    </div>

    <!-- Loading -->
    <div v-if="status === 'pending'" class="text-center py-16">
      <UIcon name="i-lucide-loader" class="size-8 text-neutral-500 mx-auto mb-3 animate-spin" />
      <p class="text-neutral-500">Loading CRM data...</p>
    </div>

    <!-- Error -->
    <div v-else-if="status === 'error'" class="text-center py-16">
      <UIcon name="i-lucide-alert-triangle" class="size-8 text-red-500 mx-auto mb-3" />
      <p class="text-red-400">Failed to load data</p>
      <UButton label="Retry" size="sm" variant="soft" class="mt-3" @click="refresh()" />
    </div>

    <!-- Kanban View -->
    <div v-else-if="viewMode === 'kanban'" class="flex gap-3 overflow-x-auto pb-4">
      <CRMColumn
        v-for="sc in stageConfig"
        :key="sc.stage"
        :stage="sc.stage"
        :label="sc.label"
        :color="sc.color"
        :contacts="contactsByStage(sc.stage)"
        @drop="handleDrop"
        @select="selectContact"
        @reach-out="handleReachOut"
      />
    </div>

    <!-- List View -->
    <div v-else-if="filteredContacts.length" class="space-y-2">
      <div
        v-for="c in filteredContacts"
        :key="c.resourceName"
        class="flex items-center gap-4 px-4 py-3 rounded-xl border border-neutral-800 bg-neutral-900/50 hover:border-neutral-700 transition-colors cursor-pointer"
        @click="selectContact(c)"
      >
        <div class="min-w-0 flex-1">
          <p class="text-sm font-medium text-neutral-200 truncate">{{ c.name }}</p>
          <p v-if="c.contact.org" class="text-[11px] text-neutral-500 truncate">{{ c.contact.org }}</p>
        </div>
        <span v-if="c.linkedin" class="text-[10px] font-medium" :class="signalColor(c.linkedin.signal_type)">
          {{ c.linkedin.signal_type?.replace('_', ' ') }}
        </span>
        <span class="text-xs text-neutral-600 tabular-nums w-16 text-right">{{ c.interaction.months_gap }}mo</span>
        <span class="text-xs font-mono font-bold tabular-nums w-10 text-right" :class="c.score_total >= 100 ? 'text-cyan-400' : 'text-neutral-500'">{{ c.score_total }}</span>
        <span class="text-[10px] px-2 py-0.5 rounded border border-neutral-700 text-neutral-400">{{ stageConfig.find(s => s.stage === c.stage)?.label }}</span>
      </div>
    </div>

    <p v-else class="text-sm text-neutral-600 text-center py-12">
      No contacts match your filters.
    </p>

    <!-- Contact Detail Slide-over -->
    <Teleport to="body">
      <div v-if="selectedContact" class="fixed inset-0 z-50 flex justify-end">
        <div class="absolute inset-0 bg-black/60" @click="closeDetail" />
        <div class="relative w-full max-w-md bg-neutral-950 border-l border-neutral-800 overflow-y-auto">
          <div class="p-5 space-y-5">
            <!-- Header -->
            <div class="flex items-start justify-between">
              <div>
                <h2 class="text-lg font-bold text-neutral-100">{{ selectedContact.name }}</h2>
                <p v-if="selectedContact.contact.title" class="text-sm text-neutral-400">{{ selectedContact.contact.title }}</p>
                <p v-if="selectedContact.contact.org" class="text-sm text-neutral-500">{{ selectedContact.contact.org }}</p>
              </div>
              <button class="text-neutral-500 hover:text-neutral-300 p-1" @click="closeDetail">
                <UIcon name="i-lucide-x" class="size-5" />
              </button>
            </div>

            <!-- Score + breakdown -->
            <div class="space-y-1.5">
              <div class="flex gap-4 text-sm">
                <div>
                  <span class="text-neutral-500">Score:</span>
                  <span class="font-bold ml-1" :class="selectedContact.score_total >= 100 ? 'text-cyan-400' : 'text-neutral-300'">{{ selectedContact.score_total }}</span>
                </div>
                <div>
                  <span class="text-neutral-500">Gap:</span>
                  <span class="text-neutral-300 ml-1">{{ selectedContact.interaction.months_gap }}mo</span>
                </div>
                <div v-if="selectedContact.linkedin">
                  <span class="text-neutral-500">LinkedIn:</span>
                  <span class="ml-1" :class="signalColor(selectedContact.linkedin.signal_type)">{{ selectedContact.linkedin.signal_type?.replace('_', ' ') }}</span>
                </div>
              </div>
              <div class="flex gap-3 text-[10px] text-neutral-600">
                <span>Interaction: {{ selectedContact.score_breakdown.interaction }}</span>
                <span>LinkedIn: {{ selectedContact.score_breakdown.linkedin }}</span>
                <span>Completeness: {{ selectedContact.score_breakdown.completeness }}</span>
              </div>
            </div>

            <!-- Interaction details -->
            <div v-if="selectedContact.interaction.last_date || selectedContact.linkedin?.current_role" class="space-y-1">
              <p v-if="selectedContact.interaction.last_date" class="text-xs text-neutral-500">
                Last contact: <span class="text-neutral-400">{{ selectedContact.interaction.last_date }}</span>
              </p>
              <p v-if="selectedContact.linkedin?.current_role" class="text-xs text-neutral-500">
                Current role: <span class="text-neutral-400">{{ selectedContact.linkedin.current_role }}</span>
              </p>
            </div>

            <!-- Emails -->
            <div v-if="selectedContact.contact.emails?.length">
              <p class="text-xs text-neutral-500 mb-1">Email</p>
              <div class="flex flex-col gap-0.5">
                <a
                  v-for="email in selectedContact.contact.emails"
                  :key="email"
                  :href="`mailto:${email}`"
                  class="text-xs text-primary-400 hover:text-primary-300 truncate"
                >{{ email }}</a>
              </div>
            </div>

            <!-- Stage selector -->
            <div>
              <p class="text-xs text-neutral-500 mb-2">Stage</p>
              <div class="flex gap-1.5 flex-wrap">
                <button
                  v-for="sc in stageConfig"
                  :key="sc.stage"
                  class="px-2.5 py-1 text-xs rounded-lg border transition-colors"
                  :class="selectedContact.stage === sc.stage ? 'border-primary-500/50 bg-primary-500/20 text-primary-400' : 'border-neutral-800 text-neutral-500 hover:text-neutral-300 hover:border-neutral-700'"
                  @click="moveContact(sc.stage)"
                >
                  {{ sc.label }}
                </button>
              </div>
            </div>

            <!-- FollowUp prompt -->
            <div v-if="selectedContact.followup_prompt">
              <div class="flex items-center justify-between mb-2">
                <p class="text-xs text-neutral-500">AI Reconnect Suggestion</p>
                <button
                  class="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] rounded border transition-colors"
                  :class="isCopied ? 'border-green-700 text-green-400 bg-green-500/10' : 'border-neutral-800 text-neutral-500 hover:text-neutral-300 hover:border-neutral-700'"
                  @click="copyPrompt"
                >
                  <UIcon :name="isCopied ? 'i-lucide-check' : 'i-lucide-copy'" class="size-3" />
                  {{ isCopied ? 'Copied' : 'Copy' }}
                </button>
              </div>
              <p class="text-sm text-neutral-300 italic bg-neutral-900 border border-neutral-800 rounded-lg p-3 select-all">
                {{ selectedContact.followup_prompt }}
              </p>
            </div>

            <!-- LinkedIn signal detail -->
            <div v-if="selectedContact.linkedin?.signal_text">
              <p class="text-xs text-neutral-500 mb-2">LinkedIn Signal</p>
              <p class="text-sm text-neutral-400">{{ selectedContact.linkedin.signal_text }}</p>
              <p v-if="selectedContact.linkedin.headline" class="text-xs text-neutral-600 mt-1">{{ selectedContact.linkedin.headline }}</p>
            </div>

            <!-- Notes -->
            <div>
              <p class="text-xs text-neutral-500 mb-2">Notes</p>
              <textarea
                v-model="editNotes"
                rows="4"
                class="w-full bg-neutral-900 border border-neutral-800 rounded-lg px-3 py-2 text-sm text-neutral-300 placeholder-neutral-600 focus:outline-none focus:border-neutral-700 resize-y"
                placeholder="Add notes about this contact..."
              />
            </div>

            <!-- Tags -->
            <div>
              <p class="text-xs text-neutral-500 mb-2">Tags (comma-separated)</p>
              <input
                v-model="editTags"
                type="text"
                class="w-full bg-neutral-900 border border-neutral-800 rounded-lg px-3 py-2 text-sm text-neutral-300 placeholder-neutral-600 focus:outline-none focus:border-neutral-700"
                placeholder="e.g. Instarea, Sales, Priority"
              />
            </div>

            <!-- Actions -->
            <div class="flex gap-2">
              <UButton
                label="Save"
                icon="i-lucide-save"
                size="sm"
                :loading="isSaving"
                @click="saveContact"
              />
              <a
                v-if="selectedContact.linkedin?.url || selectedContact.contact.urls?.find(u => u.url?.includes('linkedin'))"
                :href="selectedContact.linkedin?.url || selectedContact.contact.urls?.find(u => u.url?.includes('linkedin'))?.url"
                target="_blank"
                class="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs text-neutral-400 border border-neutral-800 rounded-lg hover:text-neutral-200 hover:border-neutral-700 transition-colors"
              >
                <UIcon name="i-lucide-linkedin" class="size-3.5" />
                LinkedIn
              </a>
              <a
                :href="`https://contacts.google.com/person/${selectedContact.resourceName.replace('people/', '')}`"
                target="_blank"
                class="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs text-neutral-400 border border-neutral-800 rounded-lg hover:text-neutral-200 hover:border-neutral-700 transition-colors"
              >
                <UIcon name="i-lucide-contact" class="size-3.5" />
                Google
              </a>
            </div>

            <!-- Contact completeness -->
            <div>
              <p class="text-xs text-neutral-500 mb-2">Contact Completeness</p>
              <div class="flex gap-3 text-[10px]">
                <span :class="selectedContact.contact.has_email ? 'text-green-400' : 'text-neutral-700'">Email</span>
                <span :class="selectedContact.contact.has_phone ? 'text-green-400' : 'text-neutral-700'">Phone</span>
                <span :class="selectedContact.contact.has_org ? 'text-green-400' : 'text-neutral-700'">Org</span>
                <span :class="selectedContact.contact.has_linkedin_url ? 'text-green-400' : 'text-neutral-700'">LinkedIn</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
