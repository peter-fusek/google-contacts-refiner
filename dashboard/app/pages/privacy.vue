<script setup lang="ts">
definePageMeta({ layout: false })

useHead({
  title: 'Privacy & Data Processing — Contact Refiner',
  meta: [
    { name: 'description', content: 'How Contact Refiner processes your Google Contacts data. Self-hosted, no third-party sharing, full transparency.' },
  ],
})
</script>

<template>
  <div class="min-h-screen bg-neutral-950 text-neutral-200">
    <!-- Nav -->
    <nav class="fixed top-0 inset-x-0 z-50 border-b border-neutral-800/50 bg-neutral-950/80 backdrop-blur-lg">
      <div class="max-w-5xl mx-auto flex items-center justify-between px-6 h-14">
        <NuxtLink to="/" class="flex items-center gap-2">
          <div class="size-8 rounded-lg bg-primary-500/20 flex items-center justify-center">
            <UIcon name="i-lucide-radar" class="size-5 text-primary-400" />
          </div>
          <span class="text-sm font-semibold text-primary-400">Contact Refiner</span>
        </NuxtLink>
        <NuxtLink
          to="/dashboard"
          class="text-xs px-3 py-1.5 rounded-lg border border-neutral-700 text-neutral-400 hover:text-neutral-200 hover:border-neutral-600 transition-colors"
        >
          Dashboard
        </NuxtLink>
      </div>
    </nav>

    <!-- Content -->
    <main class="pt-24 pb-20 px-6">
      <div class="max-w-3xl mx-auto prose prose-invert prose-sm prose-neutral">
        <h1 class="text-3xl font-bold text-neutral-100">
          Privacy & Data Processing
        </h1>
        <p class="text-neutral-400 text-base">
          Last updated: March 14, 2026
        </p>

        <h2>What Contact Refiner does</h2>
        <p>
          Contact Refiner is a self-hosted tool that analyzes and cleans up your Google Contacts.
          It uses rule-based analysis and AI review to suggest formatting fixes, diacritics restoration,
          and data normalization. You review and approve every change before it is applied.
        </p>

        <h2>Data we access</h2>
        <p>
          Contact Refiner requests the following Google API scopes through OAuth2:
        </p>
        <table class="w-full text-sm">
          <thead>
            <tr>
              <th class="text-left text-neutral-300">Data</th>
              <th class="text-left text-neutral-300">Purpose</th>
              <th class="text-left text-neutral-300">Access type</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Google Contacts</td>
              <td>Analyze fields, suggest fixes, apply approved changes</td>
              <td>Read + Write</td>
            </tr>
            <tr>
              <td>Gmail (headers only)</td>
              <td>Detect last interaction date for activity tagging</td>
              <td>Read-only (metadata, not message bodies)</td>
            </tr>
            <tr>
              <td>Google Calendar</td>
              <td>Detect shared events for activity tagging</td>
              <td>Read-only</td>
            </tr>
          </tbody>
        </table>

        <h2>How data is processed</h2>
        <ol>
          <li>
            <strong>Rule-based analysis</strong> — 25 rule categories check formatting, diacritics,
            phone numbers, email validity, and organization names. This runs entirely in your
            infrastructure with no external calls.
          </li>
          <li>
            <strong>AI review</strong> — Ambiguous changes (MEDIUM confidence) are sent to
            the Claude API (Anthropic) for a second opinion. Only the contact field values
            (names, phones, emails) are sent — no full contact profiles, no Gmail content,
            no calendar details.
          </li>
          <li>
            <strong>Human review</strong> — All changes require your explicit approval on the
            dashboard before being applied to your Google account.
          </li>
        </ol>

        <h2>LinkedIn matching</h2>
        <p>
          Contact Refiner can optionally match your Google Contacts against a LinkedIn connections
          export (CSV file you download from LinkedIn). This matching:
        </p>
        <ul>
          <li>Runs locally — the LinkedIn CSV is processed on your infrastructure only</li>
          <li>Uses fuzzy name matching to find potential matches</li>
          <li>Does not upload your LinkedIn data to any external service</li>
          <li>Does not access the LinkedIn API or scrape LinkedIn</li>
        </ul>
        <p>
          Per GDPR Article 14, this constitutes processing personal data obtained from a source
          other than the data subject (your LinkedIn export). The legal basis is legitimate interest
          in maintaining accurate contact information.
        </p>

        <h2>Data storage</h2>
        <ul>
          <li>
            <strong>Contact data</strong> stays in your Google account. Contact Refiner reads it,
            processes it in memory, and writes approved changes back. No contact data is stored
            permanently outside Google.
          </li>
          <li>
            <strong>Analysis results</strong> (change suggestions, review decisions, changelogs)
            are stored in a Google Cloud Storage bucket under your control.
          </li>
          <li>
            <strong>Learning memory</strong> (which corrections you approved/rejected) is stored
            in the same GCS bucket to improve future suggestions.
          </li>
          <li>
            <strong>AI review requests</strong> are sent to Anthropic's Claude API. Anthropic's
            data retention policy applies to these requests. No contact data is used for
            model training.
          </li>
        </ul>

        <h2>Third-party services</h2>
        <table class="w-full text-sm">
          <thead>
            <tr>
              <th class="text-left text-neutral-300">Service</th>
              <th class="text-left text-neutral-300">Data shared</th>
              <th class="text-left text-neutral-300">Purpose</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Google People API</td>
              <td>OAuth token</td>
              <td>Read/write contacts</td>
            </tr>
            <tr>
              <td>Gmail API</td>
              <td>OAuth token</td>
              <td>Read email headers (metadata only)</td>
            </tr>
            <tr>
              <td>Google Calendar API</td>
              <td>OAuth token</td>
              <td>Read event participants</td>
            </tr>
            <tr>
              <td>Anthropic Claude API</td>
              <td>Contact field values (names, phones, emails)</td>
              <td>AI review of ambiguous changes</td>
            </tr>
            <tr>
              <td>Google Cloud Storage</td>
              <td>Analysis results, changelogs, memory</td>
              <td>Persistent storage (your bucket)</td>
            </tr>
          </tbody>
        </table>

        <h2>Data retention</h2>
        <ul>
          <li>Contact backups: kept in GCS until you delete them</li>
          <li>Changelogs: kept indefinitely for audit trail</li>
          <li>Review sessions: kept indefinitely for learning</li>
          <li>Interaction cache: refreshed daily, 90-day lookback</li>
          <li>Queue statistics: 90-day rolling window</li>
        </ul>

        <h2>Your rights</h2>
        <p>
          Since Contact Refiner is self-hosted, you have full control over all data:
        </p>
        <ul>
          <li>Revoke Google OAuth access at any time via your Google Account settings</li>
          <li>Delete all stored data by clearing your GCS bucket</li>
          <li>Roll back any changes using the built-in rollback feature</li>
          <li>Export all analysis data from GCS at any time</li>
        </ul>

        <h2>Contact</h2>
        <p>
          Contact Refiner is developed by
          <a href="https://instarea.sk" target="_blank" rel="noopener" class="text-primary-400 hover:text-primary-300">Instarea s.r.o.</a>,
          Bratislava, Slovakia.
        </p>
        <p>
          For privacy-related questions, contact
          <a href="mailto:peter.fusek@instarea.sk" class="text-primary-400 hover:text-primary-300">peter.fusek@instarea.sk</a>.
        </p>
      </div>
    </main>

    <!-- Footer -->
    <footer class="py-8 px-6 border-t border-neutral-800/50">
      <div class="max-w-5xl mx-auto flex items-center justify-between text-xs text-neutral-600">
        <span>Contact Refiner by <a href="https://instarea.sk" target="_blank" rel="noopener" class="text-neutral-500 hover:text-neutral-400 transition-colors">Instarea</a></span>
        <div class="flex items-center gap-4">
          <NuxtLink to="/privacy" class="text-neutral-500 hover:text-neutral-400 transition-colors">Privacy</NuxtLink>
          <a href="https://github.com/peter-fusek/google-contacts-refiner" target="_blank" rel="noopener" class="text-neutral-500 hover:text-neutral-400 transition-colors">GitHub</a>
        </div>
      </div>
    </footer>
  </div>
</template>
