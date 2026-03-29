<script setup lang="ts">
useHead({
  title: 'Configuration — Contact Refiner',
  meta: [
    { name: 'description', content: 'Pipeline configuration: environment settings, batch size, confidence thresholds, AI model, and rule categories.' },
  ],
})

import type { ConfigResponse } from '~/server/utils/types'

const { loggedIn } = useUserSession()
const isDemo = computed(() => !loggedIn.value)

const { data, status, refresh } = useFetch<ConfigResponse>('/api/config')

const isPausing = ref(false)
const pauseStatus = ref<'idle' | 'paused' | 'error'>('idle')

async function emergencyPause() {
  if (isDemo.value) return
  isPausing.value = true
  try {
    await $fetch('/api/config/pause', { method: 'POST' })
    pauseStatus.value = 'paused'
    await refresh()
  } catch {
    pauseStatus.value = 'error'
  } finally {
    isPausing.value = false
  }
}

// --- Edit Mode ---
const isEditing = ref(false)
const isSaving = ref(false)
const saveStatus = ref<'idle' | 'saved' | 'error'>('idle')

const draft = ref({
  batchSize: 50,
  confidenceHigh: 0.90,
  confidenceMedium: 0.60,
  aiCostLimit: 3.00,
  autoMaxChanges: 200,
  autoThreshold: 0.90,
})

function startEditing() {
  if (!data.value) return
  draft.value = {
    batchSize: data.value.batchSize,
    confidenceHigh: data.value.confidenceHigh,
    confidenceMedium: data.value.confidenceMedium,
    aiCostLimit: data.value.aiCostLimit,
    autoMaxChanges: data.value.autoMaxChanges,
    autoThreshold: data.value.autoThreshold,
  }
  saveStatus.value = 'idle'
  isEditing.value = true
}

function cancelEditing() {
  isEditing.value = false
  saveStatus.value = 'idle'
}

async function saveConfig() {
  isSaving.value = true
  saveStatus.value = 'idle'
  try {
    await $fetch('/api/config/save', { method: 'POST', body: draft.value })
    saveStatus.value = 'saved'
    isEditing.value = false
    await refresh()
  } catch {
    saveStatus.value = 'error'
  } finally {
    isSaving.value = false
  }
}

interface ConfigRow {
  key: string
  field?: keyof typeof draft.value
  value: string | number
  desc: string
  editable: boolean
  type?: 'int' | 'float' | 'currency'
}

const rows = computed<ConfigRow[]>(() => {
  if (!data.value) return []
  return [
    {
      key: 'Environment',
      value: data.value.environment,
      desc: 'Where the pipeline runs: "cloud" (Cloud Run Job) or "local" (Mac)',
      editable: false,
    },
    {
      key: 'Batch Size',
      field: 'batchSize',
      value: data.value.batchSize,
      desc: 'Number of contacts processed per batch in each pipeline run',
      editable: true,
      type: 'int',
    },
    {
      key: 'Confidence HIGH',
      field: 'confidenceHigh',
      value: data.value.confidenceHigh,
      desc: 'Changes at or above this threshold are auto-applied without review',
      editable: true,
      type: 'float',
    },
    {
      key: 'Confidence MEDIUM',
      field: 'confidenceMedium',
      value: data.value.confidenceMedium,
      desc: 'Changes in this range go to AI review, then to the review queue',
      editable: true,
      type: 'float',
    },
    {
      key: 'AI Model',
      value: data.value.aiModel,
      desc: 'Claude model used for AI review of MEDIUM confidence changes',
      editable: false,
    },
    {
      key: 'AI Cost Limit',
      field: 'aiCostLimit',
      value: data.value.aiCostLimit,
      desc: 'Maximum Claude API spend per pipeline run (safety cap)',
      editable: true,
      type: 'currency',
    },
    {
      key: 'Auto Threshold',
      field: 'autoThreshold',
      value: data.value.autoThreshold,
      desc: 'Minimum confidence for auto-fix without human review',
      editable: true,
      type: 'float',
    },
    {
      key: 'Auto Max Changes',
      field: 'autoMaxChanges',
      value: data.value.autoMaxChanges,
      desc: 'Maximum changes auto-applied per run (prevents runaway fixes)',
      editable: true,
      type: 'int',
    },
    {
      key: 'Scheduler',
      value: data.value.schedulerStatus,
      desc: 'Cloud Scheduler status — daily 9:00 Europe/Bratislava',
      editable: false,
    },
  ]
})

function formatValue(row: ConfigRow): string {
  if (row.type === 'currency') return `$${row.value}/session`
  if (row.type === 'float') return `>= ${row.value}`
  return String(row.value)
}
</script>

