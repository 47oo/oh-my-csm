<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { enhancedDashboardApi, type DashboardEnhanced } from '@/api/m3'
import { useAuthStore } from '@/stores/auth'
import { extractErrorMessage } from '@/api/client'
import AssetDetailDrawer from '@/components/AssetDetailDrawer.vue'

const auth = useAuthStore()
const message = useMessage()
const loading = ref(false)
const summary = ref<DashboardEnhanced | null>(null)

const urlDrawerOpen = ref(false)
const inspectUrlId = ref<number | null>(null)

const assetCards = [
  { key: 'clusters', label: '集群', path: '/clusters' },
  { key: 'devices', label: '设备', path: '/devices' },
  { key: 'nics', label: '网卡', path: '/nics' },
  { key: 'subnets', label: '网段 (IPAM)', path: '/subnets' },
  { key: 'ips', label: 'IP 地址', path: '/ips' },
  { key: 'services', label: '服务', path: '/services' },
  { key: 'urls', label: 'URL 入口', path: '/urls' },
]

async function loadSummary() {
  loading.value = true
  try {
    summary.value = (await enhancedDashboardApi.summary()).data
  } catch (err) {
    message.error(extractErrorMessage(err, '加载看板失败'))
  } finally {
    loading.value = false
  }
}

onMounted(loadSummary)
</script>

