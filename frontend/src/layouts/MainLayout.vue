<script setup lang="ts">
import { computed, h, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NIcon, type MenuOption, useMessage } from 'naive-ui'
import {
  SpeedometerOutline,
  ServerOutline,
  HardwareChipOutline,
  GitNetworkOutline,
  GlobeOutline,
  LayersOutline,
  AppsOutline,
  LinkOutline,
  CloudUploadOutline,
  PeopleOutline,
  PersonOutline,
  ShieldCheckmarkOutline,
  EarthOutline,
  BookOutline,
  PricetagsOutline,
  DocumentTextOutline,
  TrashOutline,
  KeyOutline,
  SearchOutline,
  LogOutOutline,
} from '@vicons/ionicons5'
import { useAuthStore } from '@/stores/auth'
import GlobalSearchModal from '@/components/GlobalSearchModal.vue'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const message = useMessage()
const collapsed = ref(false)
const searchOpen = ref(false)

function handleKeydown(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    searchOpen.value = !searchOpen.value
  }
}

onMounted(() => window.addEventListener('keydown', handleKeydown))
onUnmounted(() => window.removeEventListener('keydown', handleKeydown))

function renderIcon(icon: unknown) {
  return () => h(NIcon, null, { default: () => h(icon as never) })
}

const menuOptions = computed<MenuOption[]>(() => {
  const canSystemManage = auth.hasGlobalPerm('system:manage')
  const canSeeUsers = canSystemManage || auth.hasAnyPerm('group:member_manage')
  const canSeeRecycle = auth.isSuperAdmin || auth.hasAnyPerm('recycle_bin:restore')

  const govChildren: MenuOption[] = [
    { label: '运维分组', key: '/groups', icon: renderIcon(PeopleOutline) },
  ]
  if (canSeeUsers) {
    govChildren.push({ label: '用户管理', key: '/users', icon: renderIcon(PersonOutline) })
  }
  if (canSystemManage) {
    govChildren.push(
      { label: '角色与权限', key: '/roles', icon: renderIcon(ShieldCheckmarkOutline) },
      { label: '网络域', key: '/network-zones', icon: renderIcon(EarthOutline) },
      { label: '数据字典', key: '/dicts', icon: renderIcon(BookOutline) },
    )
  }
  govChildren.push(
    { label: '标签管理', key: '/tags', icon: renderIcon(PricetagsOutline) },
    { label: '审计日志', key: '/audit', icon: renderIcon(DocumentTextOutline) },
  )
  if (canSeeRecycle) {
    govChildren.push({ label: '回收站', key: '/recycle-bin', icon: renderIcon(TrashOutline) })
  }
  govChildren.push({ label: '个人 API Token', key: '/tokens', icon: renderIcon(KeyOutline) })

  return [
    { label: '总览看板', key: '/dashboard', icon: renderIcon(SpeedometerOutline) },
    {
      type: 'group',
      label: '资产与网络拓扑',
      key: 'grp-assets',
      children: [
        { label: '集群管理', key: '/clusters', icon: renderIcon(LayersOutline) },
        { label: '设备管理', key: '/devices', icon: renderIcon(ServerOutline) },
        { label: '网卡台账', key: '/nics', icon: renderIcon(HardwareChipOutline) },
        { label: '网段与 IPAM', key: '/subnets', icon: renderIcon(GitNetworkOutline) },
        { label: 'IP 地址台账', key: '/ips', icon: renderIcon(GlobeOutline) },
        { label: '服务管理', key: '/services', icon: renderIcon(AppsOutline) },
        { label: 'URL 入口', key: '/urls', icon: renderIcon(LinkOutline) },
      ],
    },
    {
      type: 'group',
      label: 'Excel 批处理',
      key: 'grp-batch',
      children: [
        { label: 'Excel 导入导出', key: '/import-export', icon: renderIcon(CloudUploadOutline) },
      ],
    },
    {
      type: 'group',
      label: '系统与权限治理',
      key: 'grp-gov',
      children: govChildren,
    },
  ]
})

const userDropdownOptions = [
  { label: '个人 API Token', key: 'tokens', icon: renderIcon(KeyOutline) },
  { label: '修改密码', key: 'change-password', icon: renderIcon(ShieldCheckmarkOutline) },
  { type: 'divider', key: 'd1' },
  { label: '退出登录', key: 'logout', icon: renderIcon(LogOutOutline) },
]

function handleMenuSelect(key: string) {
  router.push(key)
}

async function handleUserAction(key: string) {
  if (key === 'tokens') {
    router.push('/tokens')
  } else if (key === 'change-password') {
    router.push('/change-password')
  } else if (key === 'logout') {
    await auth.logout()
    message.success('已退出登录')
    router.push('/login')
  }
}
</script>

<template>
  <n-layout has-sider class="h-screen">
    <n-layout-sider
      bordered
      collapse-mode="width"
      :collapsed-width="64"
      :width="230"
      :collapsed="collapsed"
      show-trigger
      @collapse="collapsed = true"
      @expand="collapsed = false"
    >
      <div class="h-14 flex items-center justify-center border-b border-gray-200 px-3 font-bold text-base text-blue-600 select-none">
        <span v-if="!collapsed">Oh-My-CSM</span>
        <span v-else>CSM</span>
      </div>
      <n-menu
        :collapsed="collapsed"
        :collapsed-width="64"
        :collapsed-icon-size="20"
        :options="menuOptions"
        :value="route.path"
        @update:value="handleMenuSelect"
      />
    </n-layout-sider>

    <n-layout>
      <n-layout-header bordered class="h-14 px-5 flex items-center justify-between bg-white">
        <div class="flex items-center gap-3">
          <span class="text-base font-semibold text-gray-800">{{ route.meta.title || 'Oh-My-CSM' }}</span>
          <n-button size="small" secondary type="primary" @click="searchOpen = true">
            <template #icon><n-icon :component="SearchOutline" /></template>
            全局秒搜 (Ctrl+K)
          </n-button>
        </div>

        <div class="flex items-center gap-3">
          <n-tag v-if="auth.isSuperAdmin" type="error" size="small" round>超级管理员</n-tag>
          <n-dropdown :options="userDropdownOptions" @select="handleUserAction">
            <n-button quaternary size="small">
              {{ auth.user?.display_name }} ({{ auth.user?.username }})
            </n-button>
          </n-dropdown>
        </div>
      </n-layout-header>

      <n-layout-content content-style="padding: 20px; min-height: calc(100vh - 56px);" class="bg-[#f5f7fa]">
        <router-view />
      </n-layout-content>
    </n-layout>

    <GlobalSearchModal v-model:show="searchOpen" />
  </n-layout>
</template>
