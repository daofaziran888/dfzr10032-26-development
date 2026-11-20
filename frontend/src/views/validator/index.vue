<template>
  <section class="page" data-module="validator">
    <header class="page-head">
      <div>
        <h2>采集结果校验管理</h2>
        <p class="page-desc">维护校验规则，围绕规则编号、校验字段、校验类型、期望值做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记校验规则</button>
        <button class="btn" type="button" @click="exportRows">导出采集结果校验清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" @click="openCheck(row)">缺项校验</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无采集结果校验数据，可先登记校验规则</td>
        </tr>
      </tbody>
    </table>

    <section v-if="checkResult" class="panel">
      <header class="panel-head">
        <h3>缺项处置 · {{ checkResult['来源编号'] }}</h3>
        <span class="muted">定位 {{ checkResult['定位条数'] }} 条</span>
      </header>
      <table class="data-table">
        <thead>
          <tr><th>处置编号</th><th>运单号</th><th>字段</th><th>当前定位</th><th>物理行</th><th>状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in [...checkResult['新建'], ...checkResult['更新']]" :key="item['处置编号']">
            <td>{{ item['处置编号'] }}</td>
            <td>{{ item['运单号'] }}</td>
            <td>{{ item['字段名'] }}</td>
            <td>{{ item['当前定位编号'] }}</td>
            <td>{{ item['当前位置']?.['物理行'] }}</td>
            <td>{{ item['status'] }}</td>
          </tr>
          <tr v-if="!checkResult['新建'].length && !checkResult['更新'].length">
            <td colspan="6" class="empty-state">该来源没有新增缺项</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条采集结果校验记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <Teleport to="body">
      <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>登记校验规则</h3>
            <button class="btn ghost" type="button" @click="showCreateModal = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitCreate">
            <label v-for="field in columns.slice(0, 3)" :key="field" class="modal-field">
              <span>{{ field }}</span>
              <input v-model="createFields[field]" :placeholder="`请输入${field}`" required />
            </label>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="showCreateModal = false">取消</button>
              <button class="btn primary" type="submit">提交登记</button>
            </footer>
          </form>
        </div>
      </div>
      <div v-if="showCheckModal" class="modal-overlay" @click.self="showCheckModal = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>缺项校验</h3>
            <button class="btn ghost" type="button" @click="showCheckModal = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitCheck">
            <label class="modal-field">
              <span>来源编号</span>
              <input v-model="checkSourceNo" placeholder="UPLD-0002 或 SAMP-0001" required />
            </label>
            <p class="muted">读取采集行定位数据集生成缺项处置；同一票货重投时按运单号归并，不重复建单。</p>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="showCheckModal = false">取消</button>
              <button class="btn primary" type="submit">开始校验</button>
            </footer>
          </form>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/validator'
const columns = ["规则编号", "校验字段", "校验类型", "期望值", "容错范围", "采样比例", "阻断级别", "规则状态"]
const actions = ["提交测试", "启用规则", "停用规则"]
const statuses = ["草稿", "待测试", "已启用", "已停用"]
const stats = [{"label": "规则总数", "value": 0}, {"label": "已启用规则", "value": 0}, {"label": "待测试规则", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreateModal = ref(false)
const createFields = ref<Record<string, string>>({})
const showCheckModal = ref(false)
const checkSourceNo = ref('UPLD-0002')
const checkTarget = ref<Row | null>(null)
const checkResult = ref<Record<string, any> | null>(null)

function updateStats() {
  const data = rows.value
  stats.length > 0 && (stats[0].value = data.filter((r: Row) => r.status === statuses[1] || r.pending).length)
  stats.length > 1 && (stats[1].value = data.filter((r: Row) => r.status && !r.pending && !r.abnormal).length)
  stats.length > 2 && (stats[2].value = data.filter((r: Row) => r.abnormal).length)
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCheck(row: Row) {
  errorMessage.value = ''
  checkResult.value = null
  checkTarget.value = row
  showCheckModal.value = true
}

async function submitCheck() {
  errorMessage.value = ''
  if (!checkTarget.value) return
  try {
    const response = await request(`${ENDPOINT}/${checkTarget.value.id}/check`, {
      method: 'POST',
      body: JSON.stringify({ values: { 来源编号: checkSourceNo.value } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || '缺项校验失败')
    }
    checkResult.value = payload.entry
    showCheckModal.value = false
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '缺项校验失败'
  }
}

function openCreate() {
  errorMessage.value = ''
  createFields.value = {}
  showCreateModal.value = true
}

async function submitCreate() {
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: createFields.value }),
    })
    if (!response.ok) {
      const data = await response.json()
      throw new Error(data.message || '登记失败')
    }
    showCreateModal.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error('采集结果校验动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集结果校验操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('校验规则列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    updateStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '采集结果校验列表读取失败'
  }
}

onMounted(reload)
</script>
