<script setup lang="ts">
import type { ChangelogEntry } from '~/server/utils/types'

interface ContactGroup {
  resourceName: string
  displayName: string
  latestTimestamp: string
  changeCount: number
  entries: ChangelogEntry[]
}

interface GroupedResponse {
  groups: ContactGroup[]
  total: number
  totalGroups: number
  page: number
  pageSize: number
}

const route = useRoute()
const sessionIdFilter = computed(() => (route.query.sessionId as string) || '')

const search = ref('')
const fieldFilter = ref('all')
const confidenceFilter = ref('all')
const page = ref(1)
const pageSize = 20

const fieldOptions = [
  { label: 'All fields', value: 'all' },
  { label: 'Names', value: 'names' },
  { label: 'Phones', value: 'phoneNumbers' },
  { label: 'Emails', value: 'emailAddresses' },
  { label: 'Addresses', value: 'addresses' },
  { label: 'Organizations', value: 'organizations' },
  { label: 'URLs', value: 'urls' },
  { label: 'Dates', value: 'birthdays' },
]

const confidenceOptions = [
  { label: 'All', value: 'all' },
  { label: 'HIGH', value: 'high' },
  { label: 'MEDIUM', value: 'medium' },
  { label: 'LOW', value: 'low' },
]

const { data, status } = useFetch<GroupedResponse>('/api/changelog', {
  query: computed(() => ({
    page: page.value,
    pageSize,
    search: search.value,
    field: fieldFilter.value === 'all' ? '' : fieldFilter.value,
    confidence: confidenceFilter.value === 'all' ? '' : confidenceFilter.value,
    sessionId: sessionIdFilter.value,
  })),
  watch: [page, search, fieldFilter, confidenceFilter, sessionIdFilter],
})

const groups = computed(() => data.value?.groups ?? [])
const total = computed(() => data.value?.total ?? 0)
const totalGroups = computed(() => data.value?.totalGroups ?? 0)
const totalPages = computed(() => Math.ceil(totalGroups.value / pageSize))

// Track expanded groups
const expanded = reactive<Record<string, boolean>>({})

// Auto-expand first group
watch(groups, (g) => {
  if (g.length > 0 && Object.keys(expanded).length === 0) {
    expanded[g[0]!.resourceName] = true
  }
}, { immediate: true })

// Debounce search
let searchTimeout: ReturnType<typeof setTimeout>
function onSearchInput(val: string) {
  clearTimeout(searchTimeout)
  searchTimeout = setTimeout(() => {
    search.value = val
    page.value = 1
  }, 300)
}

function confidenceColor(c: string) {
  switch (c?.toLowerCase()) {
    case 'high': return 'success'
    case 'medium': return 'warning'
    case 'low': return 'error'
    default: return 'neutral'
  }
}

function formatField(field: string) {
  return field
    .replace('phoneNumbers', 'phones')
    .replace('emailAddresses', 'emails')
    .replace('.value', '')
    .replace('.givenName', '.given')
    .replace('.familyName', '.family')
    .replace('.formattedValue', '')
}

function contactUrl(resourceName: string): string {
  return `https://contacts.google.com/person/${resourceName.replace('people/', '')}`
}

function formatTime(ts: string): string {
  if (!ts) return ''
  return ts.slice(11, 19)
}

function formatDate(ts: string): string {
  if (!ts) return ''
  return new Date(ts).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}
</script>

