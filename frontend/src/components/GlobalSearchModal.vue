<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { searchApi, type GlobalSearchResponse, type SearchHit } from '@/api/m3'
import type { AssetEntityType } from '@/api/assets'
import { extractErrorMessage } from '@/api/client'
import AssetDetailDrawer from '@/components/AssetDetailDrawer.vue'

const props = defineProps<{
  show: boolean
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
}>()

const router = useRouter()
const message = useMessage()
const keyword = ref('')
const loading = ref(false)
const result = ref<GlobalSearchResponse | null>(null)
let debounceTimer: ReturnType<typeof setTimeout> | null = null

const drawerOpen = ref(false)
const drawerType = ref<AssetEntityType | null>(null)
const drawerId = ref<number | null>(null)

watch(
  () => props.show,
  (open) => {
    if (open) {
      keyword.value = ''
      result.value = null
    }
  },
)

watch(keyword, (val) => {
  if (debounceTimer) clearTimeout(debounceTimer)
  const q = val.trim()
  if (!q) {
    result.value = null
    return
  }
  debounceTimer = setTimeout(async () => {
    loading.value = true
    try {
      result.value = (await searchApi.query(q)).data
    } catch (err) {
      message.error(extractErrorMessage(err, '搜索失败'))
    } finally {
      loading.value = false
    }
  }, 220)
})

function openHit(hit: SearchHit) {
  emit('update:show', false)
  if (hit.entity_type === 'subnet') {
    router.push('/subnets')
    message.info(`已定位网段 ${hit.title}，可在网段列表查看 IP 矩阵`)
    return
  }
  drawerType.value = hit.entity_type
  drawerId.value = hit.id
  drawerOpen.value = true
}
</script>

<template>
  <n-modal
    :show="show"
    preset="card"
    title="全局聚合秒搜（IP / 域名 / URL / 主机名 / MAC / 服务 / 集群 / 网段）"
    class="w-680px"
    @update:show="(v: boolean) => emit('update:show', v)"
  >
    <n-input
      v-model:value="keyword"
      size="large"
      autofocus
      clearable
      placeholder="输入任意 IP、域名、主机名、MAC 或服务名立即反查..."
    />

    <n-spin :show="loading" class="mt-4">
      <div v-if="result && result.groups.length" class="space-y-4 max-h-420px overflow-y-auto pr-1">
        <div v-for="grp in result.groups" :key="grp.entity_type">
          <div class="text-xs font-semibold text-gray-500 mb-1.5">{{ grp.label }} ({{ grp.items.length }})</div>
          <div class="space-y-1.5">
            <div
              v-for="hit in grp.items"
              :key="`${hit.entity_type}-${hit.id}`"
              class="p-2.5 rounded border border-gray-200 hover:border-blue-500 hover:bg-blue-50/40 cursor-pointer flex items-center justify-between transition"
              @click="openHit(hit)"
            >
              <div class="min-w-0">
                <div class="font-medium text-sm text-gray-800 truncate">{{ hit.title }}</div>
                <div class="text-xs text-gray-500 truncate">{{ hit.subtitle || '—' }}</div>
              </div>
              <div class="flex items-center gap-1.5 shrink-0 ml-3">
                <n-tag v-if="hit.group_name" size="tiny">{{ hit.group_name }}</n-tag>
                <n-tag v-if="hit.badge" size="tiny" type="info">{{ hit.badge }}</n-tag>
              </div>
            </div>
          </div>
        </div>
      </div>
      <n-empty
        v-else-if="keyword.trim() && !loading"
        description="未找到匹配资产"
        class="py-8"
      />
      <div v-else class="text-xs text-gray-400 text-center py-6">
        快捷键：随时按 <kbd class="px-1.5 py-0.5 bg-gray-100 rounded border">Ctrl + K</kbd> / <kbd class="px-1.5 py-0.5 bg-gray-100 rounded border">⌘ + K</kbd> 唤起全局搜索
      </div>
    </n-spin>
  </n-modal>

  <AssetDetailDrawer v-model:show="drawerOpen" :entity-type="drawerType" :entity-id="drawerId" />
</template>