<template>
  <div class="space-y-4">
    <n-card size="small">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div class="text-lg font-semibold text-gray-800">
            欢迎回来，{{ auth.user?.display_name }}
          </div>
          <div class="text-xs text-gray-500 mt-1">
            随时按 <kbd class="px-1.5 py-0.5 bg-gray-100 rounded border">Ctrl + K</kbd> 输入 IP / 域名 / 主机名 / MAC 秒查全链路拓扑。
          </div>
        </div>
        <div class="flex flex-wrap gap-2">
          <n-tag
            v-for="a in auth.user?.assignments || []"
            :key="`g-${a.id}`"
            type="info"
            size="small"
            round
          >
            {{ a.group_name || '全局' }} · {{ a.role_name }}
          </n-tag>
          <n-tag
            v-for="c in auth.user?.cluster_assignments || []"
            :key="`c-${c.id}`"
            type="success"
            size="small"
            round
          >
            集群: {{ c.cluster_name || `#${c.cluster_id}` }} · {{ c.role_name }}
          </n-tag>
        </div>
      </div>
    </n-card>

    <n-spin :show="loading">
      <n-grid cols="2 s:4 m:7" responsive="screen" :x-gap="12" :y-gap="12">
        <n-gi v-for="card in assetCards" :key="card.key">
          <n-card size="small" hoverable class="cursor-pointer" @click="$router.push(card.path)">
            <n-statistic :label="card.label" :value="summary?.counts?.[card.key] ?? 0" />
          </n-card>
        </n-gi>
      </n-grid>
    </n-spin>

    <!-- Operational Alerts Row -->
    <n-grid cols="1 m:3" responsive="screen" :x-gap="14" :y-gap="14">
      <!-- High Utilization Subnets Top 5 -->
      <n-gi>
        <n-card title="网段使用率 Top 5" size="small" class="h-full">
          <template #header-extra>
            <n-button text type="primary" size="tiny" @click="$router.push('/subnets')">IPAM</n-button>
          </template>
          <div v-if="summary?.top_subnets?.length" class="space-y-3">
            <div v-for="s in summary.top_subnets" :key="s.id" class="space-y-1">
              <div class="flex justify-between text-xs">
                <span class="font-medium truncate">{{ s.cidr }} · {{ s.name }}</span>
                <span class="text-gray-500 shrink-0 ml-2">{{ s.allocated_count }}/{{ s.total_capacity }}</span>
              </div>
              <n-progress
                type="line"
                :percentage="s.utilization_pct"
                :status="s.utilization_pct >= 80 ? 'error' : s.utilization_pct >= 60 ? 'warning' : 'info'"
                :height="8"
              />
            </div>
          </div>
          <n-empty v-else description="暂无网段数据" size="small" />
        </n-card>
      </n-gi>

      <!-- SSL Certificate Expiring Alert -->
      <n-gi>
        <n-card title="HTTPS 证书到期预警（30天内）" size="small" class="h-full">
          <template #header-extra>
            <n-button text type="primary" size="tiny" @click="$router.push('/urls')">全部 URL</n-button>
          </template>
          <div v-if="summary?.expiring_urls?.length" class="space-y-2">
            <div
              v-for="u in summary.expiring_urls"
              :key="u.id"
              class="p-2 rounded border border-gray-100 hover:border-amber-400 cursor-pointer flex items-center justify-between text-xs"
              @click="() => { inspectUrlId = u.id; urlDrawerOpen = true }"
            >
              <div class="min-w-0">
                <div class="font-medium truncate">{{ u.name }}</div>
                <div class="text-gray-400 truncate">{{ u.domain }}</div>
              </div>
              <n-tag
                size="tiny"
                :type="u.days_remaining <= 15 ? 'error' : 'warning'"
                class="shrink-0 ml-2"
              >
                {{ u.days_remaining < 0 ? `已过期 ${-u.days_remaining}天` : `剩 ${u.days_remaining}天` }}
              </n-tag>
            </div>
          </div>
          <n-empty v-else description="近期无到期证书" size="small" />
        </n-card>
      </n-gi>

      <!-- Incomplete & Duplicate Data Quality Card -->
      <n-gi>
        <n-card title="台账待补全与重名提醒" size="small" class="h-full">
          <div class="space-y-2 text-xs">
            <div class="flex justify-between items-center p-2 rounded bg-slate-50 cursor-pointer hover:bg-slate-100" @click="$router.push('/urls')">
              <span>未关联服务的 URL 入口</span>
              <n-tag size="tiny" :type="(summary?.incomplete_counts?.urls_without_service || 0) > 0 ? 'warning' : 'success'">
                {{ summary?.incomplete_counts?.urls_without_service ?? 0 }}
              </n-tag>
            </div>
            <div class="flex justify-between items-center p-2 rounded bg-slate-50 cursor-pointer hover:bg-slate-100" @click="$router.push('/ips')">
              <span>未绑定网卡的主机 IP</span>
              <n-tag size="tiny" :type="(summary?.incomplete_counts?.host_ips_without_nic || 0) > 0 ? 'warning' : 'success'">
                {{ summary?.incomplete_counts?.host_ips_without_nic ?? 0 }}
              </n-tag>
            </div>
            <div class="flex justify-between items-center p-2 rounded bg-slate-50 cursor-pointer hover:bg-slate-100" @click="$router.push('/devices')">
              <span>未归属运维分组的设备</span>
              <n-tag size="tiny" :type="(summary?.incomplete_counts?.devices_without_group || 0) > 0 ? 'info' : 'success'">
                {{ summary?.incomplete_counts?.devices_without_group ?? 0 }}
              </n-tag>
            </div>
          </div>

          <div v-if="summary?.duplicate_hostnames?.length" class="mt-3 pt-2 border-t border-gray-100">
            <div class="text-xs text-gray-500 mb-1.5">重名设备提醒：</div>
            <div class="flex flex-wrap gap-1.5">
              <n-tag v-for="d in summary.duplicate_hostnames" :key="d.hostname" size="tiny" type="warning">
                {{ d.hostname }} × {{ d.count }}
              </n-tag>
            </div>
          </div>
        </n-card>
      </n-gi>
    </n-grid>

    <n-card title="近期操作审计动态" size="small">
      <template #header-extra>
        <n-button text type="primary" @click="$router.push('/audit')">查看全部</n-button>
      </template>
      <n-data-table
        size="small"
        :bordered="false"
        :data="summary?.recent_audits || []"
        :columns="[
          { title: '时间', key: 'created_at', width: 190, render: (r: any) => new Date(r.created_at).toLocaleString() },
          { title: '操作人', key: 'actor_username', width: 120 },
          { title: '动作', key: 'action', width: 130 },
          { title: '目标类型', key: 'target_type', width: 110 },
          { title: '对象标识', key: 'target_label' },
          { title: '来源 IP', key: 'client_ip', width: 130 },
        ]"
      />
    </n-card>

    <AssetDetailDrawer v-model:show="urlDrawerOpen" entity-type="url" :entity-id="inspectUrlId" />
  </div>
</template>
