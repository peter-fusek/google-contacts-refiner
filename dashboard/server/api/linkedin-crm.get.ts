import type { LICRMData, LICRMResponse, LIContactStatus } from '../utils/types'
import { isDemoMode, maskFullName } from '../utils/demo'
import { getLinkedInCRMData } from '../utils/linkedin-crm-data'

function maskLICRMData(data: LICRMData): LICRMData {
  return {
    ...data,
    contacts: data.contacts.map(c => ({
      ...c,
      name: maskFullName(c.name),
      linkedinUrl: c.linkedinUrl ? 'https://www.linkedin.com/in/***' : '',
      notes: c.notes ? '[hidden in demo]' : '',
    })),
    dmLog: data.dmLog.map(d => ({
      ...d,
      contactName: maskFullName(d.contactName),
    })),
    institutions: data.institutions.map(inst => ({
      ...inst,
      contactStrategy: inst.contactStrategy ? '[hidden in demo]' : '',
      notes: inst.notes ? '[hidden in demo]' : '',
    })),
  }
}

export default defineEventHandler(async (event): Promise<LICRMResponse> => {
  const demo = await isDemoMode(event)

  const data = await getLinkedInCRMData()
  const finalData = demo ? maskLICRMData(data) : data

  const contacts = finalData.contacts
  const statusCounts: Record<string, number> = {}
  for (const c of contacts) {
    statusCounts[c.status] = (statusCounts[c.status] ?? 0) + 1
  }

  function countByStatus(status: LIContactStatus): number {
    return statusCounts[status] ?? 0
  }

  const latestSnapshot = finalData.followerSnapshots[finalData.followerSnapshots.length - 1]
  const totalSent = finalData.miningRuns.reduce((sum, r) => sum + r.sent, 0)
  const accepted = Math.round(totalSent * 0.88)

  return {
    data: finalData,
    stats: {
      totalContacts: contacts.length,
      connected: countByStatus('CONNECTED'),
      pending: countByStatus('PENDING'),
      creatorMode: countByStatus('CREATOR_MODE'),
      dmsSent: countByStatus('DM_SENT'),
      dmsSkipped: countByStatus('DM_SKIPPED'),
      responded: countByStatus('RESPONDED'),
      followers: latestSnapshot?.followers ?? 0,
      followerDelta: finalData.followerSnapshots.reduce((sum, s) => sum + (s.delta ?? 0), 0),
      acceptanceRate: `~${Math.round((accepted / totalSent) * 100)}%`,
    },
  }
})
