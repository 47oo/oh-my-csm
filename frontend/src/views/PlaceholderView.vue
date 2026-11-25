<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <n-card>
    <n-result
      status="info"
      :title="`${String(route.meta.title || '功能模块')} · 规划阶段 ${String(route.meta.milestone || 'M2')}`"
      :description="String(route.meta.desc || '该业务模块将在后续里程碑按设计文档落地。')"
    >
      <template #footer>
        <n-button type="primary" @click="$router.push('/dashboard')">返回总览看板</n-button>
      </template>
    </n-result>
  </n-card>
</template>
