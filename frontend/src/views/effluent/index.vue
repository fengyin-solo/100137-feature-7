<template>
  <section class="page" data-module="effluent">
    <header class="page-head">
      <div>
        <h2>出水监测管理</h2>
        <p class="page-desc">
          COD、氨氮、总磷出水值按厂站排放标准自动判定达标/超标与超标倍数区间；排放流量异常单独标记；
          规则改版后可重判既有记录，录入时的判定依据永久留存。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记出水记录</button>
        <button class="btn" type="button" @click="openRules">判定口径管理</button>
        <button class="btn" type="button" @click="rejudgeAll">按当前口径全部重判</button>
        <button class="btn" type="button" @click="exportRows">导出清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value" :class="item.cls">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>记录编号</span>
        <input v-model="filters.keyword" placeholder="按记录编号检索" />
      </label>
      <label class="filter-item">
        <span>所属厂站</span>
        <input v-model="filters.station" placeholder="按厂站名称检索" />
      </label>
      <label class="filter-item">
        <span>判定结论</span>
        <select v-model="filters.status">
          <option value="">全部</option>
          <option v-for="s in conclusions" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>排放流量</span>
        <select v-model="filters.flow_abnormal">
          <option value="">全部</option>
          <option value="true">仅看流量异常</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>判定依据</th>
          <th>处置 / 重判</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td>{{ row['记录编号'] ?? '—' }}</td>
          <td>{{ row['所属厂站'] ?? '—' }}</td>
          <td>{{ row['监测时间'] ?? '—' }}</td>
          <td>{{ showVal(row, 'COD出水值') }}</td>
          <td>{{ showVal(row, '氨氮出水值') }}</td>
          <td>{{ showVal(row, '总磷出水值') }}</td>
          <td>
            {{ showVal(row, '排放流量') }}
            <span v-if="row['流量异常']" class="tag tag-warn" :title="row['流量异常说明']">流量异常</span>
          </td>
          <td>
            <span class="tag" :class="conclusionClass(row['判定结论'])">{{ row['判定结论'] ?? '—' }}</span>
            <div v-if="row['判定问题']" class="cell-note">{{ row['判定问题'] }}</div>
          </td>
          <td>{{ row['超标倍数'] || '—' }}</td>
          <td>{{ row['判定规则版本'] ?? '—' }}</td>
          <td>{{ row['处置状态'] ?? '—' }}</td>
          <td><button class="link" type="button" @click="openDetail(row)">查看依据</button></td>
          <td class="row-actions">
            <button class="link" type="button" @click="rejudgeRow(row)">重判</button>
            <button v-for="action in actions" :key="action" class="link" type="button" @click="runAction(action, row)">
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无出水监测数据，可先登记出水记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条出水监测记录</span>
      <span v-if="currentVersion" class="foot-rule">
        当前判定口径 {{ currentVersion.version }}（{{ currentVersion.fingerprint }}）｜默认标准：{{ currentVersion.default_standard }}
      </span>
      <span v-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 判定依据明细 -->
    <div v-if="detailRow" class="modal-mask" @click.self="detailRow = null">
      <div class="modal modal-wide">
        <div class="modal-head">
          <h3>判定依据明细 · {{ detailRow['记录编号'] }}</h3>
          <button class="link" type="button" @click="detailRow = null">关闭</button>
        </div>
        <div class="modal-body">
          <section class="basis-section">
            <h4>基本信息</h4>
            <p>所属厂站：{{ detailRow['所属厂站'] }} ｜ 监测时间：{{ detailRow['监测时间'] }}</p>
            <p>当前判定结论：
              <span class="tag" :class="conclusionClass(detailRow['判定结论'])">{{ detailRow['判定结论'] }}</span>
              （口径 {{ detailRow['判定规则版本'] }}，{{ detailRow['判定时间'] }}）
              <span v-if="detailRow['流量异常']" class="tag tag-warn">排放流量异常</span>
            </p>
            <p v-if="detailRow['超标倍数']" class="basis-over">超标倍数：{{ detailRow['超标倍数'] }}</p>
            <p v-if="detailRow['判定问题']" class="basis-problem">未生成结论原因：{{ detailRow['判定问题'] }}</p>
          </section>

          <section class="basis-section">
            <h4>逐项比对</h4>
            <table class="data-table">
              <thead><tr><th>指标</th><th>实测值 (mg/L)</th><th>限值 (mg/L)</th><th>结果</th><th>超标倍数</th></tr></thead>
              <tbody>
                <tr v-for="item in (detailRow['判定快照']?.['指标判定'] ?? [])" :key="item['指标']">
                  <td>{{ item['指标'] }}</td>
                  <td>{{ item['实测值'] === null ? '—' : item['实测值'] }}{{ item['问题'] ? `（${item['问题']}）` : '' }}</td>
                  <td>{{ item['限值'] }}</td>
                  <td>{{ item['达标'] === null ? '不参与结论' : item['达标'] ? '达标' : '超标' }}</td>
                  <td>{{ item['达标'] === false ? `${item['倍数']}（${item['倍数区间']}）` : '—' }}</td>
                </tr>
              </tbody>
            </table>
          </section>

          <section class="basis-section">
            <h4>排放流量核查</h4>
            <p :class="detailRow['流量异常'] ? 'basis-problem' : ''">{{ detailRow['流量异常说明'] }}</p>
          </section>

          <section class="basis-section basis-entry">
            <h4>录入时判定（复核依据，永久不可变）</h4>
            <p>
              结论：<span class="tag" :class="conclusionClass(entryBasis?.['结论'])">{{ entryBasis?.['结论'] }}</span>
              ｜口径版本：{{ entryBasis?.['规则版本'] }}
              ｜规则指纹：{{ entryBasis?.['规则指纹'] }}
              ｜依据指纹：{{ entryBasis?.['依据指纹'] }}
            </p>
            <p class="basis-text">{{ entryBasis?.['依据说明'] }}</p>
            <p class="cell-note" v-if="entryBasis?.['规则版本'] !== detailRow['判定规则版本']">
              该记录已按新口径重判：录入依据为 {{ entryBasis?.['规则版本'] }}，当前结论按 {{ detailRow['判定规则版本'] }} 生成；
              复核人看到的录入依据与当时一致。
            </p>
          </section>

          <section class="basis-section">
            <h4>判定历史</h4>
            <ul class="history-list">
              <li v-for="(h, i) in (detailRow['判定历史'] ?? [])" :key="i">
                <span class="tag" :class="conclusionClass(h['结论'])">{{ h['结论'] }}</span>
                {{ h['规则版本'] }}（{{ h['规则指纹'] }}）· {{ h['判定时间'] }}
                <span class="cell-note">{{ h['依据说明'] }}</span>
              </li>
            </ul>
          </section>
        </div>
      </div>
    </div>

    <!-- 登记记录 -->
    <div v-if="createOpen" class="modal-mask" @click.self="createOpen = false">
      <div class="modal">
        <div class="modal-head">
          <h3>登记出水记录</h3>
          <button class="link" type="button" @click="createOpen = false">关闭</button>
        </div>
        <form class="modal-body" @submit.prevent="submitCreate">
          <label v-for="f in createFields" :key="f.key" class="form-item">
            <span>{{ f.label }}<em v-if="f.required">*</em></span>
            <input v-model="createForm[f.key]" :placeholder="f.hint" />
          </label>
          <p class="cell-note">
            指标留空或填写非数字时，记录可以登记，但不会生成达标/超标结论，会逐条说明原因；
            排放流量为 0、负值或超厂站上限时单独标记流量异常。
          </p>
          <div class="modal-foot">
            <button class="btn primary" type="submit">登记并自动判定</button>
            <span v-if="createError" class="error-text">{{ createError }}</span>
          </div>
        </form>
      </div>
    </div>

    <!-- 判定口径管理 -->
    <div v-if="rulesOpen" class="modal-mask" @click.self="rulesOpen = false">
      <div class="modal modal-wide">
        <div class="modal-head">
          <h3>出水排放限值判定口径</h3>
          <button class="link" type="button" @click="rulesOpen = false">关闭</button>
        </div>
        <div class="modal-body">
          <section class="basis-section">
            <h4>版本清单</h4>
            <table class="data-table">
              <thead><tr><th>版本</th><th>发布时间</th><th>规则指纹</th><th>默认标准</th><th>包含标准</th><th>说明</th></tr></thead>
              <tbody>
                <tr v-for="v in versions" :key="v.version">
                  <td>{{ v.version }}<span v-if="currentVersion && v.version === currentVersion.version" class="tag">当前</span></td>
                  <td>{{ v.published_at }}</td>
                  <td>{{ v.fingerprint }}</td>
                  <td>{{ v.default_standard }}</td>
                  <td>{{ (v.standards ?? []).join('、') }}</td>
                  <td>{{ v.note }}</td>
                </tr>
              </tbody>
            </table>
          </section>

          <section class="basis-section">
            <h4>当前口径明细</h4>
            <pre class="rule-json">{{ rulesJson }}</pre>
          </section>

          <section class="basis-section">
            <h4>发布新版口径（保存后自动重判全部既有记录）</h4>
            <p class="cell-note">
              可直接修改上方 JSON：standards 为各标准 COD/氨氮/总磷 限值（mg/L），stations 为厂站绑定的标准与
              排放流量上限（m³/d，留空表示只核查数值有效性），default_standard 为未配置厂站的兜底标准。
            </p>
            <textarea v-model="draftJson" class="rule-editor" rows="12"></textarea>
            <div class="modal-foot">
              <button class="btn primary" type="button" @click="publishRules">校验并发布新版</button>
              <button class="btn" type="button" @click="rejudgeAll">不改口径，全部重判</button>
              <span v-if="rulesError" class="error-text">{{ rulesError }}</span>
            </div>
          </section>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/effluent'
