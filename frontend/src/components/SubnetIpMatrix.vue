<script setup lang="ts">
import { h, ref, watch } from 'vue'
import { NButton, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import { subnetApi, type IpItem, type SubnetIpCell, type SubnetMatrixResponse } from '@/api/assets'
import { extractErrorMessage } from '@/api/client'

const props = defineProps<{
  subnetId: number | null
  refreshKey?: number
}>()

const emit = defineEmits<{
  (e: 'allocate-free', ip: string): void
  (e: 'inspect-ip', ipId: number): void
}>()

const message = useMessage()
const loading = ref(false)
const data = ref<SubnetMatrixResponse | null>(null)
const page = ref(1)

async function loadMatrix() {
  if (!props.subnetId) return
  loading.value = true
  try {
    data.value = (await subnetApi.matrix(props.subnetId, { page: page.value, page_size: 50 })).data
  } catch (err) {
    message.error(extractErrorMessage(err, '加载网段 IP 矩阵失败'))
  } finally {
    loading.value = false
  }
}

watch(() => [props.subnetId, props.refreshKey], () => {
  page.value = 1
  loadMatrix()
}, { immediate: true })

function cellClass(status: string): string {
  switch (status) {
    case 'free':
      return 'bg-emerald-50 border-emerald-300 text-emerald-700 hover:bg-emerald-100'
    case 'allocated':
      return 'bg-blue-500 border-blue-600 text-white hover:bg-blue-600'
    case 'vip':
      return 'bg-purple-500 border-purple-600 text-white hover:bg-purple-600'
    case 'reserved':
      return 'bg-amber-400 border-amber-500 text-amber-950 hover:bg-amber-500'
    case 'conflict':
      return 'bg-rose-500 border-rose-600 text-white hover:bg-rose-600'
    case 'gateway':
      return 'bg-slate-600 border-slate-700 text-white'
    default:
      return 'bg-gray-200 border-gray-300 text-gray-600'
  }
}

function lastOctet(ip: string): string {
  const parts = ip.split('.')
  return parts.length === 4 ? `.${parts[3]}` : ip
}

function handleCellClick(cell: SubnetIpCell) {
  if (cell.ip_id) {
    emit('inspect-ip', cell.ip_id)
  } else if (cell.status === 'free') {
    emit('allocate-free', cell.ip)
  } else if (cell.is_gateway) {
    message.info(`网关地址 ${cell.ip}`)
  }
}

const listColumns: DataTableColumns<IpItem> = [
  {
    title: 'IP 地址',
    key: 'ip_address',
    render: (r) =>
      h(
        NButton,
        { text: true, type: 'primary', onClick: () => emit('inspect-ip', r.id) },
        { default: () => r.ip_address },
      ),
  },
  { title: '类型', key: 'ip_type', width: 90, render: (r) => h(NTag, { size: 'small', type: r.ip_type === 'vip' ? 'warning' : 'info' }, { default: () => r.ip_type }) },
  { title: '状态', key: 'status', width: 100 },
  { title: '绑定设备 / 网卡', key: 'nic', render: (r) => (r.device_hostname ? `${r.device_hostname} : ${r.nic_name}` : '未绑定网卡') },
  { title: '用途', key: 'usage' },
]
</script>

<template>
  <n-spin :show="loading">
    <div v-if="data" class="space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-3 bg-slate-50 p-3 rounded border border-slate-200">
        <div class="flex flex-wrap items-center gap-3 text-xs">
          <span class="font-semibold text-sm">{{ data.subnet.cidr }}</span>
          <span v-if="data.subnet.gateway" class="text-gray-500">网关: {{ data.subnet.gateway }}</span>
          <n-tag size="small" type="success">空闲 {{ data.stats.free }}</n-tag>
          <n-tag size="small" type="info">已分配 {{ data.stats.allocated }}</n-tag>
          <n-tag size="small" :color="{ color: '#f3e8ff', textColor: '#7e22ce', borderColor: '#d8b4fe' }">VIP {{ data.stats.vip }}</n-tag>
          <n-tag size="small" type="warning">预留 {{ data.stats.reserved }}</n-tag>
          <n-tag v-if="data.stats.conflict" size="small" type="error">冲突 {{ data.stats.conflict }}</n-tag>
        </div>
        <div class="w-180px">
          <n-progress type="line" :percentage="data.subnet.utilization_pct" :indicator-placement="'inside'" />
        </div>
      </div>

      <template v-if="data.mode === 'matrix'">
        <div class="text-xs text-gray-500">
          点击 <span class="text-emerald-600 font-medium">绿色空闲格</span> 可一键分配该 IP；点击 <span class="text-blue-600 font-medium">已占用格</span> 查看全景关联详情。
        </div>
        <div class="grid grid-cols-8 sm:grid-cols-12 md:grid-cols-16 gap-1.5">
          <n-tooltip v-for="cell in data.cells" :key="cell.ip" trigger="hover">
            <template #trigger>
              <button
                type="button"
                class="h-8 rounded border text-xs font-mono cursor-pointer transition flex items-center justify-center"
                :class="cellClass(cell.status)"
                @click="handleCellClick(cell)"
              >
                {{ lastOctet(cell.ip) }}
              </button>
            </template>
            <div class="text-xs space-y-0.5">
              <div class="font-semibold">{{ cell.ip }}</div>
              <div>状态: {{ cell.status }} {{ cell.is_gateway ? '(网关)' : '' }}</div>
              <div v-if="cell.device_hostname">设备: {{ cell.device_hostname }}</div>
              <div v-if="cell.usage">用途: {{ cell.usage }}</div>
            </div>
          </n-tooltip>
        </div>
      </template>

      <template v-else>
        <n-alert type="info">
          该网段容量超过 4096（大于 /20 或 IPv6），已自动切换为「已分配 IP 分页列表」模式以保持流畅体验。
        </n-alert>
        <n-data-table size="small" :columns="listColumns" :data="data.allocated_items" :bordered="false" />
        <div class="flex justify-end">
          <n-pagination v-model:page="page" :page-size="50" :item-count="data.total_allocated" @update:page="loadMatrix" />
        </div>
      </template>
    </div>
  </n-spin>
</template>
