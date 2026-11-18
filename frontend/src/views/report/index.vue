<template>
  <section class="page" data-module="report">
    <header class="page-head">
      <div>
        <h2>采集报告</h2>
        <p class="page-desc">按统计周期把报告汇总、失败明细和指标依据冻结成完整导出包，下载包内三部分可按批次版本互相核对。</p>
      </div>
      <div class="page-actions">
        <label class="inline-field">
          <span>统计周期</span>
          <select v-model="period" data-testid="period-select" @change="loadPeriod">
            <option v-for="item in periodOptions" :key="item.统计周期" :value="item.统计周期">
              {{ item.统计周期 }}（{{ item.周期区间 }}）
            </option>
          </select>
        </label>
        <label class="inline-field">
          <span>导出边界</span>
          <select v-model="mode" data-testid="mode-select">
            <option value="freeze">冻结点前置（即时可用）</option>
            <option value="merge">数据完整前置（合并更正）</option>
          </select>
        </label>
        <button class="btn primary" data-testid="export" type="button" :disabled="exporting" @click="exportPackage">
          {{ exporting ? '打包中…' : '导出完整报告包' }}
        </button>
      </div>
    </header>

    <div class="analysis-summary">
      <article v-for="m in summaryMetrics" :key="m.label" class="analysis-card" data-testid="kpi-card">
        <span style="font-size:12px;color:var(--muted)">{{ m.label }}</span>
        <strong style="font-size:22px;display:block">{{ m.value }}</strong>
        <span :class="m.trend > 0 ? 'trend-up' : 'trend-down'" style="font-size:12px">{{ m.trend > 0 ? '↑' : '↓' }}{{ Math.abs(m.trend) }}%</span>
      </article>
      <article class="analysis-card" data-testid="kpi-card">
        <span style="font-size:12px;color:var(--muted)">冻结版本</span>
        <strong style="font-size:22px;display:block" data-testid="frozen-version">V{{ frozenVersion }}</strong>
        <span style="font-size:12px;color:var(--muted)">{{ packageNo || '尚未生成导出包' }}</span>
      </article>
    </div>

    <section class="period-board" data-testid="period-board">
      <div class="period-meta">
        <span>周期区间：{{ periodDetail.周期区间 || '—' }}</span>
        <span>线上版本：V{{ periodDetail.最新版本 || 1 }}</span>
        <span data-testid="package-version">包批次版本：V{{ frozenVersion }}</span>
        <span>导出边界：{{ currentPackage?.导出边界 || '—' }}</span>
        <span>来源书签：{{ currentPackage?.来源书签 || '—' }}</span>
        <span :class="checksPassed ? 'trend-up' : 'error-text'" data-testid="check-result">
          核对：{{ displayDetail.核对?.结论 || '—' }}
        </span>
        <span v-if="pendingEvents.length" class="error-text" data-testid="pending-hint">
          待合并更正 {{ pendingEvents.length }} 条
        </span>
      </div>

      <div class="page-actions" style="margin:10px 0">
        <button class="btn" data-testid="download-report" type="button" @click="downloadCurrent('csv')">下载可核对 CSV</button>
        <button class="btn" data-testid="download-bundle" type="button" @click="downloadCurrent('zip')">打包下载 ZIP</button>
        <button class="btn" data-testid="merge-correction" type="button" :disabled="!currentPackage?.可合并更正" @click="mergeCorrection">
          合并更正后重新生成
        </button>
        <button class="btn" data-testid="recompute-package" type="button" @click="recomputeCurrent">按批次版本重算</button>
        <button class="btn" data-testid="refresh-period" type="button" @click="refreshPeriod">刷新周期状态</button>
        <button class="btn" data-testid="verify-package" type="button" @click="verifyCurrent">提交采集监控核查</button>
      </div>
      <p v-if="boardMessage" class="board-message" data-testid="board-message">{{ boardMessage }}</p>

      <div class="deliverable-grid">
        <article>
          <h3>报告汇总</h3>
          <table class="data-table" data-testid="report-table">
            <tbody>
              <tr v-for="field in reportFields" :key="field">
                <th>{{ field }}</th>
                <td>{{ displayDetail.报告?.[field] ?? '—' }}</td>
              </tr>
            </tbody>
          </table>
        </article>
        <article>
          <h3>核对结果</h3>
          <table class="data-table" data-testid="check-table">
            <thead><tr><th>检查项</th><th>结果</th><th>说明</th></tr></thead>
            <tbody>
              <tr v-for="item in displayDetail.核对?.项目 || []" :key="item.检查项">
                <td>{{ item.检查项 }}</td>
                <td :class="item.结果 === '通过' ? 'trend-up' : 'error-text'">{{ item.结果 }}</td>
                <td>{{ item.说明 }}</td>
              </tr>
            </tbody>
          </table>
        </article>
      </div>

      <h3>失败明细（{{ displayDetail.失败明细?.length || 0 }} 条，合计 {{ displayDetail.核对?.失败量合计 ?? 0 }} 条记录）</h3>
      <table class="data-table" data-testid="failure-table">
        <thead><tr><th v-for="field in failureFields" :key="field">{{ field }}</th></tr></thead>
        <tbody>
          <tr v-for="row in displayDetail.失败明细 || []" :key="row.失败编号">
            <td>{{ row.失败编号 }}</td>
            <td>{{ row.采集源 }}</td>
            <td>{{ row.失败环节 }}</td>
            <td>{{ row.失败原因 }}</td>
            <td>{{ row.发生时间 }}</td>
            <td>{{ row.记录数 }}</td>
            <td>{{ row.批次版本 }}</td>
            <td>{{ row.来源事件 }}</td>
            <td>{{ row.数据状态 }}</td>
          </tr>
        </tbody>
      </table>

      <h3>指标依据（{{ displayDetail.指标依据?.length || 0 }} 项）</h3>
      <table class="data-table" data-testid="metric-table">
        <thead><tr><th>指标名称</th><th>指标值</th><th>计算口径</th><th>依据来源</th><th>批次版本</th></tr></thead>
        <tbody>
          <tr v-for="row in displayDetail.指标依据 || []" :key="row.指标名称">
            <td>{{ row.指标名称 }}</td>
            <td>{{ row.指标值 }}</td>
            <td>{{ row.计算口径 }}</td>
            <td>{{ row.依据来源 }}</td>
            <td>{{ row.批次版本 }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="history-block">
      <h3>导出包历史（按批次回查）</h3>
      <table class="data-table" data-testid="package-table">
        <thead>
          <tr>
            <th>包编号</th><th>统计周期</th><th>批次版本</th><th>冻结版本</th><th>导出边界</th>
            <th>状态</th><th>可核对</th><th>生成时间</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="pkg in packages" :key="pkg.包编号" :data-testid="`package-${pkg.包编号}`">
            <td>{{ pkg.包编号 }}</td>
            <td>{{ pkg.统计周期 }}</td>
            <td>{{ pkg.批次版本 }}</td>
            <td>{{ pkg.冻结版本 }}</td>
            <td>{{ pkg.导出边界 }}</td>
            <td>{{ pkg.状态 }}</td>
            <td>{{ pkg.可核对 ? '可核对' : '待核对' }}</td>
            <td>{{ pkg.生成时间 }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="downloadPackage(pkg.包编号, 'csv')">下载</button>
              <button class="link" type="button" @click="downloadPackage(pkg.包编号, 'zip')">打包</button>
              <button class="link" type="button" @click="selectPackage(pkg)">回查</button>
            </td>
          </tr>
          <tr v-if="!packages.length">
            <td colspan="9" class="empty-state">当前还没有导出包，选择统计周期后点“导出完整报告包”</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="history-block">
      <h3>采集监控回写记录（采集报告按批次回查）</h3>
      <table class="data-table" data-testid="monitor-record-table">
        <thead>
          <tr><th>核查编号</th><th>包编号</th><th>批次版本</th><th>结论</th><th>待审条数</th><th>核对时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="record in monitorRecords" :key="record.核查编号">
            <td>{{ record.核查编号 }}</td>
            <td>{{ record.包编号 }}</td>
            <td>{{ record.批次版本 }}</td>
            <td>{{ record.结论 }}</td>
            <td>{{ record.待审清单?.length || 0 }}</td>
            <td>{{ record.核对时间 }}</td>
          </tr>
          <tr v-if="!monitorRecords.length">
            <td colspan="6" class="empty-state">采集监控尚未回写核查记录</td>
          </tr>
        </tbody>
      </table>
    </section>

    <form class="analysis-form" @submit.prevent="runAnalysis">
      <label v-for="field in paramFields" :key="field"><span>{{ field }}</span>
        <select v-if="field.includes('方法')||field.includes('策略')||field.includes('粒度')" v-model="params[field]"><option v-for="o in options[field]" :key="o" :value="o">{{ o }}</option></select>
        <input v-else v-model="params[field]" :placeholder="`请输入${field}`" />
      </label>
      <button class="btn primary" type="submit" data-testid="run-analysis">运行分析</button>
    </form>
    <div v-if="chartData.length" class="analysis-chart">
      <div v-for="(bar, i) in chartData" :key="i" class="analysis-bar" :style="{ height: barHeight(bar.value) + 'px' }"><span>{{ bar.label }}</span></div>
    </div>
    <table class="data-table" v-if="results.length" data-testid="analysis-table">
      <thead><tr><th v-for="c in resultColumns" :key="c">{{ c }}</th></tr></thead>
      <tbody><tr v-for="row in results" :key="row.id"><td v-for="c in resultColumns" :key="c">{{ row[c] ?? '—' }}</td></tr></tbody>
    </table>
    <p v-if="!results.length && !loading" style="text-align:center;color:var(--muted)">请配置参数后运行分析</p>
  </section>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { request } from '@/api/client'
const ENDPOINT = '/api/report'
const summaryMetrics = ref<{label:string;value:number;trend:number}[]>([])
const results = ref<any[]>([]); const chartData = ref<{label:string;value:number}[]>([])
const loading = ref(false)
const params = ref<Record<string,string>>({})
const paramFields = ["报告编号", "采集源", "统计周期", "总采集量", "成功量", "失败量", "平均耗秒", "生成时间"].filter((f: string) => !['编号','名称','版本'].includes(f)).slice(0, 4)
const options: Record<string,string[]> = { '统计粒度': ['按日','按周','按月'], '分组字段': ['全量','按类型','按区域'], '比较方法': ['环比','同比','绝对值'], '时间范围': ['最近7天','最近30天','最近90天'] }
const resultColumns = computed(() => ["报告编号", "采集源", "统计周期", "总采集量", "成功量", "失败量", "平均耗秒", "生成时间"].slice(0, 5))
function barHeight(v: number) { const max = Math.max(...chartData.value.map(d => d.value), 1); return Math.max(10, (v / max) * 140) }
async function fetchSummary() { try { const res = await request(`${ENDPOINT}/summary`); summaryMetrics.value = (await res.json()).metrics || [] } catch(e) {} }

const reportFields = ["报告编号", "采集源", "统计周期", "总采集量", "成功量", "失败量", "平均耗秒", "生成时间", "批次版本", "冻结版本", "来源书签"]
const failureFields = ["失败编号", "采集源", "失败环节", "失败原因", "发生时间", "记录数", "批次版本", "来源事件", "数据状态"]
const period = ref('')
const mode = ref('freeze')
const periodOptions = ref<{统计周期:string;周期区间:string;最新版本:number}[]>([])
const periodDetail = ref<Record<string, any>>({})
const packages = ref<any[]>([])
const monitorRecords = ref<any[]>([])
const currentPackage = ref<any>(null)
const packageNo = ref('')
const frozenVersion = ref(1)
const pendingEvents = ref<any[]>([])
const boardMessage = ref('')
const exporting = ref(false)
const checksPassed = computed(() => displayDetail.value.核对?.可核对 === true)
// 选中历史包时展示包内冻结内容；没有包时才退回实时周期视图。
const displayDetail = computed<Record<string, any>>(() => {
  const pkg = currentPackage.value
  if (pkg && pkg.批次版本 < (periodDetail.value.最新版本 || 1)) {
    return {
      周期区间: pkg.周期区间,
      最新版本: periodDetail.value.最新版本,
      报告: pkg.报告,
      失败明细: pkg.失败明细,
      指标依据: pkg.指标依据,
      核对: pkg.核对,
    }
  }
  return periodDetail.value
})
const currentPeriodPackages = computed(() => packages.value.filter(p => p.统计周期 === period.value))

function pickPackage(list: any[]) {
  const candidates = list.filter(p => p.统计周期 === period.value)
  if (!candidates.length) return null
  return candidates.reduce((best, item) => (item.批次版本 > best.批次版本 ? item : best), candidates[0])
}

function syncFrozenFrom(pkg: any | null) {
  if (pkg) {
    currentPackage.value = pkg
    packageNo.value = pkg.包编号
    frozenVersion.value = pkg.批次版本
    pendingEvents.value = pkg.待合并更正 || []
    return
  }
  currentPackage.value = null
  packageNo.value = ''
  frozenVersion.value = periodDetail.value?.最新版本 || 1
  pendingEvents.value = periodDetail.value?.待应用事件 || []
}

async function loadPeriod() {
  boardMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/periods/${encodeURIComponent(period.value)}`)
    if (!res.ok) throw new Error('统计周期读取失败')
    periodDetail.value = await res.json()
    await loadPackages()
    const latest = pickPackage(packages.value)
    syncFrozenFrom(latest)
    await loadMonitorRecords()
  } catch (error) {
    boardMessage.value = error instanceof Error ? error.message : '统计周期读取失败'
  }
}

async function loadPeriods() {
  const res = await request(`${ENDPOINT}/periods`)
  if (!res.ok) throw new Error('统计周期列表读取失败')
  const data = await res.json()
  periodOptions.value = data.periods || []
  if (!period.value) period.value = periodOptions.value[periodOptions.value.length - 1]?.统计周期 || ''
}

async function loadPackages() {
  const res = await request(`${ENDPOINT}/export-packages?period=${encodeURIComponent(period.value)}`)
  const data = await res.json()
  packages.value = data.items || []
}

async function loadMonitorRecords() {
  const res = await request(`${ENDPOINT}/monitor-records?period=${encodeURIComponent(period.value)}`)
  if (!res.ok) { monitorRecords.value = []; return }
  const data = await res.json()
  monitorRecords.value = data.items || []
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

async function exportPackage() {
  exporting.value = true
  boardMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/export?period=${encodeURIComponent(period.value)}&mode=${encodeURIComponent(mode.value)}`)
    if (!res.ok) throw new Error('导出失败，请稍后重试')
    const filename = `report-${period.value}.csv`
    triggerDownload(await res.blob(), filename)
    packageNo.value = res.headers.get('X-Report-Package') || packageNo.value
    frozenVersion.value = Number(res.headers.get('X-Report-Version') || frozenVersion.value)
    boardMessage.value = `已生成完整报告导出包 ${packageNo.value}（冻结版本 V${frozenVersion.value}）`
    await loadPackages()
    const pkg = pickPackage(packages.value)
    if (pkg) syncFrozenFrom(pkg)
  } catch (error) {
    boardMessage.value = error instanceof Error ? error.message : '导出失败'
  } finally {
    exporting.value = false
  }
}

