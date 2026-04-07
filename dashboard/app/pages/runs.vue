<script setup lang="ts">
import type { PipelineRun } from '~/server/utils/gcs'

useHead({
  title: 'Pipeline Runs — Contact Refiner',
  meta: [
    { name: 'description', content: 'History of automated pipeline executions.' },
  ],
})

const { data, status, refresh } = useFetch<PipelineRun[]>('/api/pipeline-runs')

let interval: ReturnType<typeof setInterval> | undefined
onMounted(() => { interval = setInterval(refresh, 60_000) })
onUnmounted(() => { if (interval) clearInterval(interval) })
</script>

<template>
  <div class="space-y-6">
    <h1 class="text-xl font-bold text-neutral-100">
      Pipeline Runs
    </h1>
    <div v-if="status === 'error'" class="text-center py-12">
      <UIcon name="i-lucide-alert-triangle" class="size-8 text-red-500 mx-auto mb-3" />
      <p class="text-red-400 text-sm">Failed to load pipeline runs</p>
      <UButton label="Retry" size="sm" variant="soft" class="mt-3" icon="i-lucide-refresh-cw" @click="refresh()" />
    </div>
    <RunHistoryTable v-else :runs="data ?? []" :loading="status === 'pending'" />
  </div>
</template>
