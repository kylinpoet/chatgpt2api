import apiClient from './client'
import type { DashboardResponse } from '@/types/api'

export const statsApi = {
  async overview(timeRange: string = '24h') {
    return apiClient.get<never, DashboardResponse>('/api/dashboard', {
      params: {
        time_range: timeRange,
      },
    })
  },
}
