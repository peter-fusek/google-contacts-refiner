#!/usr/bin/env node
// Capture screenshots of ContactRefiner dashboard pages for the landing refresh.
//
// Runs against production by default (demo mode — masked PII, no login needed).
// Outputs to dashboard/public/screenshots/ at both desktop (1920×1080) and
// mobile (390×844) widths.
//
// Usage:
//   (cd dashboard && pnpm add -D playwright && npx playwright install chromium)
//   node scripts/capture_landing_screenshots.mjs
//
// Playwright lives under dashboard/node_modules (repo has no root
// package.json). We import it via an explicit file URL so `node` can resolve
// the package from scripts/ without polluting the repo root.
//
// Env overrides:
//   BASE_URL      default https://contactrefiner.com
//   OUT_DIR       default dashboard/public/screenshots
//   ONLY          comma-separated page slugs to capture (e.g. crm,signals)

import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = resolve(__dirname, '..')

const BASE_URL = process.env.BASE_URL || 'https://contactrefiner.com'
const OUT_DIR = resolve(REPO_ROOT, process.env.OUT_DIR || 'dashboard/public/screenshots')
const ONLY = (process.env.ONLY || '').split(',').map(s => s.trim()).filter(Boolean)

// Explicit file-URL import so the script resolves playwright from
// dashboard/node_modules regardless of CWD.
const playwrightEntry = pathToFileURL(
  resolve(REPO_ROOT, 'dashboard/node_modules/playwright/index.mjs'),
).href
const { chromium } = await import(playwrightEntry)

const VIEWPORTS = [
  { name: 'desktop', width: 1920, height: 1080 },
  { name: 'mobile', width: 390, height: 844 },
]

// Each target captures one dashboard page. `waitFor` gives the page a DOM
// selector it should render before the screenshot — skip if best-effort.
const TARGETS = [
  {
    slug: 'crm',
    path: '/crm',
    waitFor: 'text=/Inbox|CRM|Reached out/i',
    fullPage: false,
  },
  {
    slug: 'signals',
    path: '/signals',
    waitFor: 'text=/Candidates|Backlog|Dismissed/i',
    fullPage: false,
  },
  {
    slug: 'linkedin-crm',
    path: '/linkedin-crm',
    waitFor: 'text=/Tier|Status|Institutions/i',
    fullPage: false,
  },
  {
    slug: 'pipeline',
    path: '/pipeline',
    waitFor: 'text=/Phase|Pipeline/i',
    fullPage: false,
  },
  {
    slug: 'changelog',
    path: '/changelog',
    waitFor: null,
    fullPage: false,
  },
  {
    slug: 'analytics',
    path: '/analytics',
    waitFor: null,
    fullPage: false,
  },
]

async function captureAll() {
  await mkdir(OUT_DIR, { recursive: true })
  const browser = await chromium.launch({ headless: true })

  const manifest = { base: BASE_URL, capturedAt: new Date().toISOString(), shots: [] }

  for (const target of TARGETS) {
    if (ONLY.length && !ONLY.includes(target.slug)) continue
    for (const vp of VIEWPORTS) {
      const context = await browser.newContext({
        viewport: { width: vp.width, height: vp.height },
        // deviceScaleFactor: 1 keeps file size under the 500KB landing
        // budget — 2× retina blew the total over 2.3MB last time.
        deviceScaleFactor: 1,
        colorScheme: 'dark',
      })
      const page = await context.newPage()
      const url = `${BASE_URL}${target.path}`
      console.log(`→ ${vp.name.padEnd(7)} ${target.slug.padEnd(14)} ${url}`)
      try {
        await page.goto(url, { waitUntil: 'networkidle', timeout: 30_000 })
        if (target.waitFor) {
          await page.waitForSelector(target.waitFor, { timeout: 10_000 }).catch(() => {})
        }
        await page.waitForTimeout(800) // animations
        const filename = `${target.slug}.${vp.name}.jpg`
        const out = resolve(OUT_DIR, filename)
        await page.screenshot({ path: out, fullPage: target.fullPage, type: 'jpeg', quality: 82 })
        manifest.shots.push({ slug: target.slug, viewport: vp.name, file: filename, url })
        console.log(`  ✓ ${filename}`)
      } catch (e) {
        console.error(`  ✗ ${target.slug}/${vp.name}: ${e.message}`)
        manifest.shots.push({ slug: target.slug, viewport: vp.name, error: e.message, url })
      } finally {
        await context.close()
      }
    }
  }

  await browser.close()
  await writeFile(
    resolve(OUT_DIR, 'manifest.json'),
    JSON.stringify(manifest, null, 2),
  )
  const ok = manifest.shots.filter(s => !s.error).length
  const fail = manifest.shots.length - ok
  console.log(`\nDone: ${ok} captured, ${fail} failed. Manifest: ${OUT_DIR}/manifest.json`)
  process.exit(fail > 0 ? 1 : 0)
}

captureAll().catch((e) => {
  console.error(e)
  process.exit(2)
})
