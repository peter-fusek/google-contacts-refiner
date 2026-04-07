<script setup lang="ts">
useHead({
  title: 'Pipeline Status — Contact Refiner',
  meta: [
    { name: 'description', content: 'Monitor your Google Contacts cleanup pipeline. View current batch progress, changes applied, failures, and AI review status.' },
  ],
})

import type { StatusResponse } from '~/server/utils/types'
import type { PipelineRun } from '~/server/utils/gcs'

const { data: status, status: fetchStatus, refresh } = useFetch<StatusResponse>('/api/status')
const { data: runs } = useFetch<PipelineRun[]>('/api/pipeline-runs')
const { relativeLabel } = useNextRun(computed(() => status.value?.status))

const recentRuns = computed(() => runs.value?.slice(0, 7) ?? [])

// Poll every 5s when running
const pollInterval = ref<ReturnType<typeof setInterval>>()

watch(
  () => status.value?.status,
  (s) => {
    if (pollInterval.value) clearInterval(pollInterval.value)
    if (s === 'running') {
      pollInterval.value = setInterval(refresh, 5000)
    }
  },
  { immediate: true },
)

onUnmounted(() => {
  if (pollInterval.value) clearInterval(pollInterval.value)
})

function formatDuration(seconds: number | null) {
  if (!seconds) return '--'
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}m ${s}s`
}

function formatTime(iso: string | null) {
  if (!iso) return '--'
  return new Date(iso).toLocaleString('en-GB', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}
</script>

<template>
  <div class="space-y-6">
    <div class="flex items-center justify-between">
      <h1 class="text-xl font-bold text-neutral-100">
        Status
      </h1>
      <div class="flex items-center gap-3">
        <span class="text-xs text-neutral-500">Next: <span class="text-neutral-400">{{ relativeLabel }}</span></span>
        <StatusBadge :status="status?.status ?? 'idle'" />
      </div>
    </div>

    <!-- Loading -->
    <div v-if="fetchStatus === 'pending'" class="text-center py-16">
      <UIcon name="i-lucide-loader" class="size-8 text-neutral-500 mx-auto mb-3 animate-spin" />
      <p class="text-neutral-500">Loading status...</p>
    </div>

    <!-- Error -->
    <div v-else-if="fetchStatus === 'error'" class="text-center py-16">
      <UIcon name="i-lucide-alert-triangle" class="size-8 text-red-500 mx-auto mb-3" />
      <p class="text-red-400">Failed to load data</p>
      <UButton label="Retry" size="sm" variant="soft" class="mt-3" @click="refresh()" />
    </div>

    <!-- Pipeline Diagram -->
    <NuxtLink v-if="fetchStatus !== 'pending'" to="/pipeline" class="block rounded-xl border border-neutral-800 bg-neutral-900/50 p-5 overflow-x-auto hover:border-neutral-700 transition-colors cursor-pointer">
      <p class="text-xs uppercase tracking-wider text-neutral-500 mb-3">
        Pipeline
      </p>
      <PipelineDiagram
        :phase="status?.phase ?? 'idle'"
        :status="status?.status ?? 'idle'"
      />
    </NuxtLink>

    <!-- Progress Bars -->
    <div v-if="fetchStatus !== 'pending'" class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div class="rounded-xl border border-neutral-800 bg-neutral-900/50 p-5">
        <ProgressBar
          :current="status?.currentBatch ?? 0"
          :total="status?.totalBatches ?? 0"
          label="Batches"
        />
      </div>
      <div class="rounded-xl border border-neutral-800 bg-neutral-900/50 p-5">
        <ProgressBar
          :current="status?.contactsProcessed ?? 0"
          :total="status?.contactsTotal ?? 0"
          label="Contacts"
        />
      </div>
    </div>

    <!-- Stats Cards -->
    <div v-if="fetchStatus !== 'pending'" class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <StatsCard
        label="Applied"
        :value="status?.lastRun.changesApplied ?? 0"
        icon="i-lucide-check-circle"
        color="green"
        to="/changelog"
      />
      <StatsCard
        label="Failed"
        :value="status?.lastRun.changesFailed ?? 0"
        icon="i-lucide-x-circle"
        color="red"
        to="/runs"
      />
      <StatsCard
        label="Duration"
        :value="formatDuration(status?.lastRun.duration ?? null)"
        icon="i-lucide-clock"
        color="cyan"
        to="/runs"
      />
      <StatsCard
        label="Cost"
        :value="status?.lastRun.cost ? `$${status.lastRun.cost}` : '--'"
        icon="i-lucide-dollar-sign"
        color="amber"
        to="/runs"
      />
    </div>

    <!-- Pipeline Health — recent runs -->
    <NuxtLink v-if="recentRuns.length" to="/runs" class="block rounded-xl border border-neutral-800 bg-neutral-900/50 p-5 hover:border-neutral-700 transition-colors">
      <div class="flex items-center justify-between mb-3">
        <p class="text-xs uppercase tracking-wider text-neutral-500">
          Pipeline Health
        </p>
        <span class="text-[10px] text-neutral-600">last {{ recentRuns.length }} runs</span>
      </div>
      <div class="flex gap-1.5 items-end">
        <div
          v-for="(run, i) in recentRuns"
          :key="i"
          class="flex-1 flex flex-col items-center gap-1"
        >
          <div
            class="w-full rounded-sm transition-colors"
            :class="run.errors.length ? 'bg-red-500/70' : 'bg-green-500/60'"
            :style="{ height: `${Math.max(8, Math.min(40, run.duration_seconds / 10))}px` }"
            :title="`${formatTime(run.date)} — ${formatDuration(run.duration_seconds)} — ${run.errors.length ? run.errors.length + ' errors' : 'OK'}`"
          />
          <span class="text-[9px] text-neutral-600 tabular-nums">{{ run.phases_completed?.length ?? 0 }}ph</span>
        </div>
      </div>
      <div class="flex justify-between text-[9px] text-neutral-700 mt-1">
        <span>{{ formatTime(recentRuns[recentRuns.length - 1]?.date ?? null) }}</span>
        <span>{{ formatTime(recentRuns[0]?.date ?? null) }}</span>
      </div>
    </NuxtLink>

    <!-- Last Run Info -->
    <div v-if="fetchStatus !== 'pending'" class="rounded-xl border border-neutral-800 bg-neutral-900/50 p-5">
      <p class="text-xs uppercase tracking-wider text-neutral-500 mb-3">
        Last Run
      </p>
      <div class="grid grid-cols-2 gap-4 text-sm">
        <div>
          <span class="text-neutral-500">Started:</span>
          <span class="text-neutral-300 ml-2">{{ formatTime(status?.lastRun.startedAt ?? null) }}</span>
        </div>
        <div>
          <span class="text-neutral-500">Completed:</span>
          <span class="text-neutral-300 ml-2">{{ formatTime(status?.lastRun.completedAt ?? null) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
