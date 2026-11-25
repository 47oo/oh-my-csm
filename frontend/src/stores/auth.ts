import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY } from '@/api/client'
import { authApi, type CurrentUser } from '@/api/system'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<CurrentUser | null>(null)
  const initialized = ref(false)

  const isAuthenticated = computed(() => Boolean(localStorage.getItem(ACCESS_TOKEN_KEY)))
  const isSuperAdmin = computed(() => Boolean(user.value?.is_superadmin))

  function hasGlobalPerm(code: string): boolean {
    if (!user.value) return false
    if (user.value.is_superadmin) return true
    return user.value.global_permissions.includes(code)
  }

  function hasAnyPerm(code: string): boolean {
    if (hasGlobalPerm(code)) return true
    if (!user.value) return false
    if (Object.values(user.value.group_permissions || {}).some((perms) => perms.includes(code))) {
      return true
    }
    return Object.values(user.value.cluster_permissions || {}).some((perms) => perms.includes(code))
  }

  function canManageGroup(groupId: number): boolean {
    if (hasGlobalPerm('system:manage')) return true
    const perms = user.value?.group_permissions?.[groupId] || []
    return perms.includes('group:member_manage')
  }

  function canManageCluster(clusterId: number, groupId: number | null = null): boolean {
    if (hasGlobalPerm('system:manage')) return true
    if (groupId !== null && (user.value?.group_permissions?.[groupId] || []).includes('group:member_manage')) {
      return true
    }
    return (user.value?.cluster_permissions?.[clusterId] || []).includes('group:member_manage')
  }

  const hasWritableGroup = computed(() => {
    if (!user.value) return false
    if (user.value.is_superadmin || user.value.global_permissions.includes('asset:write')) return true
    return Object.values(user.value.group_permissions || {}).some((perms) => perms.includes('asset:write'))
  })

  const writableClusterIds = computed<number[]>(() => {
    if (!user.value) return []
    return Object.entries(user.value.cluster_permissions || {})
      .filter(([, perms]) => perms.includes('asset:write'))
      .map(([cid]) => Number(cid))
  })

  async function login(username: string, password: string) {
    const res = await authApi.login(username, password)
    localStorage.setItem(ACCESS_TOKEN_KEY, res.data.access_token)
    localStorage.setItem(REFRESH_TOKEN_KEY, res.data.refresh_token)
    await fetchMe()
    return res.data
  }

  async function fetchMe() {
    if (!localStorage.getItem(ACCESS_TOKEN_KEY)) {
      user.value = null
      initialized.value = true
      return null
    }
    try {
      const res = await authApi.me()
      user.value = res.data
    } catch {
      user.value = null
      localStorage.removeItem(ACCESS_TOKEN_KEY)
      localStorage.removeItem(REFRESH_TOKEN_KEY)
    } finally {
      initialized.value = true
    }
    return user.value
  }

  async function logout() {
    const refresh = localStorage.getItem(REFRESH_TOKEN_KEY)
    try {
      if (localStorage.getItem(ACCESS_TOKEN_KEY)) {
        await authApi.logout(refresh)
      }
    } catch {
      // Ignore logout network error and clear local tokens
    } finally {
      localStorage.removeItem(ACCESS_TOKEN_KEY)
      localStorage.removeItem(REFRESH_TOKEN_KEY)
      user.value = null
    }
  }

  return {
    user,
    initialized,
    isAuthenticated,
    isSuperAdmin,
    hasGlobalPerm,
    hasAnyPerm,
    canManageGroup,
    canManageCluster,
    hasWritableGroup,
    writableClusterIds,
    login,
    fetchMe,
    logout,
  }
})