async function downloadCurrent(kind: 'csv' | 'zip') {
  boardMessage.value = ''
  if (!packageNo.value) {
    boardMessage.value = '请先生成完整报告导出包'
    return
  }
  try {
    const suffix = kind === 'zip' ? 'bundle' : 'download'
    const res = await request(`${ENDPOINT}/export-packages/${encodeURIComponent(packageNo.value)}/${suffix}`)
    if (!res.ok) throw new Error('下载失败，请稍后重试')
    triggerDownload(await res.blob(), `${packageNo.value}-${period.value}.${kind === 'zip' ? 'zip' : 'csv'}`)
  } catch (error) {
    boardMessage.value = error instanceof Error ? error.message : '下载失败'
  }
}

async function downloadPackage(no: string, kind: 'csv' | 'zip') {
  try {
    const suffix = kind === 'zip' ? 'bundle' : 'download'
    const res = await request(`${ENDPOINT}/export-packages/${encodeURIComponent(no)}/${suffix}`)
    if (!res.ok) throw new Error('下载失败')
    triggerDownload(await res.blob(), `${no}.${kind === 'zip' ? 'zip' : 'csv'}`)
  } catch (error) {
    boardMessage.value = error instanceof Error ? error.message : '下载失败'
  }
}

function selectPackage(pkg: any) {
  syncFrozenFrom(pkg)
  boardMessage.value = `已回查导出包 ${pkg.包编号}，冻结版本 V${pkg.批次版本}`
}

