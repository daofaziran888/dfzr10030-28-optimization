import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'

const API = process.env.E2E_API_BASE || 'http://127.0.0.1:8000'

type PeriodPayload = {
  统计周期: string
  最新版本: number
  报告: Record<string, unknown>
  失败明细: Record<string, unknown>[]
  指标依据: Record<string, unknown>[]
  核对: { 结论: string; 可核对: boolean; 失败量合计: number }
}

async function periodState(request: APIRequestContext, period: string): Promise<PeriodPayload> {
  const response = await request.get(`${API}/api/report/periods/${period}`)
  expect(response.ok()).toBeTruthy()
  return (await response.json()) as PeriodPayload
}

async function createPackage(
  request: APIRequestContext, values: Record<string, unknown>,
): Promise<{ ok: boolean; message: string; entry: Record<string, any> | null }> {
  const response = await request.post(`${API}/api/report/export-packages`, { data: { values } })
  return (await response.json()) as { ok: boolean; message: string; entry: Record<string, any> | null }
}

async function addCorrection(request: APIRequestContext, period: string, failureId: string) {
  const response = await request.post(`${API}/api/report/periods/${period}/events`, {
    data: {
      values: {
        事件类型: '更正事件',
        说明: 'E2E 更正事件',
        总采集量增量: 7,
        更正明细: [{ 失败编号: failureId, 失败原因: 'E2E 复核后更正' }],
      },
    },
  })
  expect((await response.json()).ok).toBeTruthy()
}

async function packageForPeriod(
  request: APIRequestContext, period: string,
): Promise<Record<string, any> | null> {
  const response = await request.get(`${API}/api/report/export-packages?period=${period}`)
  const payload = await response.json()
  const items: Record<string, any>[] = payload.items || []
  if (!items.length) return null
  return items.reduce((best, item) => (item['批次版本'] > best['批次版本'] ? item : best), items[0])
}

function csvCell(text: string, column: string): string {
  const line = text.split('\n').find((row) => row.startsWith(column + ','))
  return line ? line.split(',')[1] : ''
}

