import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  dictApi,
  groupApi,
  tagApi,
  zoneApi,
  type DictItem,
  type NetworkZoneItem,
  type OpsGroupItem,
  type TagItem,
} from '@/api/system'

export const useOptionsStore = defineStore('options', () => {
  const dicts = ref<DictItem[]>([])
  const groups = ref<OpsGroupItem[]>([])
  const zones = ref<NetworkZoneItem[]>([])
  const tags = ref<TagItem[]>([])
  const loaded = ref(false)

  async function ensureLoaded(force = false) {
    if (loaded.value && !force) return
    const [dRes, gRes, zRes, tRes] = await Promise.all([
      dictApi.list({ only_enabled: true }),
      groupApi.list(),
      zoneApi.list(),
      tagApi.list(),
    ])
    dicts.value = dRes.data
    groups.value = gRes.data
    zones.value = zRes.data
    tags.value = tRes.data
    loaded.value = true
  }

  function dictOptions(category: string) {
    return dicts.value
      .filter((d) => d.category === category)
      .map((d) => ({ label: d.label, value: d.code }))
  }

  function dictLabel(category: string, code: string | null | undefined): string {
    if (!code) return '—'
    return dicts.value.find((d) => d.category === category && d.code === code)?.label || code
  }

  function dictColor(category: string, code: string | null | undefined): string | undefined {
    if (!code) return undefined
    return dicts.value.find((d) => d.category === category && d.code === code)?.color || undefined
  }

  return {
    dicts,
    groups,
    zones,
    tags,
    loaded,
    ensureLoaded,
    dictOptions,
    dictLabel,
    dictColor,
  }
})
