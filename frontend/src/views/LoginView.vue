<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { extractErrorMessage } from '@/api/client'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()
const message = useMessage()

const loading = ref(false)
const form = reactive({
  username: '',
  password: '',
})

async function handleSubmit() {
  if (!form.username.trim() || !form.password) {
    message.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    const res = await auth.login(form.username.trim(), form.password)
    if (res.must_change_password) {
      message.warning('首次登录，请先修改初始密码')
      router.push('/change-password')
      return
    }
    message.success('登录成功')
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/dashboard'
    router.push(redirect)
  } catch (err) {
    message.error(extractErrorMessage(err, '登录失败'))
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-100 via-blue-50 to-indigo-100 p-4">
    <n-card class="w-full max-w-420px shadow-lg" :bordered="false">
      <div class="text-center mb-6">
        <div class="text-2xl font-bold text-blue-600 tracking-tight">Oh-My-CSM</div>
        <div class="text-sm text-gray-500 mt-1">运维资产与网络拓扑台账管理系统</div>
      </div>

      <n-form @submit.prevent="handleSubmit">
        <n-form-item label="用户名">
          <n-input
            v-model:value="form.username"
            placeholder="请输入用户名（初始账号 admin）"
            autocomplete="username"
          />
        </n-form-item>
        <n-form-item label="密码">
          <n-input
            v-model:value="form.password"
            type="password"
            show-password-on="click"
            placeholder="请输入密码"
            autocomplete="current-password"
            @keyup.enter="handleSubmit"
          />
        </n-form-item>
        <n-button type="primary" block :loading="loading" @click="handleSubmit">
          登 录
        </n-button>
      </n-form>

      <div class="mt-4 text-xs text-gray-400 text-center">
        仅限内部授权账号登录 · 连续 5 次密码错误将锁定 15 分钟
      </div>
    </n-card>
  </div>
</template>
