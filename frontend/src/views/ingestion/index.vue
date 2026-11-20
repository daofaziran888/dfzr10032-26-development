<template>
  <section class="page" data-module="ingestion">
    <header class="page-head">
      <div>
        <h2>数据接入通道管理</h2>
        <p class="page-desc">维护接入通道，围绕通道编号、通道名称、输入格式、解析引擎做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记接入通道</button>
        <button class="btn" type="button" @click="exportRows">导出数据接入通道清单</button>
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
            <button class="link" type="button" @click="openParse(row)">接入解析</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无数据接入通道数据，可先登记接入通道</td>
        </tr>
      </tbody>
    </table>

    <section v-if="parseResult" class="panel">
      <header class="panel-head">
        <h3>接入解析结果 · {{ parseResult['来源编号'] }}</h3>
        <span class="muted">定位 {{ parseResult['items'].length }} 条 · 物理行 {{ parseResult['物理行数'] }}</span>
      </header>
      <table class="data-table">
        <thead>
          <tr><th>定位编号</th><th>运单号</th><th>物理行</th><th>字段定位</th><th>解析状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in parseResult['items']" :key="item['定位编号']">
            <td>{{ item['定位编号'] }}</td>
            <td>{{ item['运单号'] }}</td>
            <td>{{ item['物理行'] }}</td>
            <td class="wrap-cell">
              <span v-for="field in item['字段定位']" :key="field['字段名']" class="field-chip">
                {{ field['字段名'] }}（{{ field['起始行'] }}:{{ field['起始列'] }}）
              </span>
            </td>
            <td :class="{ 'error-text': item['解析状态'] === '解析异常' }">{{ item['解析状态'] }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据接入通道记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <Teleport to="body">
      <div v-if="showCreateModal" class="modal-overlay" @click.self="showCreateModal = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>登记接入通道</h3>
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
      <div v-if="showParseModal" class="modal-overlay" @click.self="showParseModal = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>接入解析</h3>
            <button class="btn ghost" type="button" @click="showParseModal = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitParse">
            <label class="modal-field">
              <span>来源编号</span>
              <input v-model="parseSourceNo" placeholder="UPLD-0002 或 SAMP-0001" required />
            </label>
            <p class="muted">解析结果复用采集行定位记录；带引号的多行字段按语法切分，抽取失败保留原文位置。</p>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="showParseModal = false">取消</button>
              <button class="btn primary" type="submit">开始解析</button>
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

const ENDPOINT = '/api/ingestion'
const columns = ["通道编号", "通道名称", "输入格式", "解析引擎", "并发数量", "背压阈值", "缓冲队列", "通道状态"]
const actions = ["开通通道", "暂停通道", "关闭通道"]
const statuses = ["待开通", "开通中", "运行中", "已关闭"]
const stats = [{"label": "运行中通道", "value": 0}, {"label": "待开通通道", "value": 0}, {"label": "已关闭通道", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreateModal = ref(false)
const createFields = ref<Record<string, string>>({})
const showParseModal = ref(false)
const parseSourceNo = ref('UPLD-0002')
const parseTarget = ref<Row | null>(null)
const parseResult = ref<Record<string, any> | null>(null)

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

function openParse(row: Row) {
  errorMessage.value = ''
  parseResult.value = null
  parseTarget.value = row
  showParseModal.value = true
}

async function submitParse() {
  errorMessage.value = ''
  if (!parseTarget.value) return
  try {
    const response = await request(`${ENDPOINT}/${parseTarget.value.id}/parse`, {
      method: 'POST',
      body: JSON.stringify({ values: { 来源编号: parseSourceNo.value } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.message || '接入解析失败')
    }
    parseResult.value = payload.entry
    showParseModal.value = false
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '接入解析失败'
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
      throw new Error('数据接入通道动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据接入通道操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('接入通道列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    updateStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据接入通道列表读取失败'
  }
}

onMounted(reload)
</script>