const RULES_ENDPOINT = '/api/effluent-rules'
const columns = [
  '记录编号', '所属厂站', '监测时间',
  'COD出水值', '氨氮出水值', '总磷出水值', '排放流量',
  '判定结论', '超标倍数', '判定口径', '处置状态',
]
const conclusions = ['达标', '超标', '无法判定']
const actions = ['预警通知', '关阀截流', '恢复排放']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = reactive<Record<string, string>>({ keyword: '', station: '', status: '', flow_abnormal: '' })

const detailRow = ref<Row | null>(null)
const entryBasis = computed(() => detailRow.value?.['录入判定'] ?? null)

const currentVersion = ref<Row | null>(null)
const versions = ref<Row[]>([])
const rulesOpen = ref(false)
const rulesJson = ref('')
const draftJson = ref('')
const rulesError = ref('')

const createOpen = ref(false)
const createError = ref('')
const createForm = reactive<Record<string, string>>({
  记录编号: '', 所属厂站: '', 监测时间: '',
  COD出水值: '', 氨氮出水值: '', 总磷出水值: '', 排放流量: '',
})
const createFields = [
  { key: '记录编号', label: '记录编号', required: true, hint: '如 EFFL-0008' },
  { key: '所属厂站', label: '所属厂站', required: true, hint: '与口径中的厂站名称一致' },
  { key: '监测时间', label: '监测时间', required: true, hint: '如 2026-09-27 08:00' },
  { key: 'COD出水值', label: 'COD出水值 mg/L', required: false, hint: '可留空，留空不出结论' },
  { key: '氨氮出水值', label: '氨氮出水值 mg/L', required: false, hint: '可留空，留空不出结论' },
  { key: '总磷出水值', label: '总磷出水值 mg/L', required: false, hint: '可留空，留空不出结论' },
  { key: '排放流量', label: '排放流量 m³/d', required: false, hint: '0/负值/超限将标记流量异常' },
]