async function mergeCorrection() {
  if (!packageNo.value) return
  try {
    const res = await request(`${ENDPOINT}/export-packages/${encodeURIComponent(packageNo.value)}/merge`, {
      method: 'POST', body: JSON.stringify({ values: {} }),
    })
    const data = await res.json()
    if (!data.ok) throw new Error(data.message || '合并更正失败')
    boardMessage.value = data.message
    await loadPackages()
    if (data.entry) syncFrozenFrom(data.entry)
    await loadMonitorRecords()
  } catch (error) {
    boardMessage.value = error instanceof Error ? error.message : '合并更正失败'
  }
}

async function recomputeCurrent() {
  if (!packageNo.value) return
  try {
    const res = await request(`${ENDPOINT}/export-packages/${encodeURIComponent(packageNo.value)}/recompute`)
    const data = await res.json()
    if (!res.ok) throw new Error(data.detail || '重算失败')
    // 重算后刷新周期视图，让"待合并更正"与合并按钮跟随线上版本更新。
    await loadPeriod()
    boardMessage.value = `${data.说明}（批次版本 V${data.批次版本}，线上版本 V${data.线上版本}）`
  } catch (error) {
    boardMessage.value = error instanceof Error ? error.message : '重算失败'
  }
}

async function refreshPeriod() {
  await loadPeriod()
  boardMessage.value = `已刷新统计周期 ${period.value} 的导出包状态`
}

