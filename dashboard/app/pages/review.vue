<script setup lang="ts">
import type { ReviewChange, ReviewDecision, ReviewSession } from '~/server/utils/types'

const { loggedIn } = useUserSession()
const isDemo = computed(() => !loggedIn.value)

// Fetch review data
const { data, status, refresh } = useFetch('/api/review')

// Queue stats trend
const { data: queueStats } = useFetch('/api/queue-stats')
const showStats = ref(false)

// Session history + last export
const { data: sessionsData } = useFetch('/api/review/sessions')
const showHistory = ref(false)

// Session state
const sessionId = ref('')
const decisions = ref<Record<string, ReviewDecision>>({})
const sessionStats = ref({ total: 0, approved: 0, rejected: 0, edited: 0, skipped: 0 })

// Filters
const fieldFilter = ref('__all__')
const categoryFilter = ref('__all__')
const hideDecided = ref(false)

// View mode — default to rule for faster bulk review
const viewMode = ref<'contact' | 'rule'>('rule')

// Expanded state for rule groups (collapsed by default — show 5 samples)
const expandedRules = reactive<Record<string, boolean>>({})

// Pagination
const PAGE_SIZE = 30
const currentPage = ref(0)

// Focused contact index (relative to current page)
const focusedIndex = ref(0)

// Saving state
const isSaving = ref(false)
const lastSaved = ref<string | null>(null)
const exportMessage = ref<string | null>(null)
const saveError = ref<string | null>(null)
let autoSaveTimer: ReturnType<typeof setTimeout> | undefined

// Initialize session
watch(data, (d) => {
  if (!d) return
  if (d.session) {
    sessionId.value = d.session.id
    decisions.value = { ...d.session.decisions }
    sessionStats.value = { ...d.session.stats }
  } else if (!sessionId.value) {
    sessionId.value = `review_${Date.now().toString(36)}`
  }

  // Restore from localStorage
  const saved = localStorage.getItem(`review_${sessionId.value}`)
  if (saved) {
    try {
      const parsed = JSON.parse(saved)
      if (Object.keys(parsed.decisions || {}).length > Object.keys(decisions.value).length) {
        decisions.value = parsed.decisions
        recomputeStats()
      }
    } catch { /* ignore */ }
  }
}, { immediate: true })

// All changes from API
const allChanges = computed(() => data.value?.changes ?? [])

// Filtered changes
const filteredChanges = computed(() => {
  let changes = allChanges.value
  if (fieldFilter.value && fieldFilter.value !== '__all__') {
    changes = changes.filter(c => c.field.startsWith(fieldFilter.value))
  }
  if (categoryFilter.value && categoryFilter.value !== '__all__') {
    changes = changes.filter(c => c.ruleCategory === categoryFilter.value)
  }
  if (hideDecided.value) {
    changes = changes.filter(c => !decisions.value[c.id])
  }
  return changes
})

// Group by contact (all groups — for stats)
const contactGroups = computed(() => {
  const map = new Map<string, { displayName: string; resourceName: string; changes: ReviewChange[] }>()
  for (const c of filteredChanges.value) {
    let group = map.get(c.resourceName)
    if (!group) {
      group = { displayName: c.displayName, resourceName: c.resourceName, changes: [] }
      map.set(c.resourceName, group)
    }
    group.changes.push(c)
  }
  return [...map.values()]
})

// Paginated contact groups — only render this subset
const totalPages = computed(() => Math.max(1, Math.ceil(contactGroups.value.length / PAGE_SIZE)))

const paginatedGroups = computed(() => {
  const start = currentPage.value * PAGE_SIZE
  return contactGroups.value.slice(start, start + PAGE_SIZE)
})

// Reset page when filters change
watch([fieldFilter, categoryFilter, hideDecided], () => {
  currentPage.value = 0
  focusedIndex.value = 0
})

// Group by rule category
const ruleGroups = computed(() => {
  const map = new Map<string, ReviewChange[]>()
  for (const c of filteredChanges.value) {
    let group = map.get(c.ruleCategory)
    if (!group) {
      group = []
      map.set(c.ruleCategory, group)
    }
    group.push(c)
  }
  return [...map.entries()].sort((a, b) => b[1].length - a[1].length)
})

// Filter options
const fieldOptions = computed(() => {
  const fields = data.value?.stats?.byField ?? {}
  return [
    { label: 'All fields', value: '__all__' },
    ...Object.entries(fields)
      .filter(([k]) => k !== '')
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => ({ label: `${formatFieldName(k)} (${v})`, value: k })),
  ]
})