const stats = computed(() => {
  const pass = rows.value.filter(r => r['判定结论'] === '达标').length
  const fail = rows.value.filter(r => r['判定结论'] === '超标').length
  const unknown = rows.value.filter(r => r['判定结论'] === '无法判定').length
  const flow = rows.value.filter(r => r['流量异常']).length
  return [
    { label: '达标记录（本页）', value: pass, cls: 'tag-ok' },
    { label: '超标记录（本页）', value: fail, cls: 'tag-fail' },
    { label: '无法判定（本页）', value: unknown, cls: 'tag-unknown' },
    { label: '排放流量异常（本页）', value: flow, cls: 'tag-warn' },
  ]
})

function conclusionClass(conclusion: string | undefined): string {
  if (conclusion === '达标') return 'tag-ok'
  if (conclusion === '超标') return 'tag-fail'
  return 'tag-unknown'
}

function showVal(row: Row, field: string): string {
  const v = row[field]
  return v === null || v === undefined || v === '' ? '—' : String(v)
}

function resetFilters() {
  Object.assign(filters, { keyword: '', station: '', status: '', flow_abnormal: '' })
  void reload()
}

function buildQuery(): string {
  const params = new URLSearchParams()
  if (filters.keyword) params.set('keyword', filters.keyword)
  if (filters.station) params.set('station', filters.station)
  if (filters.status) params.set('status', filters.status)
  if (filters.flow_abnormal) params.set('flow_abnormal', 'true')
  params.set('size', '200')
  return params.toString()
}

function exportRows() {
  window.open(`${ENDPOINT}/export?${buildQuery()}`, '_blank')
}

async function reload() {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}?${buildQuery()}`)
    if (!response.ok) throw new Error('出水记录列表读取失败')
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '出水监测列表读取失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '出水监测动作未生效')
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '出水监测操作失败'
  }
}

async function rejudgeRow(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/rejudge`, { method: 'POST', body: '{}' })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '重判失败')
    noticeMessage.value = payload.message
    await reload()
    if (detailRow.value?.id === row.id) openDetail(payload.entry)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '单条重判失败'
  }
}