async function verifyCurrent() {
  if (!packageNo.value) return
  try {
    const res = await request(`${ENDPOINT}/export-packages/${encodeURIComponent(packageNo.value)}/verify`, {
      method: 'POST', body: JSON.stringify({ values: {} }),
    })
    const data = await res.json()
    if (!data.ok) throw new Error(data.message || '提交核查失败')
    boardMessage.value = `${data.entry.核查编号}：${data.entry.结论}，待审 ${data.entry.待审清单?.length || 0} 条`
    await loadMonitorRecords()
  } catch (error) {
    boardMessage.value = error instanceof Error ? error.message : '提交核查失败'
  }
}

async function runAnalysis() {
  loading.value = true
  try { const res = await request(`${ENDPOINT}/run`, { method: 'POST', body: JSON.stringify({ values: params.value }) }); const data = await res.json(); results.value = data.items || []; chartData.value = (data.items || []).map((r: any) => ({ label: Object.values(r)[0] as string, value: Object.values(r).filter(v => typeof v === 'number')[0] as number || 0 })).slice(0, 6) } catch(e) { results.value = [] }
  loading.value = false
}
onMounted(async () => {
  await fetchSummary()
  try {
    await loadPeriods()
    await loadPeriod()
  } catch (error) {
    boardMessage.value = error instanceof Error ? error.message : '统计周期加载失败'
  }
})
</script>
<style scoped>
.inline-field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.inline-field select { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.period-board { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 14px; margin: 12px 0; }
.period-meta { display: flex; flex-wrap: wrap; gap: 14px; font-size: 12px; color: var(--muted); }
.board-message { font-size: 13px; color: var(--brand); }
.deliverable-grid { display: grid; grid-template-columns: 1fr 1.4fr; gap: 14px; }
.history-block { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 14px; margin: 12px 0; }
</style>
