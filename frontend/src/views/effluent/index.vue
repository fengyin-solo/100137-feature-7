<template>
  <section class="page" data-module="effluent">
    <header class="page-head">
      <div>
        <h2>出水监测管理</h2>
        <p class="page-desc">
          按厂站适用排放标准自动比对 COD、氨氮、总磷限值并给出达标/超标结论；
          超标标注倍数区间，排放流量异常单独标记。判定规则版本 v{{ ruleVersion }}。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记出水记录</button>
        <button class="btn" type="button" @click="openRules">判定规则</button>
        <button class="btn" type="button" @click="rejudgeAll">按当前规则重新判定全部</button>
        <button class="btn" type="button" @click="exportRows">导出清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>记录编号</span>
        <input v-model="filters.keyword" placeholder="按记录编号检索" />
      </label>
      <label class="filter-item">
        <span>判定结论</span>
        <select v-model="filters.status">
          <option value="">全部</option>
          <option v-for="s in ['达标', '超标', '待判定']" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>排放流量</span>
        <select v-model="filters.flow_abnormal">
          <option value="">全部</option>
          <option value="true">仅看流量异常</option>
          <option value="false">仅看流量正常</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>判定依据/操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td>{{ row['记录编号'] ?? '—' }}</td>
          <td>{{ row['所属厂站'] ?? '—' }}</td>
          <td>{{ row['监测时间'] ?? '—' }}</td>
          <td>{{ formatValue(row['COD出水值']) }}</td>
          <td>{{ formatValue(row['氨氮出水值']) }}</td>
          <td>{{ formatValue(row['总磷出水值']) }}</td>
          <td>
            {{ formatValue(row['排放流量']) }}
            <span v-if="judgment(row).flow_abnormal" class="tag tag-warn" :title="judgment(row).flow_message">
              流量异常
            </span>
          </td>
          <td>
            <span :class="conclusionClass(judgment(row).conclusion)">
              {{ judgment(row).conclusion ?? '待判定' }}
            </span>
            <div v-if="judgment(row).conclusion === '超标'" class="cell-sub">
              最大 {{ worstRatio(row) }} 倍（{{ worstBucket(row) }}）
            </div>
            <div v-if="!judgment(row).conclusion" class="cell-sub error-text">
              {{ shortReasons(row) }}
            </div>
          </td>
          <td>{{ judgment(row).standard ?? '—' }}<div class="cell-sub">v{{ judgment(row).rule_version ?? '—' }}</div></td>
          <td>{{ row['处置状态'] ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openBasis(row)">判定依据</button>
            <button class="link" type="button" @click="review(row)">复核</button>
            <button class="link" type="button" @click="rejudge(row)">重新判定</button>
            <button class="link" type="button" @click="runAction('预警通知', row)">预警通知</button>
            <button class="link" type="button" @click="runAction('关阀截流', row)">关阀截流</button>
            <button class="link" type="button" @click="runAction('恢复排放', row)">恢复排放</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无出水监测数据，可先登记出水记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条出水监测记录 · 判定规则 v{{ ruleVersion }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="successMessage" class="success-text">{{ successMessage }}</span>
    </footer>

    <!-- 登记出水记录 -->
    <div v-if="showCreate" class="modal-mask" @click.self="showCreate = false">
      <div class="modal">
        <h3>登记出水记录</h3>
        <p class="page-desc">监测值支持数字或「32 mg/L」写法；指标缺失/格式不对会保留记录但不生成达标结论。</p>
        <div class="form-grid">
          <label v-for="field in formFields" :key="field" class="form-item">
            <span>{{ field }}</span>
            <input v-model="createForm[field]" :placeholder="`请输入${field}`" />
          </label>
        </div>
        <div class="modal-foot">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" type="button" @click="submitCreate">登记并自动判定</button>
        </div>
      </div>
    </div>

    <!-- 判定规则 -->
    <div v-if="showRules" class="modal-mask wide" @click.self="showRules = false">
      <div class="modal">
        <h3>判定规则 <span class="page-desc">当前版本 v{{ rules.version }} · 更新于 {{ rules.updated_at }}</span></h3>
        <h4>排放标准限值（mg/L）</h4>
        <table class="data-table compact">
          <thead>
            <tr><th>标准</th><th>COD</th><th>氨氮</th><th>总磷</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="(limits, name) in rules.standards" :key="name">
              <td>{{ name }}</td>
              <td v-for="key in ['cod', 'nh3n', 'tp']" :key="key">
                <input class="num-input" v-model="limitDraft[name][key]" />
              </td>
              <td>
                <button class="link" type="button" @click="saveStandard(String(name))">保存限值</button>
              </td>
            </tr>
          </tbody>
        </table>
        <p class="page-desc">限值保存后规则版本递增；既有记录需点「按当前规则重新判定全部」后才套用新口径。</p>

        <h4>厂站判定口径</h4>
        <table class="data-table compact">
          <thead>
            <tr><th>厂站</th><th>适用标准</th><th>流量下限</th><th>流量上限</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="(rule, name) in stationDraft" :key="name">
              <td>{{ name }}</td>
              <td>
                <select v-model="rule.standard">
                  <option v-for="s in standardNames" :key="s" :value="s">{{ s }}</option>
                </select>
              </td>
              <td><input class="num-input" v-model="rule.flow_min" /></td>
              <td><input class="num-input" v-model="rule.flow_max" /></td>
              <td><button class="link" type="button" @click="saveStation(String(name))">保存口径</button></td>
            </tr>
            <tr>
              <td><input v-model="newStation.name" placeholder="新增厂站名称" /></td>
              <td>
                <select v-model="newStation.standard">
                  <option v-for="s in standardNames" :key="s" :value="s">{{ s }}</option>
                </select>
              </td>
              <td><input class="num-input" v-model="newStation.flow_min" placeholder="m³/h" /></td>
              <td><input class="num-input" v-model="newStation.flow_max" placeholder="m³/h" /></td>
              <td><button class="link" type="button" @click="saveStation(newStation.name)">新增口径</button></td>
            </tr>
          </tbody>
        </table>
        <div class="modal-foot">
          <button class="btn primary" type="button" @click="showRules = false">完成</button>
        </div>
      </div>
    </div>

    <!-- 判定依据 -->
    <div v-if="basisRow" class="modal-mask" @click.self="basisRow = null">
      <div class="modal">
        <h3>判定依据 · {{ basisRow['记录编号'] }}</h3>
        <div class="basis-head">
          <span :class="conclusionClass(basisJudgment.conclusion)">
            自动结论：{{ basisJudgment.conclusion ?? '待判定' }}
          </span>
          <span class="page-desc">{{ basisJudgment.conclusion_text }}</span>
        </div>
        <table v-if="basisJudgment.items?.length" class="data-table compact">
          <thead>
            <tr><th>指标</th><th>实测值</th><th>排放限值(mg/L)</th><th>倍数</th><th>倍数区间</th></tr>
          </thead>
          <tbody>
            <tr v-for="it in basisJudgment.items" :key="it.field">
              <td>{{ it.indicator }}</td>
              <td>{{ it.value }}</td>
              <td>{{ it.limit }}</td>
              <td :class="{ 'error-text': it.exceeded }">{{ it.ratio }}</td>
              <td>{{ it.ratio_bucket ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
        <ul class="reason-list">
          <li v-for="(r, i) in basisJudgment.reasons" :key="i">{{ r }}</li>
        </ul>
        <dl class="basis-meta">
          <div><dt>厂站标准</dt><dd>{{ basisJudgment.standard ?? '—' }}</dd></div>
          <div><dt>规则版本</dt><dd>v{{ basisJudgment.rule_version ?? '—' }}</dd></div>
          <div><dt>流量正常区间</dt><dd>{{ flowRangeText(basisJudgment) }}</dd></div>
          <div><dt>判定时间</dt><dd>{{ basisJudgment.evaluated_at }}</dd></div>
          <div class="full"><dt>依据快照哈希</dt><dd>{{ basisJudgment.basis_hash ?? '—' }}</dd></div>
        </dl>
        <div class="modal-foot">
          <button class="btn" type="button" @click="basisRow = null">关闭</button>
          <button class="btn primary" type="button" @click="reviewFromBasis">用这份依据复核</button>
        </div>
      </div>
    </div>

    <!-- 复核结果 -->
    <div v-if="reviewData" class="modal-mask" @click.self="reviewData = null">
      <div class="modal">
        <h3>复核结果 · {{ reviewData.entry_id }}</h3>
        <p :class="reviewData.review.consistent ? 'success-text' : 'error-text'" class="review-verdict">
          {{ reviewData.review.consistent ? '✓ 判定依据与录入时一致' : '✗ 判定依据与录入时不一致' }}
        </p>
        <p>{{ reviewData.review.reason }}</p>
        <dl class="basis-meta">
          <div><dt>规则版本</dt><dd>v{{ reviewData.review.rule_version ?? '—' }}</dd></div>
          <div><dt>标准</dt><dd>{{ reviewData.review.standard ?? '—' }}</dd></div>
          <div><dt>快照哈希</dt><dd>{{ reviewData.review.saved_hash ?? '—' }}</dd></div>
          <div v-if="reviewData.review.actual_hash"><dt>实算哈希</dt><dd>{{ reviewData.review.actual_hash }}</dd></div>
        </dl>
        <div class="modal-foot">
          <button class="btn primary" type="button" @click="reviewData = null">知道了</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type IndicatorItem = {
  indicator: string
  field: string
  value: number
  limit: number
  ratio: number
  exceeded: boolean
  ratio_bucket: string | null
}
type Judgment = {
  conclusion: '达标' | '超标' | null
  status: string
  conclusion_text: string
  reasons: string[]
  flow_abnormal: boolean
  flow_message: string
  flow_range: { min: number | null; max: number | null }
  items: IndicatorItem[]
  basis: Record<string, unknown> | null
  inputs: Record<string, unknown>
  basis_hash: string | null
  rule_version: number | null
  standard: string | null
  evaluated_at: string
}
type Row = Record<string, string | number | boolean | null> & { id: number; 判定?: Judgment; 处置状态?: string | null }
type RulesPayload = {
  version: number
  updated_at: string
  standards: Record<string, Record<string, number>>
  stations: Record<string, { standard: string; flow_min: number | null; flow_max: number | null }>
}

const ENDPOINT = '/api/effluent'
const columns = ['记录编号', '所属厂站', '监测时间', 'COD出水值(mg/L)', '氨氮出水值(mg/L)', '总磷出水值(mg/L)', '排放流量(m³/h)', '自动判定结论', '适用标准/版本', '处置状态']
const formFields = ['记录编号', '所属厂站', '监测时间', 'COD出水值', '氨氮出水值', '总磷出水值', '排放流量']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const successMessage = ref('')
const filters = reactive<{ keyword: string; status: string; flow_abnormal: string }>({
  keyword: '',
  status: '',
  flow_abnormal: '',
})

const showCreate = ref(false)
const createForm = ref<Record<string, string>>({})
const showRules = ref(false)
const rules = ref<RulesPayload>({ version: 0, updated_at: '', standards: {}, stations: {} })
const limitDraft = ref<Record<string, Record<string, number>>>({})
const stationDraft = ref<Record<string, { standard: string; flow_min: number | null; flow_max: number | null }>>({})
const newStation = reactive({ name: '', standard: '一级A', flow_min: '', flow_max: '' })
const basisRow = ref<Row | null>(null)
const reviewData = ref<{ entry_id: number; review: Record<string, any> } | null>(null)

const ruleVersion = computed(() => rules.value.version)
const standardNames = computed(() => Object.keys(rules.value.standards))
const basisJudgment = computed<Judgment>(() => (basisRow.value?.判定 ?? {}) as Judgment)

const stats = computed(() => {
  const list = rows.value
  return [
    { label: '判定达标', value: list.filter((r) => r.判定?.conclusion === '达标').length },
    { label: '判定超标', value: list.filter((r) => r.判定?.conclusion === '超标').length },
    { label: '待判定（缺指标/口径）', value: list.filter((r) => !r.判定?.conclusion).length },
    { label: '排放流量异常', value: list.filter((r) => r.判定?.flow_abnormal).length },
  ]
})

function judgment(row: Row): Judgment {
  return (row.判定 ?? {}) as Judgment
}

function formatValue(raw: unknown): string {
  if (raw === null || raw === undefined || raw === '') return '—'
  return String(raw)
}

function conclusionClass(conclusion: string | null): string {
  if (conclusion === '达标') return 'tag tag-ok'
  if (conclusion === '超标') return 'tag tag-bad'
  return 'tag tag-pending'
}

function worstRatio(row: Row): string {
  const items = judgment(row).items ?? []
  const worst = items.filter((i) => i.exceeded).sort((a, b) => b.ratio - a.ratio)[0]
  return worst ? String(worst.ratio) : '—'
}

function worstBucket(row: Row): string {
  const items = judgment(row).items ?? []
  const worst = items.filter((i) => i.exceeded).sort((a, b) => b.ratio - a.ratio)[0]
  return worst?.ratio_bucket ?? '—'
}

function shortReasons(row: Row): string {
  return (judgment(row).reasons ?? [])[0] ?? '未生成结论'
}

function flowRangeText(j: Judgment): string {
  const range = j.flow_range ?? {}
  if (range.min === null && range.max === null) return '未配置'
  return `${range.min ?? '−∞'} ~ ${range.max ?? '+∞'} m³/h`
}

function flash(message: string, ok = true) {
  if (ok) {
    successMessage.value = message
    errorMessage.value = ''
  } else {
    errorMessage.value = message
    successMessage.value = ''
  }
}

function resetFilters() {
  filters.keyword = ''
  filters.status = ''
  filters.flow_abnormal = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  createForm.value = {}
  showCreate.value = true
}

async function submitCreate() {
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: createForm.value }),
    })
    const payload = await response.json()
    showCreate.value = false
    flash(payload.message || '出水记录已登记', true)
    await reload()
  } catch (error) {
    flash(error instanceof Error ? error.message : '登记失败', false)
  }
}