const categoryOptions = computed(() => {
  const cats = data.value?.stats?.byCategory ?? {}
  return [
    { label: 'All rules', value: '__all__' },
    ...Object.entries(cats)
      .filter(([k]) => k !== '')
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => ({ label: `${k} (${v})`, value: k })),
  ]
})

// Rule view helpers
function ruleUndecided(changes: ReviewChange[]): number {
  return changes.filter(c => !decisions.value[c.id]).length
}

function ruleDecidedCount(changes: ReviewChange[]): number {
  return changes.filter(c => decisions.value[c.id]).length
}

function ruleApprovalRate(changes: ReviewChange[]): number | null {
  const decided = changes.filter(c => decisions.value[c.id])
  if (decided.length < 1) return null
  const approved = decided.filter(c => {
    const d = decisions.value[c.id]
    return d?.decision === 'approved' || d?.decision === 'edited'
  }).length
  return Math.round((approved / decided.length) * 100)
}

function ruleSamples(changes: ReviewChange[]): ReviewChange[] {
  // Show first 5 undecided, or first 5 overall if all decided
  const undecided = changes.filter(c => !decisions.value[c.id])
  return (undecided.length > 0 ? undecided : changes).slice(0, 5)
}

function formatFieldShort(field: string): string {
  return field
    .replace('phoneNumbers', 'phones')
    .replace('emailAddresses', 'emails')
    .replace('.value', '')
    .replace('.formattedValue', '')
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  } catch { return iso }
}

function formatFieldName(field: string): string {
  return field
    .replace('phoneNumbers', 'phones')
    .replace('emailAddresses', 'emails')
    .replace('names', 'names')
    .replace('organizations', 'orgs')
    .replace('addresses', 'addr')
}

function decide(changeId: string, decision: ReviewDecision['decision'], editedValue?: string) {
  decisions.value[changeId] = {
    changeId,
    decision,
    editedValue,
    decidedAt: new Date().toISOString(),
  }
  recomputeStats()
  saveToLocalStorage()
  scheduleAutoSave()
}

function decideAllForContact(resourceName: string, decision: 'approved' | 'rejected') {
  const changes = allChanges.value.filter(c => c.resourceName === resourceName)
  for (const c of changes) {
    decisions.value[c.id] = {
      changeId: c.id,
      decision,
      decidedAt: new Date().toISOString(),
    }
  }
  recomputeStats()
  saveToLocalStorage()
  scheduleAutoSave()
}

function undoDecision(changeId: string) {
  delete decisions.value[changeId]
  recomputeStats()
  saveToLocalStorage()
}

function bulkDecide(decision: 'approved' | 'rejected') {
  for (const c of filteredChanges.value) {
    if (!decisions.value[c.id]) {
      decisions.value[c.id] = {
        changeId: c.id,
        decision,
        decidedAt: new Date().toISOString(),
      }
    }
  }
  recomputeStats()
  saveToLocalStorage()
  scheduleAutoSave()
}

function decisionColor(decision?: string) {
  switch (decision) {
    case 'approved': return 'success'
    case 'rejected': return 'error'
    case 'edited': return 'warning'
    case 'skipped': return 'neutral'
    default: return undefined
  }
}

function recomputeStats() {
  const s = { total: 0, approved: 0, rejected: 0, edited: 0, skipped: 0 }
  for (const d of Object.values(decisions.value)) {
    s[d.decision]++
    s.total++
  }
  sessionStats.value = s
}

function saveToLocalStorage() {
  localStorage.setItem(`review_${sessionId.value}`, JSON.stringify({
    decisions: decisions.value,
    savedAt: new Date().toISOString(),
  }))
}

function scheduleAutoSave() {
  if (autoSaveTimer) clearTimeout(autoSaveTimer)
  autoSaveTimer = setTimeout(() => saveToGCS(), 30_000)
}