<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <h1 class="text-xl font-bold text-neutral-100">
        Config
      </h1>
      <div class="flex gap-2">
        <UButton
          v-if="!isDemo && !isEditing"
          icon="i-lucide-pencil"
          label="Edit"
          size="sm"
          variant="soft"
          color="neutral"
          @click="startEditing"
        />
        <UButton
          v-if="!isDemo"
          icon="i-lucide-octagon"
          label="Emergency Stop"
          size="sm"
          variant="soft"
          color="error"
          :loading="isPausing"
          @click="emergencyPause"
        />
      </div>
    </div>

    <!-- Emergency Stop Feedback -->
    <div v-if="pauseStatus === 'paused'" class="rounded-lg border border-red-800 bg-red-900/30 px-4 py-3 text-sm text-red-300">
      Pipeline paused. The scheduler has been disabled and no new runs will start.
      To resume, re-enable the Cloud Scheduler in GCP Console.
    </div>
    <div v-if="pauseStatus === 'error'" class="rounded-lg border border-amber-800 bg-amber-900/30 px-4 py-3 text-sm text-amber-300">
      Failed to pause pipeline. Check the server logs or disable the scheduler manually in GCP Console.
    </div>

    <!-- Save Feedback -->
    <div v-if="saveStatus === 'saved'" class="rounded-lg border border-green-800 bg-green-900/30 px-4 py-3 text-sm text-green-300">
      Configuration saved. Changes will take effect on the next pipeline run.
    </div>
    <div v-if="saveStatus === 'error'" class="rounded-lg border border-red-800 bg-red-900/30 px-4 py-3 text-sm text-red-300">
      Failed to save configuration. Check the server logs.
    </div>

    <!-- Loading -->
    <div v-if="status === 'pending'" class="text-center py-16">
      <UIcon name="i-lucide-loader" class="size-8 text-neutral-500 mx-auto mb-3 animate-spin" />
      <p class="text-neutral-500">Loading config...</p>
    </div>

    <!-- Error -->
    <div v-else-if="status === 'error'" class="text-center py-16">
      <UIcon name="i-lucide-alert-triangle" class="size-8 text-red-500 mx-auto mb-3" />
      <p class="text-red-400">Failed to load data</p>
      <UButton label="Retry" size="sm" variant="soft" class="mt-3" @click="refresh()" />
    </div>

    <div v-if="status !== 'pending' && status !== 'error'" class="rounded-xl border border-neutral-800 bg-neutral-900/50 overflow-hidden">
      <table class="w-full text-sm">
        <thead class="bg-neutral-900/80">
          <tr class="text-left text-neutral-500 uppercase tracking-wider text-xs">
            <th class="px-5 py-3 font-medium">Parameter</th>
            <th class="px-5 py-3 font-medium">Value</th>
            <th class="px-5 py-3 font-medium">Description</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-neutral-800/50">
          <tr
            v-for="row in rows"
            :key="row.key"
            class="hover:bg-neutral-800/30 transition-colors"
          >
            <td class="px-5 py-3 text-neutral-400">
              {{ row.key }}
              <UIcon
                v-if="!row.editable"
                name="i-lucide-lock"
                class="size-3 text-neutral-600 ml-1 inline"
              />
            </td>
            <td class="px-5 py-3 text-neutral-200 font-mono">
              <!-- Edit mode: show input for editable fields -->
              <template v-if="isEditing && row.editable && row.field">
                <input
                  v-model.number="draft[row.field]"
                  type="number"
                  :step="row.type === 'int' ? 1 : 0.01"
                  class="w-28 bg-neutral-800 border border-neutral-700 rounded px-2 py-1 text-sm text-neutral-200 font-mono focus:border-primary-500 focus:outline-none"
                />
              </template>
              <!-- Display mode -->
              <template v-else>
                <UBadge
                  v-if="row.key === 'Scheduler'"
                  :label="String(row.value).toUpperCase()"
                  :color="row.value === 'active' || row.value === 'enabled' ? 'success' : 'warning'"
                  variant="subtle"
                  size="xs"
                />
                <span v-else>{{ formatValue(row) }}</span>
              </template>
            </td>
            <td class="px-5 py-3 text-xs text-neutral-600">{{ row.desc }}</td>
          </tr>
        </tbody>
      </table>

      <!-- Edit mode actions -->
      <div v-if="isEditing" class="flex justify-end gap-2 px-5 py-3 border-t border-neutral-800">
        <UButton
          label="Cancel"
          size="sm"
          variant="ghost"
          color="neutral"
          @click="cancelEditing"
        />
        <UButton
          label="Save"
          size="sm"
          variant="solid"
          color="primary"
          icon="i-lucide-save"
          :loading="isSaving"
          @click="saveConfig"
        />
      </div>
    </div>

  </div>
</template>
