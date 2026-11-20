<template>
  <section class="page" data-module="location">
    <header class="page-head">
      <div>
        <h2>采集行定位与缺项处置</h2>
        <p class="page-desc">
          从正文样本与上传记录解析原始坐标，接入解析和采集结果校验共用同一条定位记录；
          复核时按原处置号回到当时的物理行，按新样本看当前位置。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="loadAll">刷新</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <section class="panel">
      <header class="panel-head">
        <h3>来源与解析</h3>
        <span class="muted">带引号的多行字段按语法切分；解析异常仍保留原文位置</span>
      </header>
      <table class="data-table">
        <thead>
          <tr><th>来源</th><th>编号</th><th>版本</th><th>导出字段</th><th>解析状态</th><th>物理行 / 记录</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="source in sources" :key="source.key">
            <td>{{ source.type }}</td>
            <td>{{ source.no }}</td>
            <td>{{ source.version || '—' }}</td>
            <td class="wrap-cell">{{ source.fields || '—' }}</td>
            <td :class="{ 'error-text': source.abnormal }">{{ source.status || '待解析' }}</td>
            <td>{{ source.lines ?? '—' }} / {{ source.count ?? '—' }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="parseSource(source)">解析定位</button>
            </td>
          </tr>
          <tr v-if="!sources.length">
            <td colspan="7" class="empty-state">暂无正文样本或上传记录</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <header class="panel-head">
        <h3>采集行定位数据集</h3>
        <span class="muted">共 {{ locations.length }} 条定位记录</span>
      </header>
      <table class="data-table">
        <thead>
          <tr><th>定位编号</th><th>来源</th><th>运单号</th><th>物理行</th><th>字段定位（行:列）</th><th>解析状态</th><th>异常说明</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in locations" :key="String(row.id)">
            <td>{{ row['定位编号'] }}</td>
            <td>{{ row['来源类型'] }} {{ row['来源编号'] }}</td>
            <td>{{ row['运单号'] }}</td>
            <td>{{ row['物理行'] }}</td>
            <td class="wrap-cell">
              <span v-for="field in row['字段定位']" :key="field['字段名']" class="field-chip">
                {{ field['字段名'] }}={{ field['值'] || '∅' }}（{{ field['起始行'] }}:{{ field['起始列'] }}-{{ field['结束行'] }}:{{ field['结束列'] }}）
              </span>
            </td>
            <td :class="{ 'error-text': row['解析状态'] === '解析异常' }">{{ row['解析状态'] }}</td>
            <td class="wrap-cell">{{ row['异常说明'] || '—' }}</td>
          </tr>
          <tr v-if="!locations.length">
            <td colspan="7" class="empty-state">还没有定位记录，先在上方解析一个来源</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <header class="panel-head">
        <h3>缺项处置明细</h3>
        <div class="panel-tools">
          <label class="inline-field">
            <span>复核人</span>
            <input v-model="assignee" placeholder="复核人" />
          </label>
          <button class="btn" type="button" @click="loadTodos">按待办领取</button>
        </div>
      </header>
      <table class="data-table">
        <thead>
          <tr><th>处置编号</th><th>运单号</th><th>缺项字段</th><th>当前定位</th><th>当前物理行</th><th>状态</th><th>领取人</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in dispositions" :key="String(row.id)">
            <td>{{ row['处置编号'] }}</td>
            <td>{{ row['运单号'] }}</td>
            <td>{{ row['字段名'] }}（{{ row['缺项原因'] }}）</td>
            <td>{{ row['当前定位编号'] }}</td>
            <td>{{ row['当前位置']?.['物理行'] }}</td>
            <td>{{ row['status'] }}</td>
            <td>{{ row['领取人'] || '—' }}</td>
            <td class="row-actions">
              <button class="link" type="button" @click="claim(row)">领取</button>
              <button class="link" type="button" @click="openRecheck(row)">再次校验</button>
              <button class="link" type="button" @click="applyRecheck(row)">同步位置</button>
            </td>
          </tr>
          <tr v-if="!dispositions.length">
            <td colspan="8" class="empty-state">暂无缺项处置；可对来源执行采集结果校验生成</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-if="recheck" class="panel">
      <header class="panel-head">
        <h3>再次校验结果 · {{ recheck['运单号'] }}</h3>
        <span :class="recheck['can_cover'] ? 'ok-text' : 'error-text'">
          {{ recheck['can_cover'] ? '手上核查可覆盖最新位置' : '现有核查不能完全覆盖最新位置' }}
        </span>
      </header>

      <div class="recheck-grid">
        <article class="recheck-card">
          <h4>能否覆盖</h4>
          <ul class="plain-list">
            <li v-for="item in recheck['coverage']" :key="item['字段名']">
              <strong>{{ item['字段名'] }}</strong>：{{ item['结论'] }}
              <span class="muted">
                （原 {{ item['旧位置']?.['物理行'] ?? '—' }} 行 → 新 {{ item['新位置']?.['物理行'] }} 行）
                {{ item['说明'] }}
              </span>
            </li>
          </ul>
        </article>
        <article class="recheck-card">
          <h4>还应领取的待办</h4>
          <ul class="plain-list">
            <li v-for="item in recheck['should_claim']" :key="item['字段名'] + String(item['处置编号'])">
              {{ item['字段名'] }}：{{ item['建议动作'] }}
              <span class="muted">{{ item['处置编号'] || '待生成' }} · {{ item['说明'] }}</span>
            </li>
            <li v-if="!recheck['should_claim'].length" class="muted">没有额外的待办</li>
          </ul>
        </article>
        <article class="recheck-card">
          <h4>按原处置号回放</h4>
          <ul class="plain-list">
            <li v-for="(detail, no) in recheck['locate_by_old_disposition']" :key="no">
              {{ no }}：当时第 {{ detail?.['当时位置']?.['物理行'] ?? '—' }} 行，
              来源 {{ detail?.['当时位置']?.['上传编号'] || detail?.['当时位置']?.['样本编号'] || '—' }}
              <span class="muted">
                ；当前第 {{ detail?.['当前位置']?.['物理行'] ?? '—' }} 行
              </span>
            </li>
          </ul>
        </article>
        <article class="recheck-card">
          <h4>按新样本定位</h4>
          <p>
            {{ recheck['locate_by_new_sample']['定位编号'] }} ·
            {{ recheck['locate_by_new_sample']['位置']['物理行'] }} 行
          </p>
          <p class="muted wrap-cell">{{ recheck['locate_by_new_sample']['位置']['值'] }}</p>
        </article>
      </div>
    </section>

    <footer class="page-foot">
      <span>共 {{ locations.length }} 条定位记录、{{ dispositions.length }} 条缺项处置</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="message" class="ok-text">{{ message }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchJson, request } from '@/api/client'

type Dict = Record<string, any>

const sources = ref<Dict[]>([])
const locations = ref<Dict[]>([])
const dispositions = ref<Dict[]>([])
const recheck = ref<Dict | null>(null)
const assignee = ref('复核员甲')
const errorMessage = ref('')
const message = ref('')

const stats = computed(() => [
  { label: '定位记录', value: locations.value.length },
  { label: '解析异常', value: locations.value.filter((row) => row['解析状态'] === '解析异常').length },
  { label: '待办处置', value: dispositions.value.filter((row) => row['status'] === '待领取' || row['status'] === '核查中').length },
])

function toSourceRows(samples: Dict[], uploads: Dict[]): Dict[] {
  const sampleRows = samples.map((row) => ({
    key: `sample-${row.id}`,
    type: '正文样本',
    no: row['样本编号'],
    version: row['版本'],
    fields: row['字段名'],
    status: row['解析状态'],
    abnormal: row['解析状态'] === '解析异常',
    lines: row['物理行数'],
    count: row['记录数'],
  }))
  const uploadRows = uploads.map((row) => ({
    key: `upload-${row.id}`,
    type: '上传记录',
    no: row['上传编号'],
    version: row['版本'],
    fields: '',
    status: row['解析状态'],
    abnormal: row['解析状态'] === '解析异常',
    lines: row['物理行数'],
    count: row['记录数'],
  }))
  return [...uploadRows, ...sampleRows]
}

async function loadSources() {
  const payload = await fetchJson<{ samples: Dict[]; uploads: Dict[] }>('/api/location/sources')
  sources.value = toSourceRows(payload.samples ?? [], payload.uploads ?? [])
}

async function loadLocations() {
  const payload = await fetchJson<{ items: Dict[] }>('/api/location/export')
  locations.value = (payload.items ?? []).slice().reverse()
}

async function loadDispositions() {
  const payload = await fetchJson<{ items: Dict[] }>('/api/location/dispositions?size=200')
  dispositions.value = payload.items ?? []
}

async function loadAll() {
  errorMessage.value = ''
  try {
    await Promise.all([loadSources(), loadLocations(), loadDispositions()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据读取失败'
  }
}

async function parseSource(source: Dict) {
  errorMessage.value = ''
  message.value = ''
  try {
    const payload = await fetchJson<Dict>(
      `/api/location/parse?source_no=${encodeURIComponent(source.no)}&source_type=${encodeURIComponent(source.type)}`,
    )
    message.value = `已解析 ${payload['记录数']} 条记录，共 ${payload['物理行数']} 物理行`
    await loadAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '解析失败'
  }
}

async function claim(row: Dict) {
  errorMessage.value = ''
  try {
    const response = await request(`/api/location/dispositions/${row.id}/claim`, {
      method: 'POST',
      body: JSON.stringify({ values: { 复核人: assignee.value } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || '领取失败')
    }
    message.value = payload.message
    await loadAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '领取失败'
  }
}

async function loadTodos() {
  errorMessage.value = ''
  try {
    const payload = await fetchJson<{ items: Dict[] }>(
      `/api/location/dispositions/todos?assignee=${encodeURIComponent(assignee.value)}`,
    )
    dispositions.value = payload.items ?? []
    message.value = `待办 ${payload.items.length} 条`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '待办读取失败'
  }
}

async function runRecheck(row: Dict, autoClaim: boolean) {
  errorMessage.value = ''
  try {
    const response = await request('/api/location/recheck', {
      method: 'POST',
      body: JSON.stringify({
        disposition_ref: row['处置编号'],
        assignee: assignee.value,
        auto_claim: autoClaim,
      }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || '再次校验失败')
    }
    recheck.value = payload.entry
    message.value = payload.message
    await loadAll()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '再次校验失败'
  }
}

function openRecheck(row: Dict) {
  return runRecheck(row, false)
}

function applyRecheck(row: Dict) {
  return runRecheck(row, true)
}

onMounted(loadAll)
</script>