async function saveToGCS() {
  if (isSaving.value || !Object.keys(decisions.value).length) return
  isSaving.value = true
  try {
    // Build change metadata for feedback
    const changeMeta: Record<string, { ruleCategory: string; field: string; old: string; suggested: string; confidence: number }> = {}
    for (const c of allChanges.value) {
      if (decisions.value[c.id]) {
        changeMeta[c.id] = {
          ruleCategory: c.ruleCategory,
          field: c.field,
          old: c.old,
          suggested: c.new,
          confidence: c.confidence,
        }
      }
    }

    const allDecisions = Object.values(decisions.value)
    await $fetch('/api/review/decide', {
      method: 'POST',
      body: {
        sessionId: sessionId.value,
        reviewFilePath: data.value?.reviewFilePath,
        decisions: allDecisions,
        changeMeta,
      },
    })
    lastSaved.value = new Date().toLocaleTimeString()
    saveError.value = null
  } catch (err) {
    console.error('Failed to save to GCS:', err)
    saveError.value = 'Failed to save. Check your connection.'
  } finally {
    isSaving.value = false
  }
}

async function exportDecisions() {
  await saveToGCS()
  try {
    const result = await $fetch('/api/review/export', {
      method: 'POST',
      body: { sessionId: sessionId.value },
    })
    exportMessage.value = `Exported ${result.exported} decisions for pipeline processing.`
  } catch (err) {
    console.error('Export failed:', err)
    exportMessage.value = 'Export failed — please try again.'
  }
}

const progressPercent = computed(() => {
  if (!allChanges.value.length) return 0
  return Math.round((sessionStats.value.total / allChanges.value.length) * 100)
})

// Auto-export when all changes reviewed (100%)
watch(progressPercent, async (pct) => {
  if (pct === 100 && allChanges.value.length > 0 && !isDemo.value && !exportMessage.value) {
    await exportDecisions()
  }
})

// Keyboard shortcuts
function handleKeydown(e: KeyboardEvent) {
  if (isDemo.value) return
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return

  const group = paginatedGroups.value[focusedIndex.value]
  if (!group) return

  const undecidedChanges = group.changes.filter(c => !decisions.value[c.id])
  const firstUndecided = undecidedChanges[0]

  switch (e.key) {
    case 'j':
      e.preventDefault()
      if (focusedIndex.value < paginatedGroups.value.length - 1) {
        focusedIndex.value++
      } else if (currentPage.value < totalPages.value - 1) {
        currentPage.value++
        focusedIndex.value = 0
      }
      break
    case 'k':
      e.preventDefault()
      if (focusedIndex.value > 0) {
        focusedIndex.value--
      } else if (currentPage.value > 0) {
        currentPage.value--
        focusedIndex.value = PAGE_SIZE - 1
      }
      break
    case 'a':
      e.preventDefault()
      if (firstUndecided) decide(firstUndecided.id, 'approved')
      break
    case 'r':
      e.preventDefault()
      if (firstUndecided) decide(firstUndecided.id, 'rejected')
      break
    case 's':
      e.preventDefault()
      if (firstUndecided) decide(firstUndecided.id, 'skipped')
      break
    case 'e':
      e.preventDefault()
      // Edit is handled inside ReviewContactCard component
      break
    case 'A':
      e.preventDefault()
      decideAllForContact(group.resourceName, 'approved')
      break
    case 'R':
      e.preventDefault()
      decideAllForContact(group.resourceName, 'rejected')
      break
    case 'S':
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault()
        saveToGCS()
      }
      break
  }
}

onMounted(() => {
  window.addEventListener('keydown', handleKeydown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeydown)
  if (autoSaveTimer) clearTimeout(autoSaveTimer)
})


</script>