async function runAction(action: string, row: Row) {
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    flash(payload.message)
    await reload()
  } catch (error) {
    flash(error instanceof Error ? error.message : '操作失败', false)
  }
}

async function rejudge(row: Row) {
  try {
    const response = await request(`${ENDPOINT}/${row.id}/rejudge`, { method: 'POST' })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    flash(payload.message)
    await reload()
  } catch (error) {
    flash(error instanceof Error ? error.message : '重新判定失败', false)
  }
}

async function rejudgeAll() {
  try {
    const response = await request(`${ENDPOINT}/rejudge-all`, { method: 'POST' })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    flash(payload.message)
    await Promise.all([reload(), loadRules()])
  } catch (error) {
    flash(error instanceof Error ? error.message : '批量重新判定失败', false)
  }
}

function openBasis(row: Row) {
  basisRow.value = row
}

async function review(row: Row) {
  basisRow.value = null
  try {
    const response = await request(`${ENDPOINT}/${row.id}/review`)
    if (!response.ok) throw new Error('复核请求未生效')
    reviewData.value = await response.json()
  } catch (error) {
    flash(error instanceof Error ? error.message : '复核失败', false)
  }
}

async function reviewFromBasis() {
  if (!basisRow.value) return
  const row = basisRow.value
  basisRow.value = null
  await review(row)
}