<template>
  <div class="space-y-4">
    <!-- Filters -->
    <div class="flex flex-wrap gap-3">
      <UInput
        :model-value="search"
        placeholder="Search changes..."
        icon="i-lucide-search"
        class="w-64"
        @update:model-value="onSearchInput"
      />
      <USelect
        v-model="fieldFilter"
        :items="fieldOptions"
        value-key="value"
        class="w-40"
        @update:model-value="page = 1"
      />
      <USelect
        v-model="confidenceFilter"
        :items="confidenceOptions"
        value-key="value"
        class="w-32"
        @update:model-value="page = 1"
      />
      <div class="ml-auto text-xs text-neutral-500 self-center tabular-nums">
        {{ total }} changes across {{ totalGroups }} contacts
      </div>
    </div>

    <!-- Session filter banner -->
    <div v-if="sessionIdFilter" class="flex items-center gap-2 px-3 py-2 rounded-lg border border-primary-500/30 bg-primary-500/10 text-xs text-primary-400">
      <UIcon name="i-lucide-filter" class="size-3.5" />
      <span>Filtered by pipeline run session</span>
      <NuxtLink to="/changelog" class="ml-auto text-neutral-500 hover:text-neutral-300">Clear filter</NuxtLink>
    </div>

    <!-- Grouped by Contact -->
    <div class="space-y-3">
      <!-- Loading -->
      <div v-if="status === 'pending'" class="text-center py-8">
        <UIcon name="i-lucide-loader" class="size-6 text-neutral-500 mx-auto mb-2 animate-spin" />
        <p class="text-neutral-500 text-xs">Loading changelog...</p>
      </div>

      <!-- Empty -->
      <div v-else-if="groups.length === 0" class="text-center py-8 text-neutral-600 text-xs">
        No changes found
      </div>

      <!-- Contact Groups -->
      <div
        v-for="group in groups"
        :key="group.resourceName + group.displayName"
        class="rounded-xl border border-neutral-800 overflow-hidden"
      >
        <!-- Group Header -->
        <div
          class="flex items-center gap-3 px-4 py-3 bg-neutral-900/80 cursor-pointer hover:bg-neutral-800/50 transition-colors"
          @click="expanded[group.resourceName] = !expanded[group.resourceName]"
        >
          <UIcon
            :name="expanded[group.resourceName] ? 'i-lucide-chevron-down' : 'i-lucide-chevron-right'"
            class="size-4 text-neutral-500 shrink-0"
          />
          <span class="text-sm font-medium text-neutral-200 truncate">{{ group.displayName }}</span>
          <UBadge :label="`${group.changeCount}`" variant="subtle" color="neutral" size="xs" />
          <span class="text-[10px] text-neutral-600 tabular-nums shrink-0">{{ formatDate(group.latestTimestamp) }}</span>
          <div class="ml-auto flex items-center gap-2 shrink-0">
            <a
              v-if="group.resourceName !== '***'"
              :href="contactUrl(group.resourceName)"
              target="_blank"
              rel="noopener"
              class="text-neutral-600 hover:text-neutral-400"
              title="View in Google Contacts"
              @click.stop
            >
              <UIcon name="i-lucide-external-link" class="size-3.5" />
            </a>
          </div>
        </div>

        <!-- Entries Table -->
        <div v-if="expanded[group.resourceName]">
          <table class="w-full text-xs">
            <tbody class="divide-y divide-neutral-800/50">
              <tr
                v-for="entry in group.entries"
                :key="`${entry.field}-${entry.timestamp}`"
                class="hover:bg-neutral-800/30 transition-colors"
              >
                <td class="px-4 py-2 text-neutral-400 font-mono w-40">
                  {{ formatField(entry.field) }}
                </td>
                <td class="px-4 py-2">
                  <DiffDisplay :old-value="entry.old" :new-value="entry.new" />
                </td>
                <td class="px-4 py-2 w-20">
                  <UBadge
                    :label="entry.confidence?.toUpperCase()"
                    :color="confidenceColor(entry.confidence)"
                    variant="subtle"
                    size="xs"
                  />
                </td>
                <td class="px-4 py-2 text-neutral-500 max-w-xs truncate">
                  {{ entry.reason }}
                </td>
                <td class="px-4 py-2 text-neutral-600 tabular-nums whitespace-nowrap w-16">
                  {{ formatTime(entry.timestamp) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Pagination -->
    <div v-if="totalPages > 1" class="flex justify-center">
      <UPagination
        v-model="page"
        :total="totalGroups"
        :items-per-page="pageSize"
      />
    </div>
  </div>
</template>
