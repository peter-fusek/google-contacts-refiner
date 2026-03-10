<script setup lang="ts">
import type { StatusResponse } from '~/server/utils/types'

const { data: status, refresh } = useFetch<StatusResponse>('/api/status')

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
      <StatusBadge :status="status?.status ?? 'idle'" />
    </div>

    <!-- Pipeline Diagram -->
    <div class="rounded-xl border border-neutral-800 bg-neutral-900/50 p-5 overflow-x-auto">
      <p class="text-xs uppercase tracking-wider text-neutral-500 mb-3">
        Pipeline
      </p>
      <PipelineDiagram
        :phase="status?.phase ?? 'idle'"
        :status="status?.status ?? 'idle'"
      />
    </div>

    <!-- Progress Bars -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
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
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
      <StatsCard
        label="Applied"
        :value="status?.lastRun.changesApplied ?? 0"
        icon="i-lucide-check-circle"
        color="green"
      />
      <StatsCard
        label="Failed"
        :value="status?.lastRun.changesFailed ?? 0"
        icon="i-lucide-x-circle"
        color="red"
      />
      <StatsCard
        label="Duration"
        :value="formatDuration(status?.lastRun.duration ?? null)"
        icon="i-lucide-clock"
        color="cyan"
      />
      <StatsCard
        label="Cost"
        :value="status?.lastRun.cost ? `$${status.lastRun.cost}` : '--'"
        icon="i-lucide-dollar-sign"
        color="amber"
      />
    </div>

    <!-- AI Review -->
    <div
      v-if="status?.aiReview"
      class="rounded-xl border border-neutral-800 bg-neutral-900/50 p-5 space-y-4"
    >
      <p class="text-xs uppercase tracking-wider text-neutral-500">
        AI Review (Phase 2)
      </p>
      <ProgressBar
        :current="status.aiReview.reviewed"
        :total="status.aiReview.total"
        label="Contacts reviewed"
      />
      <div class="flex gap-6 text-sm">
        <div>
          <span class="text-neutral-500">Promoted:</span>
          <span class="text-primary-400 ml-1 font-semibold">{{ status.aiReview.promoted }}</span>
        </div>
        <div>
          <span class="text-neutral-500">Demoted:</span>
          <span class="text-amber-400 ml-1 font-semibold">{{ status.aiReview.demoted }}</span>
        </div>
      </div>
    </div>

    <!-- Last Run Info -->
    <div class="rounded-xl border border-neutral-800 bg-neutral-900/50 p-5">
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
