<script setup lang="ts">
// Landing-page live stats — pulls non-sensitive aggregate metrics from
// /api/health (which is intentionally unauthenticated). Falls back to the
// hardcoded tiles if the API is unreachable so the landing still renders.

interface HealthResponse {
  status: string
  dashboard: { version: string; buildDate: string; gitSha: string }
  pipeline: {
    lastRunDate: string | null
    lastRunDuration: number | null
    lastRunChangesApplied: number
    lastRunAiCost: number | null
    totalRuns: number
  }
  queue: { pendingChanges: number }
  signals: { linkedinSignalsCount: number; followupCandidates: number }
}

const { data, error } = await useFetch<HealthResponse>('/api/health', {
  // Keep the landing snappy — fail quickly to fallback if the API is slow.
  timeout: 3_000,
  // Cached per server render; re-fetches on mount so visitors see fresh numbers.
  lazy: false,
  server: true,
})

const fallback = computed(() => !!error.value || !data.value || data.value.status !== 'ok')

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  } catch {
    return '—'
  }
}

function fmtCost(v: number | null): string {
  if (v === null || v === undefined) return '—'
  return v < 0.01 ? `<$0.01` : `$${v.toFixed(2)}`
}
</script>

<template>
  <div class="grid grid-cols-2 md:grid-cols-5 gap-6 text-center">
    <div>
      <div class="text-2xl md:text-3xl font-bold text-primary-400 tabular-nums">6</div>
      <div class="label-refined mt-1">Pipeline phases</div>
    </div>

    <div>
      <div class="text-2xl md:text-3xl font-bold text-neutral-100 tabular-nums">
        {{ fallback ? '5,500+' : data!.signals.followupCandidates.toLocaleString() }}
      </div>
      <div class="label-refined mt-1">
        {{ fallback ? 'Contacts managed' : 'Scored contacts' }}
      </div>
    </div>

    <div>
      <div class="text-2xl md:text-3xl font-bold text-emerald-400 tabular-nums">
        {{ fallback ? '—' : data!.signals.linkedinSignalsCount.toLocaleString() }}
      </div>
      <div class="label-refined mt-1">LinkedIn signals</div>
    </div>

    <div>
      <div class="text-2xl md:text-3xl font-bold text-cyan-400 tabular-nums">
        {{ fallback ? '<$0.02' : fmtCost(data!.pipeline.lastRunAiCost) }}
      </div>
      <div class="label-refined mt-1">
        {{ fallback ? 'Daily AI cost' : 'Last-run AI cost' }}
      </div>
    </div>

    <div>
      <div class="text-2xl md:text-3xl font-bold text-green-400 tabular-nums">
        {{ fallback ? '100%' : fmtDate(data!.pipeline.lastRunDate) }}
      </div>
      <div class="label-refined mt-1">
        {{ fallback ? 'Self-hosted' : 'Last pipeline run' }}
      </div>
    </div>
  </div>
</template>
