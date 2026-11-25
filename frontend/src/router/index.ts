import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('@/views/LoginView.vue'),
    meta: { public: true },
  },
  {
    path: '/change-password',
    name: 'change-password',
    component: () => import('@/views/ChangePasswordView.vue'),
    meta: { allowPasswordChange: true },
  },
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    redirect: '/dashboard',
    children: [
      {
        path: 'dashboard',
        name: 'dashboard',
        component: () => import('@/views/DashboardView.vue'),
        meta: { title: '总览看板' },
      },
      {
        path: 'clusters',
        name: 'clusters',
        component: () => import('@/views/assets/ClustersView.vue'),
        meta: { title: '集群管理' },
      },
      {
        path: 'devices',
        name: 'devices',
        component: () => import('@/views/assets/DevicesView.vue'),
        meta: { title: '设备管理' },
      },
      {
        path: 'nics',
        name: 'nics',
        component: () => import('@/views/assets/NicsView.vue'),
        meta: { title: '网卡台账' },
      },
      {
        path: 'subnets',
        name: 'subnets',
        component: () => import('@/views/assets/SubnetsView.vue'),
        meta: { title: '网段与 IPAM' },
      },
      {
        path: 'ips',
        name: 'ips',
        component: () => import('@/views/assets/IpsView.vue'),
        meta: { title: 'IP 地址台账' },
      },
      {
        path: 'services',
        name: 'services',
        component: () => import('@/views/assets/ServicesView.vue'),
        meta: { title: '服务管理' },
      },
      {
        path: 'urls',
        name: 'urls',
        component: () => import('@/views/assets/UrlsView.vue'),
        meta: { title: 'URL 入口' },
      },
      {
        path: 'import-export',
        name: 'import-export',
        component: () => import('@/views/batch/ImportExportView.vue'),
        meta: { title: 'Excel 导入导出' },
      },
      {
        path: 'groups',
        name: 'groups',
        component: () => import('@/views/admin/GroupsView.vue'),
        meta: { title: '运维分组' },
      },
      {
        path: 'users',
        name: 'users',
        component: () => import('@/views/admin/UsersView.vue'),
        meta: { title: '用户管理' },
      },
      {
        path: 'roles',
        name: 'roles',
        component: () => import('@/views/admin/RolesView.vue'),
        meta: { title: '角色与权限' },
      },
      {
        path: 'network-zones',
        name: 'network-zones',
        component: () => import('@/views/admin/NetworkZonesView.vue'),
        meta: { title: '网络域' },
      },
      {
        path: 'dicts',
        name: 'dicts',
        component: () => import('@/views/admin/DictsView.vue'),
        meta: { title: '数据字典' },
      },
      {
        path: 'tags',
        name: 'tags',
        component: () => import('@/views/admin/TagsView.vue'),
        meta: { title: '标签管理' },
      },
      {
        path: 'audit',
        name: 'audit',
        component: () => import('@/views/admin/AuditLogsView.vue'),
        meta: { title: '审计日志' },
      },
      {
        path: 'recycle-bin',
        name: 'recycle-bin',
        component: () => import('@/views/admin/RecycleBinView.vue'),
        meta: { title: '回收站' },
      },
      {
        path: 'tokens',
        name: 'tokens',
        component: () => import('@/views/profile/ApiTokensView.vue'),
        meta: { title: '个人 API Token' },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!auth.initialized) {
    await auth.fetchMe()
  }

  if (to.meta.public) {
    if (auth.user) {
      return auth.user.must_change_password ? '/change-password' : '/dashboard'
    }
    return true
  }

  if (!auth.user) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }

  if (auth.user.must_change_password && !to.meta.allowPasswordChange) {
    return '/change-password'
  }

  return true
})

export default router
