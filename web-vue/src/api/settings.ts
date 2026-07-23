import apiClient from './client'
import type { Settings, SettingsUpdateResponse } from '@/types/api'

export type RawSettings = Record<string, any>

export interface BackupTestResult {
  ok: boolean
  status?: number
  error?: string | null
}

export interface ImageStorageTestResult {
  ok: boolean
  status?: number
  error?: string | null
}

export interface ImageStorageSyncResult {
  uploaded: number
  skipped: number
  failed: number
}

export interface RetentionCleanupRequest {
  log_retention_hours?: number
  image_retention_hours?: number
}

export interface RetentionCleanupSection {
  removed: number
  kept?: number
  removed_size_bytes: number
  retention_hours: number
  dry_run: boolean
}

export interface RetentionCleanupResult {
  dry_run: boolean
  logs: RetentionCleanupSection
  images: RetentionCleanupSection
  total_removed: number
  total_size_bytes: number
}

export interface AccountCleanupRequest {
  auto_remove_invalid_accounts?: boolean
  auto_remove_rate_limited_accounts?: boolean
}

export interface AccountCleanupResult {
  dry_run: boolean
  invalid: number
  rate_limited: number
  total_removed: number
  auto_remove_invalid_accounts: boolean
  auto_remove_rate_limited_accounts: boolean
}

export interface BackupState {
  running?: boolean
  last_status?: string
  last_started_at?: string
  last_finished_at?: string
  last_object_key?: string
  last_error?: string
}

export interface BackupItem {
  key: string
  name?: string
  size?: number
  size_bytes?: number
  last_modified?: string
  encrypted?: boolean
}

export interface BackupRunResult {
  key: string
  size: number
  encrypted: boolean
}

export type ThirdPartyAppsSettings = Settings['third_party_apps']

function cloneJsonValue<T>(value: T): T {
  if (typeof value === 'undefined') return value
  return JSON.parse(JSON.stringify(value)) as T
}

function requireObject(value: unknown, name: string): RawSettings {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`Invalid ${name} response`)
  }
  return value as RawSettings
}

export function normalizeThirdPartyApps(raw: unknown): ThirdPartyAppsSettings {
  return cloneJsonValue(requireObject(raw, 'third-party apps')) as ThirdPartyAppsSettings
}

export function normalizeSettings(raw: RawSettings | null | undefined): Settings {
  return cloneJsonValue(requireObject(raw, 'settings')) as unknown as Settings
}

export function prepareSettingsForEdit(raw: RawSettings | Settings | null | undefined): Settings {
  return normalizeSettings(raw as RawSettings)
}

function toBackendSettings(settings: Settings): RawSettings {
  return cloneJsonValue(settings) as unknown as RawSettings
}

export function prepareSettingsForSave(settings: Settings): RawSettings {
  return toBackendSettings(settings)
}

export function prepareSettingsPatch(settings: Settings, baseline?: Settings | null): RawSettings {
  const next = toBackendSettings(settings)
  if (!baseline) return next
  const previous = toBackendSettings(baseline)
  return Object.fromEntries(
    Object.keys(next)
      .filter((key) => JSON.stringify(next[key]) !== JSON.stringify(previous[key]))
      .map((key) => [key, cloneJsonValue(next[key])]),
  )
}

export const settingsApi = {
  async get() {
    const response = await apiClient.get<never, { config: RawSettings }>('/api/settings')
    return normalizeSettings(response.config)
  },
  async getThirdPartyApps() {
    const response = await apiClient.get<never, { third_party_apps: RawSettings }>('/api/third-party-apps')
    return normalizeThirdPartyApps(response.third_party_apps)
  },

  async update(settings: Settings): Promise<SettingsUpdateResponse> {
    const response = await apiClient.post<RawSettings, { config: RawSettings }>('/api/settings', toBackendSettings(settings))
    return {
      status: 'ok',
      message: 'saved',
      config: normalizeSettings(response.config),
    }
  },

  async updatePartial(payload: RawSettings): Promise<SettingsUpdateResponse> {
    const response = await apiClient.post<RawSettings, { config: RawSettings }>('/api/settings', cloneJsonValue(payload))
    return {
      status: 'ok',
      message: 'saved',
      config: normalizeSettings(response.config),
    }
  },

  testBackup: () =>
    apiClient.post<Record<string, never>, { result: BackupTestResult }>('/api/backup/test', {}),

  listBackups: () =>
    apiClient.get<never, { items: BackupItem[]; state: BackupState; settings: RawSettings }>('/api/backups'),

  runBackup: () =>
    apiClient.post<Record<string, never>, { result: BackupRunResult }>('/api/backups/run', {}),

  deleteBackup: (key: string) =>
    apiClient.post<{ key: string }, { ok: boolean }>('/api/backups/delete', { key }),

  testImageStorage: () =>
    apiClient.post<Record<string, never>, { result: ImageStorageTestResult }>('/api/image-storage/test', {}),

  syncImageStorage: () =>
    apiClient.post<Record<string, never>, { result: ImageStorageSyncResult }>('/api/image-storage/sync', {}),

  previewRetentionCleanup: (payload: RetentionCleanupRequest = {}) =>
    apiClient.post<RetentionCleanupRequest, RetentionCleanupResult>('/api/settings/retention-cleanup/preview', payload),

  runRetentionCleanup: (payload: RetentionCleanupRequest = {}) =>
    apiClient.post<RetentionCleanupRequest, RetentionCleanupResult>('/api/settings/retention-cleanup/run', payload),

  previewAccountCleanup: (payload: AccountCleanupRequest = {}) =>
    apiClient.post<AccountCleanupRequest, AccountCleanupResult>('/api/settings/account-cleanup/preview', payload),

  runAccountCleanup: (payload: AccountCleanupRequest = {}) =>
    apiClient.post<AccountCleanupRequest, AccountCleanupResult>('/api/settings/account-cleanup/run', payload),
}
