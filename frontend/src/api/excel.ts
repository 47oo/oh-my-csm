import api from './client'

export type ImportMode = 'multi_sheet' | 'wide_table'
export type ConflictStrategy = 'skip' | 'merge_non_empty' | 'overwrite'
export type ErrorMode = 'all_or_nothing' | 'skip_errors'

export interface UploadedSheetInfo {
  name: string
  headers: string[]
  row_count: number
  sample_rows: Array<Record<string, string>>
}

export interface WideFieldMeta {
  key: string
  label: string
  required_hint: string | null
}

export interface ExcelUploadResponse {
  upload_id: string
  filename: string
  detected_mode: ImportMode
  sheets: UploadedSheetInfo[]
  wide_fields: WideFieldMeta[]
  suggested_mapping: Record<string, string>
}

export interface ExcelRowResult {
  sheet: string
  row_num: number
  entity_type: string
  natural_key: string
  operation: 'create' | 'update' | 'skip' | 'error'
  reason: string | null
}

export interface ExcelPreviewResponse {
  upload_id: string
  mode: ImportMode
  total_rows: number
  create_count: number
  update_count: number
  skip_count: number
  error_count: number
  can_commit: boolean
  rows: ExcelRowResult[]
  errors: ExcelRowResult[]
}

export interface ExcelCommitResponse {
  batch_id: number
  status: string
  total_rows: number
  created_count: number
  updated_count: number
  skipped_count: number
  failed_count: number
  errors: ExcelRowResult[]
}

export interface ImportBatchItem {
  id: number
  user_id: number | null
  filename: string
  mode: string
  conflict_strategy: string
  error_mode: string
  total_rows: number
  created_count: number
  updated_count: number
  skipped_count: number
  failed_count: number
  status: string
  created_at: string
}

export function triggerBlobDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

export const excelApi = {
  downloadTemplate: async () => {
    const res = await api.get('/excel/template', { responseType: 'blob' })
    triggerBlobDownload(res.data, 'oh-my-csm-标准导入模板.xlsx')
  },
  upload: (file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post<ExcelUploadResponse>('/excel/upload', formData)
  },
  preview: (payload: {
    upload_id: string
    mode: ImportMode
    conflict_strategy: ConflictStrategy
    error_mode: ErrorMode
    default_group_id: number | null
    sheet_name?: string | null
    column_mapping: Record<string, string>
    store_unmapped_to_extra: boolean
  }) => api.post<ExcelPreviewResponse>('/excel/preview', payload),
  commit: (payload: {
    upload_id: string
    mode: ImportMode
    conflict_strategy: ConflictStrategy
    error_mode: ErrorMode
    default_group_id: number | null
    sheet_name?: string | null
    column_mapping: Record<string, string>
    store_unmapped_to_extra: boolean
  }) => api.post<ExcelCommitResponse>('/excel/commit', payload),
  listBatches: () => api.get<ImportBatchItem[]>('/excel/batches'),
  downloadBatchErrors: async (batchId: number) => {
    const res = await api.get(`/excel/batches/${batchId}/errors`, { responseType: 'blob' })
    triggerBlobDownload(res.data, `import-batch-${batchId}-错误清单.xlsx`)
  },
  exportWorkbook: async (payload: { mode: ImportMode; group_id: number | null; env: string | null }) => {
    const res = await api.post('/excel/export', payload, { responseType: 'blob' })
    const fname = payload.mode === 'multi_sheet' ? 'oh-my-csm-全量台账-6Sheet.xlsx' : 'oh-my-csm-台账宽表.xlsx'
    triggerBlobDownload(res.data, fname)
  },
}
