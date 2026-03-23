<script setup lang="ts">
const open = ref(false)
const description = ref('')
const submitting = ref(false)
const pendingScreenshot = ref('')
const toast = useToast()

function onBugClick() {
  open.value = true
}

function onPaste(event: ClipboardEvent) {
  const items = event.clipboardData?.items
  if (!items) return
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      event.preventDefault()
      const blob = item.getAsFile()
      if (!blob) continue
      const reader = new FileReader()
      reader.onload = () => {
        pendingScreenshot.value = reader.result as string
      }
      reader.readAsDataURL(blob)
      break
    }
  }
}

function onFileSelect(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !file.type.startsWith('image/')) return
  const reader = new FileReader()
  reader.onload = () => {
    pendingScreenshot.value = reader.result as string
  }
  reader.readAsDataURL(file)
}

function removeScreenshot() {
  pendingScreenshot.value = ''
}

async function submit() {
  if (!description.value.trim()) return

  submitting.value = true
  try {
    const environment = {
      url: window.location.href,
      viewport: `${window.innerWidth}x${window.innerHeight}`,
      userAgent: navigator.userAgent,
      timestamp: new Date().toISOString(),
    }

    const result = await $fetch<{ issueNumber: number, issueUrl: string }>('/api/bug-report', {
      method: 'POST',
      body: {
        description: description.value.trim(),
        pageUrl: window.location.href,
        screenshot: pendingScreenshot.value,
        pageState: { route: useRoute().fullPath, page: useRoute().name },
        environment,
      },
    })

    toast.add({
      title: `Bug reported — Issue #${result.issueNumber}`,
      description: 'Thank you! We\'ll look into it.',
      color: 'success',
      duration: 5000,
    })

    open.value = false
    description.value = ''
    pendingScreenshot.value = ''
  }
  catch (err: unknown) {
    const message = err instanceof Error ? err.message : 'Failed to submit bug report'
    toast.add({
      title: 'Bug report failed',
      description: message,
      color: 'error',
      duration: 5000,
    })
  }
  finally {
    submitting.value = false
  }
}
</script>

<template>
  <!-- Floating bug report button -->
  <button
    class="fixed bottom-4 right-4 z-50 size-10 flex items-center justify-center rounded-full bg-neutral-800 border border-neutral-700 text-neutral-400 hover:text-red-400 hover:border-red-500/40 hover:bg-red-500/10 transition-all duration-200 shadow-lg"
    title="Report a bug"
    @click="onBugClick"
  >
    <UIcon name="i-lucide-bug" class="size-5" />
  </button>

  <!-- Modal -->
  <UModal v-model:open="open">
    <template #content>
      <div class="p-6 space-y-4" @paste="onPaste">
        <div class="flex items-center gap-3">
          <div class="size-10 rounded-xl bg-red-500/10 border border-red-500/20 flex items-center justify-center">
            <UIcon name="i-lucide-bug" class="size-5 text-red-400" />
          </div>
          <div>
            <h3 class="text-lg font-semibold text-neutral-100">
              Report a Bug
            </h3>
            <p class="text-xs text-neutral-500">
              Take a screenshot (Cmd+Shift+4) and paste it here, or upload an image.
            </p>
          </div>
        </div>

        <!-- Screenshot preview or upload area -->
        <div v-if="pendingScreenshot" class="relative rounded-lg border border-neutral-700 overflow-hidden">
          <img :src="pendingScreenshot" alt="Page screenshot" class="w-full opacity-70" />
          <button
            class="absolute top-2 right-2 size-6 flex items-center justify-center rounded-full bg-neutral-900/80 text-neutral-400 hover:text-red-400 transition-colors"
            title="Remove screenshot"
            @click="removeScreenshot"
          >
            <UIcon name="i-lucide-x" class="size-3.5" />
          </button>
        </div>
        <div v-else class="rounded-lg border border-dashed border-neutral-700 p-4 text-center">
          <p class="text-xs text-neutral-500 mb-2">
            Paste a screenshot (Cmd+V) or
          </p>
          <label class="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-neutral-800 border border-neutral-700 text-neutral-400 hover:text-neutral-200 cursor-pointer transition-colors">
            <UIcon name="i-lucide-upload" class="size-3.5" />
            Upload image
            <input type="file" accept="image/*" class="hidden" @change="onFileSelect" />
          </label>
        </div>

        <div>
          <label class="text-sm text-neutral-400 mb-1.5 block">What went wrong?</label>
          <textarea
            v-model="description"
            class="w-full rounded-lg bg-neutral-800 border border-neutral-700 text-neutral-200 text-sm px-3 py-2 placeholder-neutral-500 focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500/30 resize-none"
            rows="3"
            placeholder="Describe what you expected vs what happened..."
            :disabled="submitting"
            @keydown.meta.enter="submit"
          />
        </div>

        <div class="flex items-center justify-between pt-1">
          <p class="text-[11px] text-neutral-600">
            Creates a GitHub issue with your screenshot
          </p>
          <div class="flex gap-2">
            <button
              class="px-3 py-1.5 text-sm rounded-lg text-neutral-400 hover:text-neutral-200 transition-colors"
              :disabled="submitting"
              @click="open = false"
            >
              Cancel
            </button>
            <button
              class="px-4 py-1.5 text-sm rounded-lg bg-red-500/20 border border-red-500/30 text-red-400 hover:bg-red-500/30 transition-colors disabled:opacity-40"
              :disabled="!description.trim() || submitting"
              @click="submit"
            >
              <span v-if="submitting" class="flex items-center gap-2">
                <UIcon name="i-lucide-loader-2" class="size-3.5 animate-spin" />
                Submitting...
              </span>
              <span v-else>Submit Bug</span>
            </button>
          </div>
        </div>
      </div>
    </template>
  </UModal>
</template>