async function rejudgeAll() {
  errorMessage.value = ''
  try {
    const response = await request(`${RULES_ENDPOINT}/rejudge`, { method: 'POST', body: '{}' })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '全量重判失败')
    noticeMessage.value = payload.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '全量重判失败'
  }
}

function openDetail(row: Row) {
  detailRow.value = row
}

function openCreate() {
  createError.value = ''
  Object.assign(createForm, {
    记录编号: '', 所属厂站: '', 监测时间: '',
    COD出水值: '', 氨氮出水值: '', 总磷出水值: '', 排放流量: '',
  })
  createOpen.value = true
}

async function submitCreate() {
  createError.value = ''
  try {
    const response = await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ values: { ...createForm } }) })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    createOpen.value = false
    noticeMessage.value = payload.message
    await reload()
  } catch (error) {
    createError.value = error instanceof Error ? error.message : '登记失败'
  }
}

async function loadRules() {
  try {
    const [curResp, verResp] = await Promise.all([
      request(`${RULES_ENDPOINT}/current`),
      request(`${RULES_ENDPOINT}/versions`),
    ])
    if (!curResp.ok || !verResp.ok) throw new Error('判定口径读取失败')
    const current = await curResp.json()
    currentVersion.value = { version: current.version, fingerprint: current.fingerprint, default_standard: current.default_standard }
    versions.value = await verResp.json()
    const display = {
      note: current.note,
      default_standard: current.default_standard,
      standards: current.standards,
      stations: current.stations,
    }
    rulesJson.value = JSON.stringify(display, null, 2)
    draftJson.value = rulesJson.value
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '判定口径读取失败'
  }
}

function openRules() {
  rulesError.value = ''
  rulesOpen.value = true
  void loadRules()
}

async function publishRules() {
  rulesError.value = ''
  let values: any
  try {
    values = JSON.parse(draftJson.value)
  } catch {
    rulesError.value = '口径 JSON 格式不正确，请检查后再发布'
    return
  }
  try {
    const response = await request(`${RULES_ENDPOINT}/publish`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '发布失败')
    noticeMessage.value = payload.message
    await Promise.all([loadRules(), reload()])
  } catch (error) {
    rulesError.value = error instanceof Error ? error.message : '口径发布失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 12px;
  background: #e8eef7;
  color: #334155;
  white-space: nowrap;
}
.tag-ok { background: #e7f6ec; color: #157347; }
.tag-fail { background: #fdeceb; color: #b42318; }
.tag-unknown { background: #fff4e0; color: #b25e09; }
.tag-warn { background: #fdeceb; color: #b42318; margin-left: 4px; }
.cell-note { color: var(--muted); font-size: 12px; margin: 2px 0 0; }
.notice-text { color: #157347; }
.foot-rule { margin-left: auto; }
.modal-mask {
  position: fixed; inset: 0; background: rgba(15, 23, 42, 0.45);
  display: flex; align-items: flex-start; justify-content: center; z-index: 50; padding: 40px 16px;
  overflow-y: auto;
}
.modal {
  background: #fff; border-radius: 10px; width: 520px; max-width: 100%;
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.2);
}
.modal-wide { width: 860px; }
.modal-head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 14px 18px; border-bottom: 1px solid var(--border);
}
.modal-head h3 { margin: 0; font-size: 15px; }
.modal-body { padding: 14px 18px; }
.modal-foot { display: flex; align-items: center; gap: 12px; margin-top: 12px; }
.form-item { display: block; margin-bottom: 10px; }
.form-item span, .basis-section h4 { display: block; font-size: 13px; margin-bottom: 4px; }
.form-item em { color: #b42318; font-style: normal; margin-left: 2px; }
.form-item input { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.basis-section { margin-bottom: 16px; }
.basis-section h4 { font-size: 13px; color: #1f2937; }
.basis-section p { font-size: 13px; margin: 4px 0; }
.basis-over { color: #b42318; }
.basis-problem { color: #b25e09; }
.basis-entry { background: #f8fafc; border: 1px dashed var(--border); border-radius: 8px; padding: 10px 12px; }
.basis-text {
  background: #fff; border: 1px solid var(--border); border-radius: 6px;
  padding: 8px 10px; font-size: 12px; line-height: 1.7; white-space: pre-wrap;
}
.history-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; }
.history-list li { font-size: 12px; }
.rule-json {
  background: #0f172a; color: #dbeafe; border-radius: 8px; padding: 10px 12px;
  font-size: 12px; max-height: 220px; overflow: auto; margin: 0;
}
.rule-editor {
  width: 100%; font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px;
  border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px;
}
</style>
