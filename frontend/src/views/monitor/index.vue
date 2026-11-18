<template>
  <section class="page" data-module="monitor">
    <header class="page-head">
      <div>
        <h2>采集监控</h2>
        <p class="page-desc">接力采集报告的完整导出包，按报告周期重算线上指标，差异进入待审清单，并把核查结果持久化成可供采集报告回查的业务记录。</p>
      </div>
      <div class="page-actions">
        <label class="inline-field">
          <span>统计周期</span>
          <select v-model="period" data-testid="monitor-period" @change="loadMonitor">
            <option v-for="item in periods" :key="item.统计周期" :value="item.统计周期">
              {{ item.统计周期 }}（{{ item.周期区间 }}）
            </option>
          </select>
        </label>
        <button class="btn primary" data-testid="sync-package" type="button" :disabled="syncing" @click="syncPackage()">
          {{ syncing ? '核对中…' : '同步重算导出包' }}
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
        <span style="font-size:12px;color:var(--muted)">待审条数</span>
        <strong style="font-size:22px;display:block" data-testid="pending-count">{{ pendingTotal }}</strong>
        <span style="font-size:12px;color:var(--muted)">最近核查 {{ latestCheck?.结论 || '未核查' }}</span>
      </article>
    </div>

    <section class="period-board" data-testid="monitor-board">
      <div class="period-meta">
        <span>统计周期：{{ period }}</span>
        <span>线上版本：V{{ onlineVersion }}</span>
        <span>监控已持久化导出包：{{ scopePackages.length }} 个</span>
        <span>来源书签：{{ latestCheck?.来源书签 || '—' }}</span>
      </div>
      <p v-if="monitorMessage" class="board-message" data-testid="monitor-message">{{ monitorMessage }}</p>

      <h3>导出包接力情况</h3>
      <table class="data-table" data-testid="monitor-package-table">
        <thead>
          <tr>
            <th>包编号</th><th>统计周期</th><th>批次版本</th><th>冻结版本</th><th>线上版本</th>
            <th>导出边界</th><th>可核对</th><th>待合并</th><th>监控已持久化</th><th>最近结论</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="pkg in scopePackages" :key="pkg.包编号" :data-testid="`monitor-package-${pkg.包编号}`">
            <td>{{ pkg.包编号 }}</td>
            <td>{{ pkg.统计周期 }}</td>
            <td>{{ pkg.批次版本 }}</td>
            <td>{{ pkg.冻结版本 }}</td>
            <td>{{ pkg.线上版本 }}</td>
            <td>{{ pkg.导出边界 }}</td>
            <td>{{ pkg.可核对 ? '可核对' : '待核对' }}</td>
            <td>{{ pkg.待合并条数 }}</td>
            <td>{{ pkg.监控已持久化 ? `已持久化（${pkg.核查次数} 次）` : '未持久化' }}</td>
            <td :class="pkg.最近核查结论 === '一致' ? 'trend-up' : 'error-text'">{{ pkg.最近核查结论 }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="selectPackage(pkg)">查看核查</button>
              <button class="link" type="button" @click="syncPackage(pkg.包编号)">重算核对</button>
            </td>
          </tr>
          <tr v-if="!scopePackages.length">
            <td colspan="11" class="empty-state">该周期还没有导出包，可点“同步重算导出包”由监控侧冻结一份再核对</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-if="selectedPackage" class="period-board" data-testid="monitor-package-detail">
      <div class="period-meta">
        <span>包编号：{{ selectedPackage.包编号 }}</span>
        <span>批次版本：V{{ selectedPackage.批次版本 }}</span>
        <span>冻结版本：V{{ selectedPackage.冻结版本 }}</span>
        <span>线上版本：V{{ selectedPackage.线上版本 }}</span>
        <span>内容指纹：{{ selectedPackage.内容指纹 }}</span>
        <span>可核对：{{ selectedPackage.可核对 ? '是' : '否' }}</span>
      </div>
      <div class="deliverable-grid">
        <article>
          <h3>包内报告摘要</h3>
          <table class="data-table" data-testid="monitor-report-summary">
            <tbody>
              <tr v-for="field in reportFields" :key="field"><th>{{ field }}</th><td>{{ selectedPackage.报告?.[field] ?? '—' }}</td></tr>
            </tbody>
          </table>
        </article>
        <article>
          <h3>指标依据（{{ selectedPackage.指标依据?.length || 0 }} 项）</h3>
          <table class="data-table" data-testid="monitor-metric-table">
            <thead><tr><th>指标名称</th><th>指标值</th><th>依据来源</th><th>批次版本</th></tr></thead>
            <tbody>
              <tr v-for="row in selectedPackage.指标依据 || []" :key="row.指标名称">
                <td>{{ row.指标名称 }}</td><td>{{ row.指标值 }}</td><td>{{ row.依据来源 }}</td><td>{{ row.批次版本 }}</td>
              </tr>
            </tbody>
          </table>
        </article>
      </div>
      <h3>待合并更正（{{ selectedPackage.待合并更正?.length || 0 }} 条）</h3>
      <table class="data-table" data-testid="monitor-pending-events">
        <thead><tr><th>事件编号</th><th>批次版本</th><th>事件类型</th><th>说明</th><th>记录时间</th></tr></thead>
        <tbody>
          <tr v-for="event in selectedPackage.待合并更正 || []" :key="event.事件编号">
            <td>{{ event.事件编号 }}</td><td>{{ event.批次版本 }}</td><td>{{ event.事件类型 }}</td>
            <td>{{ event.说明 }}</td><td>{{ event.记录时间 }}</td>
          </tr>
          <tr v-if="!selectedPackage.待合并更正?.length">
            <td colspan="5" class="empty-state">导出包已是周期最新版本，没有待合并更正</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="period-board">
      <h3>待审清单</h3>
      <table class="data-table" data-testid="review-table">
        <thead><tr><th>核查编号</th><th>包编号</th><th>核对项</th><th>包内值</th><th>线上值</th><th>差异</th><th>处置建议</th></tr></thead>
        <tbody>
          <tr v-for="item in reviewRows" :key="`${item.核查编号}-${item.序号}`">
            <td>{{ item.核查编号 }}</td>
            <td>{{ item.包编号 }}</td>
            <td>{{ item.核对项 }}</td>
            <td>{{ item.包内值 }}</td>
            <td>{{ item.线上值 }}</td>
            <td>{{ item.差异 }}</td>
            <td>{{ item.处置建议 }}</td>
          </tr>
          <tr v-if="!reviewRows.length">
            <td colspan="7" class="empty-state">当前周期没有待审项</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="period-board">
      <h3>监控核查记录（采集报告可按批次回查）</h3>
      <table class="data-table" data-testid="monitor-check-table">
        <thead>
          <tr><th>核查编号</th><th>包编号</th><th>批次版本</th><th>线上版本</th><th>结论</th><th>待审条数</th><th>核对时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="record in checks" :key="record.核查编号">
            <td>{{ record.核查编号 }}</td><td>{{ record.包编号 }}</td><td>{{ record.批次版本 }}</td>
            <td>{{ record.线上版本 }}</td>
            <td :class="record.结论 === '一致' ? 'trend-up' : 'error-text'">{{ record.结论 }}</td>
            <td>{{ record.待审清单?.length || 0 }}</td><td>{{ record.核对时间 }}</td>
          </tr>
          <tr v-if="!checks.length">
            <td colspan="7" class="empty-state">尚未提交核对</td>
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
const ENDPOINT = '/api/monitor'
const summaryMetrics = ref<{label:string;value:number;trend:number}[]>([])
const results = ref<any[]>([]); const chartData = ref<{label:string;value:number}[]>([])
const loading = ref(false)
const params = ref<Record<string,string>>({})
const paramFields = ["面板编号", "采集源总数", "今日采集量", "成功率", "平均延迟", "失败重试", "异常告警", "更新时间"].filter((f: string) => !['编号','名称','版本'].includes(f)).slice(0, 4)
const options: Record<string,string[]> = { '统计粒度': ['按日','按周','按月'], '分组字段': ['全量','按类型','按区域'], '比较方法': ['环比','同比','绝对值'], '时间范围': ['最近7天','最近30天','最近90天'] }
const resultColumns = computed(() => ["面板编号", "采集源总数", "今日采集量", "成功率", "平均延迟", "失败重试", "异常告警", "更新时间"].slice(0, 5))
function barHeight(v: number) { const max = Math.max(...chartData.value.map(d => d.value), 1); return Math.max(10, (v / max) * 140) }
async function fetchSummary() { try { const res = await request(`${ENDPOINT}/summary`); summaryMetrics.value = (await res.json()).metrics || [] } catch(e) {} }

const reportFields = ["报告编号", "采集源", "统计周期", "总采集量", "成功量", "失败量", "平均耗秒", "批次版本", "冻结版本", "来源书签"]
const periods = ref<{统计周期:string;周期区间:string;最新版本:number}[]>([])
const packages = ref<any[]>([])
const checks = ref<any[]>([])
const period = ref('')
const selectedPackage = ref<any>(null)
const monitorMessage = ref('')
const syncing = ref(false)

const scopePackages = computed(() => packages.value.filter(p => p.统计周期 === period.value))
const scopeChecks = computed(() => checks.value.filter(r => r.统计周期 === period.value))
const latestCheck = computed(() => scopeChecks.value[0] || null)
const pendingTotal = computed(() => scopeChecks.value.reduce((sum, r) => sum + (r.待审清单?.length || 0), 0))
const onlineVersion = computed(() => {
  const list = scopePackages.value.map(p => p.线上版本)
  return list.length ? Math.max(...list) : periods.value.find(p => p.统计周期 === period.value)?.最新版本 || 0
})
const reviewRows = computed(() => scopeChecks.value.flatMap(record =>
  (record.待审清单 || []).map((item: any) => ({ ...item, 核查编号: record.核查编号, 包编号: record.包编号 }))))

async function loadMonitor() {
  // 注意：这里不清空 monitorMessage，避免刷新与同步操作并发时把刚生成的结果冲掉。
  try {
    const res = await request(`${ENDPOINT}/report-packages${period.value ? `?period=${encodeURIComponent(period.value)}` : ''}`)
    if (!res.ok) throw new Error('导出包清单读取失败')
    const data = await res.json()
    packages.value = data.items || []
    if (data.periods?.length) periods.value = data.periods
    if (!period.value) period.value = periods.value[periods.value.length - 1]?.统计周期 || ''
    const checkRes = await request(`${ENDPOINT}/report-checks?period=${encodeURIComponent(period.value)}`)
    if (checkRes.ok) {
      const checkData = await checkRes.json()
      checks.value = checkData.items || []
    }
    const current = scopePackages.value.find(p => p.包编号 === selectedPackage.value?.包编号)
    const target = current || scopePackages.value[0] || null
    if (target) {
      // 列表接口是摘要，选中包需要再拉一次完整内容（报告/明细/指标依据）。
      const detailRes = await request(`${ENDPOINT}/report-packages/${encodeURIComponent(target.包编号)}`)
      selectedPackage.value = detailRes.ok ? await detailRes.json() : target
    } else {
      selectedPackage.value = null
    }
  } catch (error) {
    monitorMessage.value = error instanceof Error ? error.message : '导出包清单读取失败'
  }
}

function selectPackage(pkg: any) {
  const row = scopePackages.value.find(item => item.包编号 === pkg.包编号) || pkg
  selectedPackage.value = row
  void loadPackageDetail(row.包编号)
}

async function loadPackageDetail(packageNo: string) {
  try {
    const res = await request(`${ENDPOINT}/report-packages/${encodeURIComponent(packageNo)}`)
    if (!res.ok) return
    const detail = await res.json()
    if (selectedPackage.value?.包编号 === packageNo) selectedPackage.value = detail
  } catch {
    // 详情拉取失败时保留列表摘要，不阻塞页面。
  }
}

async function syncPackage(packageNo?: string) {
  syncing.value = true
  monitorMessage.value = ''
  try {
    const values: Record<string, string> = { 统计周期: period.value }
    if (packageNo) values.包编号 = packageNo
    const res = await request(`${ENDPOINT}/report-packages/sync`, {
      method: 'POST', body: JSON.stringify({ values }),
    })
    const data = await res.json()
    if (!data.ok) throw new Error(data.message || '同步重算失败')
    monitorMessage.value = `${data.entry.核查编号}：${data.entry.结论}，待审 ${data.entry.待审清单?.length || 0} 条`
    if (data.entry['新建导出包']) monitorMessage.value += '（本次由采集监控冻结了新导出包）'
    await loadMonitor()
    if (data.entry.包编号) {
      const res2 = await request(`${ENDPOINT}/report-packages/${encodeURIComponent(data.entry.包编号)}`)
      if (res2.ok) selectedPackage.value = await res2.json()
    }
  } catch (error) {
    monitorMessage.value = error instanceof Error ? error.message : '同步重算失败'
  } finally {
    syncing.value = false
  }
}

async function runAnalysis() {
  loading.value = true
  try { const res = await request(`${ENDPOINT}/run`, { method: 'POST', body: JSON.stringify({ values: params.value }) }); const data = await res.json(); results.value = data.items || []; chartData.value = (data.items || []).map((r: any) => ({ label: Object.values(r)[0] as string, value: Object.values(r).filter(v => typeof v === 'number')[0] as number || 0 })).slice(0, 6) } catch(e) { results.value = [] }
  loading.value = false
}
onMounted(async () => {
  await fetchSummary()
  await loadMonitor()
})
</script>
<style scoped>
.inline-field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.inline-field select { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.period-board { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 14px; margin: 12px 0; }
.period-meta { display: flex; flex-wrap: wrap; gap: 14px; font-size: 12px; color: var(--muted); }
.board-message { font-size: 13px; color: var(--brand); }
.deliverable-grid { display: grid; grid-template-columns: 1fr 1.2fr; gap: 14px; }
</style>
