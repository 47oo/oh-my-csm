<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useMessage } from 'naive-ui'
import { authApi } from '@/api/system'
import { useAuthStore } from '@/stores/auth'
import { extractErrorMessage } from '@/api/client'

const router = useRouter()
const auth = useAuthStore()
const message = useMessage()
const loading = ref(false)

const form = reactive({
  old_password: '',
  new_password: '',
  confirm_password: '',
})

async function handleSubmit() {
  if (!form.old_password || !form.new_password) {
    message.warning('请填写完整密码信息')
    return
  }
  if (form.new_password !== form.confirm_password) {
    message.error('两次输入的新密码不一致')
    return
  }
  loading.value = true
  try {
    await authApi.changePassword(form.old_password, form.new_password)
    await auth.fetchMe()
    message.success('密码修改成功')
    router.push('/dashboard')
  } catch (err) {
    message.error(extractErrorMessage(err, '密码修改失败'))
  } finally {
    loading.value = false
  }
}

async function handleLogout() {
  await auth.logout()
  router.push('/login')
}
</script>

<template>
  <div class="min-h-screen flex items-center justify-center bg-[#f5f7fa] p-4">
    <n-card class="w-full max-w-440px" title="修改密码">
      <n-alert v-if="auth.user?.must_change_password" type="warning" class="mb-4">
        当前账号处于首次登录或密码重置状态，必须先修改密码才能进入系统。
      </n-alert>

      <n-form>
        <n-form-item label="当前密码">
          <n-input v-model:value="form.old_password" type="password" show-password-on="click" />
        </n-form-item>
        <n-form-item label="新密码（至少 8 位，需含字母与数字）">
          <n-input v-model:value="form.new_password" type="password" show-password-on="click" />
        </n-form-item>
        <n-form-item label="确认新密码">
          <n-input
            v-model:value="form.confirm_password"
            type="password"
            show-password-on="click"
            @keyup.enter="handleSubmit"
          />
        </n-form-item>
        <div class="flex gap-3">
          <n-button type="primary" class="flex-1" :loading="loading" @click="handleSubmit">
            确认修改
          </n-button>
          <n-button v-if="!auth.user?.must_change_password" @click="router.push('/dashboard')">
            返回
          </n-button>
          <n-button v-else quaternary @click="handleLogout">退出登录</n-button>
        </div>
      </n-form>
    </n-card>
  </div>
</template>