test('完整报告导出包：周期打包、版本冻结、打包下载与跨模块回查', async ({ page, request }) => {
  test.setTimeout(180_000)
  const browserErrors: string[] = []
  page.on('pageerror', (error) => browserErrors.push(error.message))

  await page.goto('/report')
  await expect(page.getByTestId('period-board')).toBeVisible({ timeout: 15_000 })
  const periodSelect = page.getByTestId('period-select')
  await expect(periodSelect).not.toHaveValue('', { timeout: 15_000 })
  await expect(page.locator('[data-testid="failure-table"] tbody tr').first()).toBeVisible({ timeout: 15_000 })
  const period = await periodSelect.inputValue()
  const live = await periodState(request, period)
  expect(live.核对.可核对).toBe(true)

  // 页面优先展示已冻结的最新包；没有包时展示实时周期视图。两者都必须自洽。
  const existing = await packageForPeriod(request, period)
  const shownVersion = existing ? existing['批次版本'] : live.最新版本
  const reference = existing
    ? { 报告: existing['报告'], 失败明细: existing['失败明细'], 指标依据: existing['指标依据'] }
    : live
  expect(await page.getByTestId('frozen-version').innerText()).toBe(`V${shownVersion}`)
  await expect(page.getByTestId('report-table')).toContainText(String(reference.报告['总采集量']))
  await expect(page.locator('[data-testid="failure-table"] tbody tr')).toHaveCount(reference.失败明细.length)
  await expect(page.locator('[data-testid="metric-table"] tbody tr')).toHaveCount(reference.指标依据.length)
  await expect(page.getByTestId('check-result')).toContainText('通过')

  // 导出：CSV 同时带汇总、失败明细与指标依据，且与页面版本一致
  const downloadPromise = page.waitForEvent('download')
  await page.getByTestId('export').click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe(`report-${period}.csv`)
  const csvPath = await download.path()
  const csvText = await readFile(csvPath!, 'utf-8')
  expect(csvText).toContain('报告汇总')
  expect(csvText).toContain('失败明细')
  expect(csvText).toContain('指标依据')
  expect(csvCell(csvText, '批次版本')).toBe(String(shownVersion))
  expect(csvCell(csvText, '失败量')).toBe(String(reference.报告['失败量']))

  // 同一版本重复提交只允许一次成功，其余被拦下
  const duplicate = await createPackage(request, { 统计周期: period, 批次版本: shownVersion })
  expect(duplicate.ok).toBe(false)
  expect(duplicate.message).toContain('只允许提交一次')
  expect(duplicate.entry?.['包编号']).toBeTruthy()

  // 打包 ZIP：包含三个明细文件与 manifest
  const zipPromise = page.waitForEvent('download')
  await page.getByTestId('download-bundle').click()
  const zip = await zipPromise
  expect(zip.suggestedFilename()).toContain('.zip')
  const zipPath = await zip.path()
  const zipBytes = await readFile(zipPath!)
  expect(zipBytes.byteLength).toBeGreaterThan(0)

  // 导出期间新增更正事件：已冻结包不变，线上版本前进
  const frozenVersion = await page.getByTestId('frozen-version').innerText()
  const firstFailure = String(reference.失败明细[0]['失败编号'])
  await addCorrection(request, period, firstFailure)
  const afterEvent = await periodState(request, period)
  expect(afterEvent.最新版本).toBe(live.最新版本 + 1)
  await page.getByTestId('refresh-period').click()
  await expect(page.getByTestId('board-message')).toContainText('已刷新')
  await expect(page.getByTestId('frozen-version')).toHaveText(frozenVersion)
  await page.getByTestId('recompute-package').click()
  await expect(page.getByTestId('board-message')).toContainText('一致')

  // 合并更正后重新生成：版本前进，报告与明细仍同版本
  await page.getByTestId('merge-correction').click()
  await expect(page.getByTestId('board-message')).toContainText('已生成', { timeout: 15_000 })
  await expect(page.getByTestId('frozen-version')).toHaveText(`V${afterEvent.最新版本}`)
  await expect(page.getByTestId('check-result')).toContainText('通过')
  await expect(page.locator('[data-testid="failure-table"] tbody tr')).toHaveCount(afterEvent.失败明细.length)

  // 采集监控接力：按报告周期核对线上指标并生成待审清单
  await addCorrection(request, period, firstFailure)
  await page.goto('/monitor')
  await expect(page.getByTestId('monitor-package-table')).toBeVisible({ timeout: 15_000 })
  await page.getByTestId('sync-package').click()
  await expect(page.getByTestId('monitor-message')).toContainText('待审', { timeout: 15_000 })
  await expect(page.locator('[data-testid="review-table"] tbody tr').first()).toBeVisible()

  // 采集报告按批次回查监控回写的业务记录
  const records = await request.get(`${API}/api/report/monitor-records?period=${period}`)
  const payload = await records.json()
  expect(payload.items.length).toBeGreaterThan(0)
  expect(payload.items[0]['包编号']).toBeTruthy()
  expect(payload.items[0]['批次版本']).toBeTruthy()

  expect(browserErrors).toEqual([])
})

test('历史版本导出包：周期继续演进后仍按原批次版本重算', async ({ request }) => {
  const period = '2026-W36'
  const before = await periodState(request, period)
  const firstFailure = String(before.失败明细[0]['失败编号'])
  // 先推进版本再取包，避免重复运行同一用例时撞上"同版本只提交一次"。
  if (await packageForPeriod(request, period)) {
    await addCorrection(request, period, firstFailure)
  }
  const created = await createPackage(request, { 统计周期: period })
  expect(created.ok).toBe(true)
  const packageNo = created.entry!['包编号'] as string
  const frozen = created.entry!['批次版本'] as number

  await addCorrection(request, period, firstFailure)
  const recompute = await request.get(`${API}/api/report/export-packages/${packageNo}/recompute`)
  const result = await recompute.json()
  expect(result.一致).toBe(true)
  expect(result.重算版本).toBe(frozen)

  const stored = await request.get(`${API}/api/report/export-packages/${packageNo}`)
  const storedBody = await stored.json()
  expect(storedBody.批次版本).toBe(frozen)
  expect(storedBody.可合并更正).toBe(true)
  expect(storedBody.来源书签).toBeTruthy()

  const csv = await request.get(`${API}/api/report/export-packages/${packageNo}/download`)
  const text = await csv.text()
  expect(csvCell(text, '批次版本')).toBe(String(frozen))
})