async function openRules() {
  showRules.value = true
  await loadRules()
}

async function loadRules() {
  try {
    const response = await request(`${ENDPOINT}/rules`)
    if (!response.ok) throw new Error('规则读取失败')
    rules.value = await response.json()
    limitDraft.value = Object.fromEntries(
      Object.entries(rules.value.standards).map(([name, limits]) => [name, { ...limits }]),
    )
    stationDraft.value = Object.fromEntries(
      Object.entries(rules.value.stations).map(([name, rule]) => [
        name,
        { standard: rule.standard, flow_min: rule.flow_min, flow_max: rule.flow_max },
      ]),
    )
  } catch (error) {
    flash(error instanceof Error ? error.message : '规则读取失败', false)
  }
}

async function saveStandard(name: string) {
  try {
    const response = await request(`${ENDPOINT}/rules/standards/${encodeURIComponent(name)}`, {
      method: 'PUT',
      body: JSON.stringify({ values: limitDraft.value[name] }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    flash(payload.message)
    await loadRules()
  } catch (error) {
    flash(error instanceof Error ? error.message : '限值保存失败', false)
  }
}

async function saveStation(name: string) {
  const trimmed = name.trim()
  if (!trimmed) {
    flash('厂站名称不能为空', false)
    return
  }
  const existing = stationDraft.value[trimmed]
  const draft = existing ?? {
    standard: newStation.standard,
    flow_min: newStation.flow_min === '' ? null : Number(newStation.flow_min),
    flow_max: newStation.flow_max === '' ? null : Number(newStation.flow_max),
  }
  try {
    const response = await request(`${ENDPOINT}/rules/stations/${encodeURIComponent(trimmed)}`, {
      method: 'PUT',
      body: JSON.stringify({ values: draft }),
    })
    const payload = await response.json()
    if (!payload.ok) throw new Error(payload.message)
    newStation.name = ''
    flash(payload.message)
    await loadRules()
  } catch (error) {
    flash(error instanceof Error ? error.message : '厂站口径保存失败', false)
  }
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  if (filters.keyword) params.set('keyword', filters.keyword)
  if (filters.status) params.set('status', filters.status)
  if (filters.flow_abnormal) params.set('flow_abnormal', filters.flow_abnormal)
  params.set('size', '200')
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) throw new Error('出水记录列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    flash(error instanceof Error ? error.message : '出水监测列表读取失败', false)
  }
}

onMounted(() => {
  void reload()
  void loadRules()
})
</script>

<style scoped>
.page-actions { display: flex; gap: 8px; }
.tag { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 12px; white-space: nowrap; }
.tag-ok { background: #e7f6ec; color: #1a7f37; }
.tag-bad { background: #fde8e7; color: #b42318; }
.tag-pending { background: #f1f5f9; color: #475569; }
.tag-warn { background: #fef3d7; color: #9a6700; margin-left: 4px; }
.cell-sub { font-size: 11px; color: var(--muted); margin-top: 2px; max-width: 220px; }
.success-text { color: #1a7f37; }
.modal-mask {
  position: fixed; inset: 0; background: rgba(15, 23, 42, 0.45);
  display: flex; align-items: center; justify-content: center; z-index: 20;
}
.modal {
  background: #fff; border-radius: 10px; padding: 20px 24px;
  width: 560px; max-height: 86vh; overflow: auto;
}
.modal-mask.wide .modal { width: 820px; }
.modal h3 { margin: 0 0 8px; }
.modal h4 { margin: 16px 0 8px; font-size: 14px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 12px 0; }
.form-item span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 2px; }
.form-item input, .num-input {
  width: 100%; border: 1px solid var(--border); border-radius: 6px; padding: 5px 8px; font-size: 13px;
}
.num-input { width: 84px; }
.modal-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
.compact th, .compact td { padding: 5px 8px; font-size: 12px; }
.basis-head { display: flex; flex-direction: column; gap: 4px; margin: 10px 0; }
.reason-list { margin: 8px 0; padding-left: 18px; font-size: 13px; color: #334155; }
.basis-meta { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 16px; margin: 12px 0 0; font-size: 12px; }
.basis-meta .full { grid-column: 1 / -1; }
.basis-meta dt { color: var(--muted); display: inline; margin-right: 6px; }
.basis-meta dd { display: inline; margin: 0; }
.review-verdict { font-size: 16px; font-weight: 600; }
</style>