<template>
  <div class="space-y-4">
    <!-- Header -->
    <div class="flex items-center justify-between">
      <div>
        <h1 class="text-xl font-bold text-neutral-100">
          Review MEDIUM Changes
        </h1>
        <p class="text-xs text-neutral-500 mt-1">
          {{ allChanges.length }} changes from {{ contactGroups.length }} contacts
        </p>
      </div>
      <div v-if="!isDemo" class="flex items-center gap-3">
        <span v-if="saveError" class="text-xs text-red-400">
          {{ saveError }}
        </span>
        <span v-else-if="exportMessage" class="text-xs text-green-400">
          {{ exportMessage }}
        </span>
        <span v-else-if="lastSaved" class="text-xs text-neutral-600">
          Saved {{ lastSaved }}
        </span>
        <UButton
          label="Save"
          icon="i-lucide-save"
          size="sm"
          variant="soft"
          :loading="isSaving"
          @click="saveToGCS()"
        />
        <UButton
          :label="exportMessage ? 'Exported' : 'Export for Pipeline'"
          :icon="exportMessage ? 'i-lucide-check' : 'i-lucide-upload'"
          size="sm"
          :color="exportMessage ? 'success' : 'primary'"
          :disabled="(!sessionStats.approved && !sessionStats.edited) || !!exportMessage"
          @click="exportDecisions()"
        />
      </div>
    </div>

    <!-- Progress -->
    <div class="rounded-lg border border-neutral-800 bg-neutral-900/50 p-3">
      <div class="flex items-center justify-between text-xs mb-2">
        <span class="text-neutral-400">
          {{ sessionStats.total }}/{{ allChanges.length }} reviewed ({{ progressPercent }}%)
        </span>
        <div class="flex gap-3">
          <span class="text-green-400">{{ sessionStats.approved }} approved</span>
          <span class="text-red-400">{{ sessionStats.rejected }} rejected</span>
          <span class="text-amber-400">{{ sessionStats.edited }} edited</span>
          <span class="text-neutral-500">{{ sessionStats.skipped }} skipped</span>
        </div>
      </div>
      <div class="h-1.5 bg-neutral-800 rounded-full overflow-hidden">
        <div
          class="h-full bg-primary-500 transition-all duration-300"
          :style="{ width: `${progressPercent}%` }"
        />
      </div>
    </div>

    <!-- Decision Counts + Last Export -->
    <div v-if="sessionStats.total > 0 || sessionsData?.lastExport" class="flex flex-wrap gap-3">
      <!-- Decision counts -->
      <div v-if="sessionStats.total > 0" class="flex items-center gap-4 rounded-lg border border-neutral-800 bg-neutral-900/50 px-4 py-2.5">
        <div class="text-xs text-neutral-400">Decisions</div>
        <div class="flex items-center gap-1">
          <span class="text-lg font-semibold text-green-400 tabular-nums">{{ sessionStats.approved }}</span>
          <span class="text-[10px] text-neutral-600">approved</span>
        </div>
        <div class="flex items-center gap-1">
          <span class="text-lg font-semibold text-red-400 tabular-nums">{{ sessionStats.rejected }}</span>
          <span class="text-[10px] text-neutral-600">rejected</span>
        </div>
        <div class="flex items-center gap-1">
          <span class="text-lg font-semibold text-amber-400 tabular-nums">{{ sessionStats.edited }}</span>
          <span class="text-[10px] text-neutral-600">edited</span>
        </div>
        <div v-if="sessionStats.skipped > 0" class="flex items-center gap-1">
          <span class="text-lg font-semibold text-neutral-500 tabular-nums">{{ sessionStats.skipped }}</span>
          <span class="text-[10px] text-neutral-600">skipped</span>
        </div>
      </div>

      <!-- Last export status -->
      <div v-if="sessionsData?.lastExport" class="flex items-center gap-2 rounded-lg border border-neutral-800 bg-neutral-900/50 px-4 py-2.5">
        <UIcon name="i-lucide-upload" class="size-3.5 text-neutral-500" />
        <span class="text-xs text-neutral-400">
          Last export: {{ formatDate(sessionsData.lastExport.exportedAt) }}
        </span>
        <span class="text-xs text-neutral-500">
          ({{ sessionsData.lastExport.count }} decisions)
        </span>
      </div>
    </div>

    <!-- Session History -->
    <div v-if="sessionsData?.sessions?.length" class="rounded-lg border border-neutral-800 bg-neutral-900/50">
      <div
        class="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-neutral-800/30"
        @click="showHistory = !showHistory"
      >
        <div class="flex items-center gap-2 text-xs text-neutral-400">
          <UIcon :name="showHistory ? 'i-lucide-chevron-down' : 'i-lucide-chevron-right'" class="size-3" />
          <span>Session History</span>
          <span class="text-neutral-600">({{ sessionsData.sessions.length }} sessions)</span>
        </div>
      </div>
      <div v-if="showHistory" class="px-3 pb-3">
        <table class="w-full text-xs">
          <thead>
            <tr class="text-neutral-600 border-b border-neutral-800/50">
              <th class="text-left py-1 font-normal">Date</th>
              <th class="text-right py-1 font-normal">Total</th>
              <th class="text-right py-1 font-normal">Approved</th>
              <th class="text-right py-1 font-normal">Rejected</th>
              <th class="text-right py-1 font-normal">Edited</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="s in sessionsData.sessions"
              :key="s.id"
              class="border-b border-neutral-800/30 text-neutral-400"
            >
              <td class="py-1.5">{{ formatDate(s.createdAt) }}</td>
              <td class="text-right tabular-nums">{{ s.stats.total }}</td>
              <td class="text-right tabular-nums text-green-400/80">{{ s.stats.approved }}</td>
              <td class="text-right tabular-nums text-red-400/80">{{ s.stats.rejected }}</td>
              <td class="text-right tabular-nums text-amber-400/80">{{ s.stats.edited }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Queue Stats Trend -->
    <div v-if="queueStats?.length" class="rounded-lg border border-neutral-800 bg-neutral-900/50">
      <div
        class="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-neutral-800/30"
        @click="showStats = !showStats"
      >
        <div class="flex items-center gap-2 text-xs text-neutral-400">
          <UIcon :name="showStats ? 'i-lucide-chevron-down' : 'i-lucide-chevron-right'" class="size-3" />
          <span>Queue Trend</span>
          <span v-if="queueStats.length >= 2" class="text-neutral-600">
            {{ queueStats[queueStats.length - 1].totalChanges }}
            <template v-if="queueStats[queueStats.length - 1].totalChanges !== queueStats[queueStats.length - 2].totalChanges">
              <span :class="queueStats[queueStats.length - 1].totalChanges < queueStats[queueStats.length - 2].totalChanges ? 'text-green-500' : 'text-red-400'">
                ({{ queueStats[queueStats.length - 1].totalChanges < queueStats[queueStats.length - 2].totalChanges ? '' : '+' }}{{ queueStats[queueStats.length - 1].totalChanges - queueStats[queueStats.length - 2].totalChanges }})
              </span>
            </template>
          </span>
        </div>
      </div>
      <div v-if="showStats" class="px-3 pb-3">
        <div class="flex items-end gap-px h-16">
          <div
            v-for="(entry, i) in queueStats.slice(-30)"
            :key="i"
            class="flex-1 bg-primary-500/60 hover:bg-primary-400/80 rounded-t-sm transition-colors relative group"
            :style="{ height: `${Math.max(4, (entry.totalChanges / Math.max(...queueStats.slice(-30).map(e => e.totalChanges))) * 100)}%` }"
          >
            <div class="absolute bottom-full mb-1 left-1/2 -translate-x-1/2 hidden group-hover:block bg-neutral-800 text-[10px] text-neutral-300 px-1.5 py-0.5 rounded whitespace-nowrap z-10">
              {{ entry.date }}: {{ entry.totalChanges }}
            </div>
          </div>
        </div>
        <div class="flex justify-between text-[10px] text-neutral-600 mt-1">
          <span>{{ queueStats.slice(-30)[0]?.date }}</span>
          <span>{{ queueStats[queueStats.length - 1]?.date }}</span>
        </div>
      </div>
    </div>

    <!-- Empty state -->
    <div v-if="status === 'success' && !allChanges.length" class="text-center py-16">
      <UIcon name="i-lucide-check-circle" class="size-12 text-green-500 mx-auto mb-3" />
      <p class="text-neutral-400">No pending review changes</p>
      <p class="text-xs text-neutral-600 mt-1">Changes appear here after the pipeline runs with MEDIUM confidence items</p>
    </div>

    <!-- Loading -->
    <div v-else-if="status === 'pending'" class="text-center py-16">
      <UIcon name="i-lucide-loader" class="size-8 text-neutral-500 mx-auto mb-3 animate-spin" />
      <p class="text-neutral-500">Loading review data...</p>
    </div>

    <template v-else>
      <!-- Filters + Controls -->
      <div class="flex flex-wrap items-center gap-3">
        <USelect
          v-model="fieldFilter"
          :items="fieldOptions"
          value-key="value"
          class="w-48"
          placeholder="Field"
        />
        <USelect
          v-model="categoryFilter"
          :items="categoryOptions"
          value-key="value"
          class="w-48"
          placeholder="Rule"
        />
        <label class="flex items-center gap-1.5 text-xs text-neutral-400 cursor-pointer">
          <input v-model="hideDecided" type="checkbox" class="rounded border-neutral-700 bg-neutral-800" />
          Hide decided
        </label>

        <div class="ml-auto flex items-center gap-2">
          <UFieldGroup size="xs">
            <UButton
              label="Contact"
              :variant="viewMode === 'contact' ? 'solid' : 'ghost'"
              @click="viewMode = 'contact'"
            />
            <UButton
              label="Rule"
              :variant="viewMode === 'rule' ? 'solid' : 'ghost'"
              @click="viewMode = 'rule'"
            />
          </UFieldGroup>

          <template v-if="!isDemo">
            <UButton
              v-if="filteredChanges.filter(c => !decisions[c.id]).length"
              size="xs"
              variant="soft"
              color="success"
              :label="`Approve remaining (${filteredChanges.filter(c => !decisions[c.id]).length})`"
              @click="bulkDecide('approved')"
            />
            <UButton
              v-if="filteredChanges.filter(c => !decisions[c.id]).length"
              size="xs"
              variant="soft"
              color="error"
              :label="`Reject remaining (${filteredChanges.filter(c => !decisions[c.id]).length})`"
              @click="bulkDecide('rejected')"
            />
          </template>
        </div>
      </div>

      <!-- Contact View (paginated) -->
      <div v-if="viewMode === 'contact'" class="space-y-3">
        <ReviewContactCard
          v-for="(group, idx) in paginatedGroups"
          :key="group.resourceName"
          :group="group"
          :focused="idx === focusedIndex"
          :decisions="decisions"
          @decide="decide"
          @decide-all="decideAllForContact"
          @undo-decision="undoDecision"
          @focus="focusedIndex = idx"
        />

        <!-- Pagination -->
        <div v-if="totalPages > 1" class="flex items-center justify-center gap-3 pt-2">
          <UButton
            size="xs"
            variant="ghost"
            icon="i-lucide-chevron-left"
            :disabled="currentPage === 0"
            @click="currentPage--; focusedIndex = 0"
          />
          <span class="text-xs text-neutral-400 tabular-nums">
            {{ currentPage + 1 }} / {{ totalPages }}
            <span class="text-neutral-600 ml-1">({{ contactGroups.length }} contacts)</span>
          </span>
          <UButton
            size="xs"
            variant="ghost"
            icon="i-lucide-chevron-right"
            :disabled="currentPage >= totalPages - 1"
            @click="currentPage++; focusedIndex = 0"
          />
        </div>
      </div>

      <!-- Rule View -->
      <div v-if="viewMode === 'rule'" class="space-y-3">
        <div
          v-for="[category, changes] in ruleGroups"
          :key="category"
          class="rounded-xl border border-neutral-800 bg-neutral-900/30"
        >
          <div class="flex items-center justify-between px-4 py-3 border-b border-neutral-800/50">
            <div class="flex items-center gap-2">
              <UButton
                size="xs" variant="ghost" color="neutral"
                :icon="expandedRules[category] ? 'i-lucide-chevron-down' : 'i-lucide-chevron-right'"
                @click="expandedRules[category] = !expandedRules[category]"
              />
              <span class="text-sm font-medium text-neutral-200">{{ category }}</span>
              <UBadge :label="`${ruleUndecided(changes)} pending`" variant="subtle" size="xs" :color="ruleUndecided(changes) === 0 ? 'success' : 'neutral'" />
              <span v-if="ruleDecidedCount(changes) > 0" class="text-[10px] text-neutral-600">
                {{ ruleDecidedCount(changes) }} decided
                <span v-if="ruleApprovalRate(changes) !== null" class="text-neutral-500">
                  ({{ ruleApprovalRate(changes) }}% approved)
                </span>
              </span>
            </div>
            <div v-if="!isDemo" class="flex gap-1">
              <UButton
                v-if="ruleUndecided(changes) > 0"
                size="xs" variant="soft" color="success"
                :label="`Approve remaining (${ruleUndecided(changes)})`"
                @click="changes.forEach(c => { if (!decisions[c.id]) decide(c.id, 'approved') })"
              />
              <UButton
                v-if="ruleUndecided(changes) > 0"
                size="xs" variant="soft" color="error"
                :label="`Reject remaining (${ruleUndecided(changes)})`"
                @click="changes.forEach(c => { if (!decisions[c.id]) decide(c.id, 'rejected') })"
              />
              <UButton
                v-if="ruleDecidedCount(changes) > 0"
                size="xs" variant="ghost" color="neutral"
                label="Undo all"
                @click="changes.forEach(c => { if (decisions[c.id]) undoDecision(c.id) })"
              />
            </div>
          </div>

          <!-- Sample preview (always visible: first 5 undecided) -->
          <div v-if="!expandedRules[category]" class="divide-y divide-neutral-800/30">
            <div
              v-for="change in ruleSamples(changes)"
              :key="change.id"
              class="px-4 py-1.5 flex items-center gap-3"
              :class="{ 'opacity-40': decisions[change.id] }"
            >
              <span class="text-xs text-neutral-400 w-32 shrink-0 truncate">{{ change.displayName }}</span>
              <span class="text-xs font-mono text-neutral-500 w-28 shrink-0 truncate">{{ formatFieldShort(change.field) }}</span>
              <div class="flex-1 min-w-0">
                <DiffDisplay :old-value="change.old" :new-value="change.new" />
              </div>
              <span class="text-[10px] text-neutral-600 tabular-nums w-10 text-right shrink-0">{{ (change.confidence * 100).toFixed(0) }}%</span>
              <div v-if="!isDemo" class="flex items-center gap-1 shrink-0">
                <template v-if="decisions[change.id]">
                  <UBadge :label="decisions[change.id].decision" :color="decisionColor(decisions[change.id].decision)" variant="subtle" size="xs" />
                </template>
                <template v-else>
                  <UButton size="xs" variant="ghost" color="success" icon="i-lucide-check" @click.stop="decide(change.id, 'approved')" />
                  <UButton size="xs" variant="ghost" color="error" icon="i-lucide-x" @click.stop="decide(change.id, 'rejected')" />
                </template>
              </div>
            </div>
            <div v-if="changes.length > 5" class="px-4 py-1.5 text-[10px] text-neutral-600 cursor-pointer hover:text-neutral-400" @click="expandedRules[category] = true">
              + {{ changes.length - 5 }} more — click to expand
            </div>
          </div>

          <!-- Full list (expanded) -->
          <div v-else class="divide-y divide-neutral-800/30 max-h-96 overflow-y-auto">
            <div
              v-for="change in changes"
              :key="change.id"
              class="px-4 py-1.5 flex items-center gap-3"
              :class="{ 'opacity-40': decisions[change.id] }"
            >
              <span class="text-xs text-neutral-400 w-32 shrink-0 truncate">{{ change.displayName }}</span>
              <span class="text-xs font-mono text-neutral-500 w-28 shrink-0 truncate">{{ formatFieldShort(change.field) }}</span>
              <div class="flex-1 min-w-0">
                <DiffDisplay :old-value="change.old" :new-value="change.new" />
              </div>
              <span class="text-[10px] text-neutral-600 tabular-nums w-10 text-right shrink-0">{{ (change.confidence * 100).toFixed(0) }}%</span>
              <div v-if="!isDemo" class="flex items-center gap-1 shrink-0">
                <template v-if="decisions[change.id]">
                  <UBadge :label="decisions[change.id].decision" :color="decisionColor(decisions[change.id].decision)" variant="subtle" size="xs" />
                  <UButton size="xs" variant="ghost" icon="i-lucide-undo-2" color="neutral" @click.stop="undoDecision(change.id)" />
                </template>
                <template v-else>
                  <UButton size="xs" variant="ghost" color="success" icon="i-lucide-check" @click.stop="decide(change.id, 'approved')" />
                  <UButton size="xs" variant="ghost" color="error" icon="i-lucide-x" @click.stop="decide(change.id, 'rejected')" />
                  <UButton size="xs" variant="ghost" color="neutral" icon="i-lucide-skip-forward" @click.stop="decide(change.id, 'skipped')" />
                </template>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- Keyboard help -->
    <div v-if="!isDemo" class="text-[10px] text-neutral-700 flex gap-4 justify-center pt-2">
      <span><kbd class="px-1 py-0.5 bg-neutral-800 rounded">a</kbd> approve</span>
      <span><kbd class="px-1 py-0.5 bg-neutral-800 rounded">r</kbd> reject</span>
      <span><kbd class="px-1 py-0.5 bg-neutral-800 rounded">e</kbd> edit</span>
      <span><kbd class="px-1 py-0.5 bg-neutral-800 rounded">s</kbd> skip</span>
      <span><kbd class="px-1 py-0.5 bg-neutral-800 rounded">j</kbd>/<kbd class="px-1 py-0.5 bg-neutral-800 rounded">k</kbd> nav</span>
      <span><kbd class="px-1 py-0.5 bg-neutral-800 rounded">Shift+A</kbd> approve all</span>
      <span><kbd class="px-1 py-0.5 bg-neutral-800 rounded">Ctrl+S</kbd> save</span>
    </div>
  </div>
</template>
