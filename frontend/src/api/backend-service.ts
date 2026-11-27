import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  EntryRow,
  FeedbackEligibility,
  OverviewResult,
  PageResult,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
  requestKey?: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values, request_key: requestKey }),
  })
  return await response.json()
}

/**
 * 核对反馈引用当前是否具备登记/审核改进方案的资格。
 * 断网重发同一申请时，主任可凭此判断是沿用待补签收资格，
 * 还是等签收完成后从原申请继续建方案。
 */
export async function checkFeedbackEligibility(
  feedbackRef: string,
  responsibleTeacher?: string,
): Promise<FeedbackEligibility> {
  const params = new URLSearchParams({ feedback_ref: feedbackRef })
  if (responsibleTeacher) params.set('responsible_teacher', responsibleTeacher)
  const response = await request(`/api/improve_plan/feedback-eligibility?${params.toString()}`)
  if (!response.ok) throw new Error('反馈签收资格核对失败')
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}
