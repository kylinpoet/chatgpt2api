import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { statsApi } from '@/api/stats'
import { usePageQuery, useSerialVisibilityPolling } from '@/composables/usePageQuery'
import { usePageRuntime } from '@/composables/usePageRuntime'
import {
  chartColors,
  createLineSeries,
  createPieDataItem,
  getLineChartTheme,
  getModelColor,
  getPieChartTheme,
} from '@/lib/chartTheme'
import { DEFAULT_DASHBOARD_TIME_RANGE, type DashboardTimeRange } from '@/lib/timeRanges'
import type { DashboardPerformanceRow, DashboardResponse } from '@/types/api'

type ChartInstance = {
  setOption: (
    option: unknown,
    opts?: boolean | { notMerge?: boolean; lazyUpdate?: boolean; replaceMerge?: string[] },
  ) => void
  resize: () => void
  dispose: () => void
}

type ChartKey = 'modelTrend' | 'callTrend' | 'successRate' | 'duration' | 'modelShare' | 'modelRank'
type RenderMode = 'initial' | 'refresh'

const DASHBOARD_REFRESH_INTERVAL_MS = 5_000
const DASHBOARD_POLL_TIMER_KEY = 'dashboard:poll'

function formatInteger(value: unknown) {
  const number = Number(value || 0)
  return Number.isFinite(number) ? Math.max(0, Math.trunc(number)).toLocaleString('zh-CN') : '0'
}

function formatPercent(value: unknown) {
  const number = Number(value || 0)
  return `${Number.isFinite(number) ? number.toFixed(1) : '0.0'}%`
}

function formatDuration(value: unknown) {
  const milliseconds = Math.max(0, Number(value || 0))
  if (!Number.isFinite(milliseconds) || milliseconds <= 0) return '-'
  if (milliseconds >= 60_000) {
    const totalSeconds = Math.round(milliseconds / 1_000)
    const minutes = Math.floor(totalSeconds / 60)
    const seconds = totalSeconds % 60
    return seconds > 0 ? `${minutes}m ${seconds}s` : `${minutes}m`
  }
  if (milliseconds >= 1_000) return `${(milliseconds / 1_000).toFixed(1)}s`
  return `${Math.round(milliseconds)}ms`
}

function tooltipHeading(value: unknown) {
  return `<div style="font-weight:600;margin-bottom:4px">${String(value || '')}</div>`
}

function tooltipValue(value: unknown) {
  return `<span style="font-weight:600">${String(value)}</span>`
}

function tooltipRichRow(label: string, valueHtml: string, marker = '') {
  return `<span style="white-space:nowrap">${marker}${label}</span><span style="text-align:right;white-space:nowrap">${valueHtml}</span>`
}

function tooltipRow(label: string, value: unknown, marker = '') {
  return tooltipRichRow(label, tooltipValue(value), marker)
}

function tooltipSummaryRow(label: string, valueHtml: string) {
  return `<span style="grid-column:1/-1;height:1px;margin:5px 0;background:rgba(148,163,184,.35)"></span>${tooltipRichRow(label, valueHtml)}`
}

function tooltipRows(rows: string[]) {
  return `<div style="display:grid;grid-template-columns:max-content max-content;column-gap:12px;row-gap:2px;align-items:center">${rows.join('')}</div>`
}

function positivePerformanceRows(rows: DashboardPerformanceRow[] | undefined) {
  return (rows || []).filter(row => Number(row.successful_calls || 0) > 0)
}

function dashboardContentSignature(value: DashboardResponse | null) {
  if (!value) return ''
  return JSON.stringify({
    time_range: value.time_range,
    accounts: value.accounts,
    metrics: value.metrics,
  })
}

export function useDashboardPage() {
  const pageRuntime = usePageRuntime('dashboard')
  const dashboardQuery = usePageQuery({
    runtime: pageRuntime,
    key: 'dashboard:data',
    errorMessage: '概览加载失败',
  })
  const modelTrendRange = ref<DashboardTimeRange>(DEFAULT_DASHBOARD_TIME_RANGE)
  const callTrendRange = ref<DashboardTimeRange>(DEFAULT_DASHBOARD_TIME_RANGE)
  const successRateRange = ref<DashboardTimeRange>(DEFAULT_DASHBOARD_TIME_RANGE)
  const durationRange = ref<DashboardTimeRange>(DEFAULT_DASHBOARD_TIME_RANGE)
  const modelShareRange = ref<DashboardTimeRange>(DEFAULT_DASHBOARD_TIME_RANGE)
  const modelRankRange = ref<DashboardTimeRange>(DEFAULT_DASHBOARD_TIME_RANGE)
  const snapshot = ref<DashboardResponse | null>(null)
  const snapshots = ref<Partial<Record<DashboardTimeRange, DashboardResponse>>>({})
  let requestCount = 0

  function selectedRanges() {
    return Array.from(new Set<DashboardTimeRange>([
      modelTrendRange.value,
      callTrendRange.value,
      successRateRange.value,
      durationRange.value,
      modelShareRange.value,
      modelRankRange.value,
    ]))
  }

  function snapshotFor(timeRange: DashboardTimeRange) {
    return snapshots.value[timeRange]
  }

  const modelTrendChartRef = ref<HTMLDivElement | null>(null)
  const callTrendChartRef = ref<HTMLDivElement | null>(null)
  const successRateChartRef = ref<HTMLDivElement | null>(null)
  const durationChartRef = ref<HTMLDivElement | null>(null)
  const modelShareChartRef = ref<HTMLDivElement | null>(null)
  const modelRankChartRef = ref<HTMLDivElement | null>(null)
  const charts: Record<ChartKey, ChartInstance | null> = {
    modelTrend: null,
    callTrend: null,
    successRate: null,
    duration: null,
    modelShare: null,
    modelRank: null,
  }
  let chartsReady = false
  let modelShareMobile: boolean | null = null

  const dashboardPolling = useSerialVisibilityPolling({
    runtime: pageRuntime,
    key: DASHBOARD_POLL_TIMER_KEY,
    intervalMs: DASHBOARD_REFRESH_INTERVAL_MS,
    action: async () => {
      await loadDashboard({ silent: true, source: 'auto' })
    },
  })

  const dashboardDataReady = computed(() => snapshot.value !== null)
  const isLoading = computed(() => dashboardQuery.loading.value)
  const errorMessage = computed(() => dashboardQuery.error.value)
  const stats = computed(() => {
    const accounts = snapshot.value?.accounts
    return [
      {
        label: '账号总数',
        value: formatInteger(accounts?.total),
        icon: 'lucide:users',
        iconBg: 'bg-sky-100',
        iconColor: 'text-sky-600',
      },
      {
        label: '可用账号',
        value: formatInteger(accounts?.active),
        icon: 'lucide:circle-check',
        iconBg: 'bg-emerald-100',
        iconColor: 'text-emerald-600',
      },
      {
        label: '限流账号',
        value: formatInteger(accounts?.limited),
        icon: 'lucide:clock-3',
        iconBg: 'bg-amber-100',
        iconColor: 'text-amber-600',
      },
      {
        label: '异常账号',
        value: formatInteger(accounts?.abnormal),
        icon: 'lucide:circle-alert',
        iconBg: 'bg-rose-100',
        iconColor: 'text-rose-600',
      },
      {
        label: '禁用账号',
        value: formatInteger(accounts?.disabled),
        icon: 'lucide:ban',
        iconBg: 'bg-slate-100',
        iconColor: 'text-slate-600',
      },
      {
        label: '剩余额度',
        value: formatInteger(accounts?.total_quota),
        icon: 'lucide:coins',
        iconBg: 'bg-cyan-100',
        iconColor: 'text-cyan-600',
      },
    ]
  })

  const modelSharePerformance = computed(() => positivePerformanceRows(
    snapshotFor(modelShareRange.value)?.metrics.model_performance,
  ))
  const modelRankPerformance = computed(() => positivePerformanceRows(
    snapshotFor(modelRankRange.value)?.metrics.model_performance,
  ))

  function modelTrendSeries() {
    const trend = snapshotFor(modelTrendRange.value)?.metrics.trend
    const source = trend?.model_calls || {}
    const bucketCount = trend?.labels.length || 0
    const ranked = Object.entries(source)
      .map(([name, values]) => ({
        name,
        values: Array.from({ length: bucketCount }, (_, index) => Math.max(0, Number(values[index] || 0))),
      }))
      .filter(item => item.values.some(value => value > 0))
      .sort((left, right) => {
        const totalDifference = right.values.reduce((sum, value) => sum + value, 0)
          - left.values.reduce((sum, value) => sum + value, 0)
        return totalDifference || left.name.localeCompare(right.name)
      })
    const visible = ranked.slice(0, 6)
    const hidden = ranked.slice(6)
    if (hidden.length) {
      visible.push({
        name: '其它模型',
        values: Array.from({ length: bucketCount }, (_, index) => (
          hidden.reduce((sum, item) => sum + Number(item.values[index] || 0), 0)
        )),
      })
    }
    return visible
  }

  function setChartOption(key: ChartKey, option: Record<string, unknown>, mode: RenderMode) {
    charts[key]?.setOption({
      ...option,
      animation: true,
      animationDuration: mode === 'initial' ? 650 : 220,
      animationDurationUpdate: 220,
      animationEasing: 'cubicOut',
    }, {
      notMerge: true,
      lazyUpdate: mode === 'refresh',
      replaceMerge: ['series', 'xAxis', 'yAxis', 'legend'],
    })
  }

  function updateModelTrendChart(mode: RenderMode = 'refresh') {
    const trend = snapshotFor(modelTrendRange.value)?.metrics.trend
    if (!trend || !charts.modelTrend) return
    const theme = getLineChartTheme()
    const models = modelTrendSeries()
    setChartOption('modelTrend', {
      ...theme,
      color: models.map(item => item.name === '其它模型' ? chartColors.slate : getModelColor(item.name)),
      tooltip: {
        ...theme.tooltip,
        trigger: 'axis',
        formatter: (params: Array<{
          axisValue: string
          marker: string
          seriesName: string
          value: number
        }> | undefined) => {
          if (!params?.length) return ''
          const rows = params.filter(item => Number(item.value || 0) > 0)
          const total = rows.reduce((sum, item) => sum + Number(item.value || 0), 0)
          return tooltipHeading(params[0].axisValue) + tooltipRows([
            ...rows.map(item => tooltipRow(item.seriesName, formatInteger(item.value), item.marker)),
            tooltipSummaryRow('总调用', tooltipValue(formatInteger(total))),
          ])
        },
      },
      legend: {
        ...theme.legend,
        data: models.map(item => item.name),
        type: 'scroll',
        top: 0,
        right: 0,
      },
      grid: {
        ...theme.grid,
        top: models.length > 4 ? 58 : 48,
      },
      xAxis: { ...theme.xAxis, data: trend.labels, boundaryGap: true },
      yAxis: { ...theme.yAxis, minInterval: 1 },
      series: models.map(item => ({
        name: item.name,
        type: 'bar',
        stack: 'calls',
        data: item.values,
        barWidth: modelTrendRange.value === '7d' ? '68%' : undefined,
        barMaxWidth: modelTrendRange.value === '7d' ? 140 : 30,
        itemStyle: {
          color: item.name === '其它模型' ? chartColors.slate : getModelColor(item.name),
          borderRadius: [3, 3, 0, 0],
        },
        emphasis: { focus: 'none' },
      })),
    }, mode)
  }

  function updateCallTrendChart(mode: RenderMode = 'refresh') {
    const trend = snapshotFor(callTrendRange.value)?.metrics.trend
    if (!trend || !charts.callTrend) return
    const theme = getLineChartTheme()
    setChartOption('callTrend', {
      ...theme,
      tooltip: {
        ...theme.tooltip,
        formatter: (params: Array<{
          axisValue: string
          marker: string
          seriesName: string
          value: number
        }> | undefined) => {
          if (!params?.length) return ''
          return tooltipHeading(params[0].axisValue) + tooltipRows(
            params.map(item => tooltipRow(item.seriesName, formatInteger(item.value), item.marker)),
          )
        },
      },
      xAxis: { ...theme.xAxis, data: trend.labels },
      yAxis: { ...theme.yAxis, minInterval: 1 },
      series: [
        createLineSeries('成功', trend.successful_calls, chartColors.success, {
          areaOpacity: 0.18,
          lineWidth: 3,
        }),
        createLineSeries('失败', trend.failed_calls, chartColors.danger, {
          areaOpacity: 0.08,
          lineWidth: 2,
        }),
        createLineSeries('切号', trend.account_switches, chartColors.warning, {
          areaOpacity: 0,
          lineWidth: 2,
        }),
      ],
    }, mode)
  }

  function updateSuccessRateChart(mode: RenderMode = 'refresh') {
    const trend = snapshotFor(successRateRange.value)?.metrics.trend
    if (!trend || !charts.successRate) return
    const theme = getLineChartTheme()
    const finalRates = trend.success_rate.map((value, index) => (
      Number(trend.successful_calls[index] || 0) + Number(trend.failed_calls[index] || 0) > 0
        ? Number(value || 0)
        : null
    ))
    const recoveryRates = trend.account_switch_recovery_rate.map((value, index) => (
      Number(trend.account_switch_requests[index] || 0) > 0 ? Number(value || 0) : null
    ))
    const measuredRates = [...finalRates, ...recoveryRates]
      .filter((value): value is number => value !== null)
    const minimumRate = measuredRates.length ? Math.min(...measuredRates) : 0
    const axisMinimum = Math.max(0, Math.floor((minimumRate - 10) / 10) * 10)
    setChartOption('successRate', {
      ...theme,
      tooltip: {
        ...theme.tooltip,
        formatter: (params: Array<{
          dataIndex: number
          marker: string
          seriesName: string
        }> | undefined) => {
          const index = Number(params?.[0]?.dataIndex ?? -1)
          if (index < 0) return ''
          const finalRateMarker = params?.find(item => item.seriesName === '最终成功率')?.marker || ''
          const recoveryRateMarker = params?.find(item => item.seriesName === '切号恢复率')?.marker || ''
          const successful = Number(trend.successful_calls[index] || 0)
          const failed = Number(trend.failed_calls[index] || 0)
          const switchedRequests = Number(trend.account_switch_requests[index] || 0)
          const switches = Number(trend.account_switches[index] || 0)
          const recovered = Number(trend.account_switch_recovered[index] || 0)
          const finalRate = successful + failed > 0 ? formatPercent(trend.success_rate[index]) : '-'
          const lines = [
            tooltipRow('最终成功率', finalRate, finalRateMarker),
          ]
          if (switchedRequests > 0) {
            lines.push(tooltipRow('切号恢复率', formatPercent(trend.account_switch_recovery_rate[index]), recoveryRateMarker))
          }
          lines.push(tooltipSummaryRow('成功 / 失败', tooltipValue(`${formatInteger(successful)} / ${formatInteger(failed)}`)))
          if (switchedRequests > 0) {
            lines.push(tooltipRow('切号 / 恢复请求', `${formatInteger(switches)} / ${formatInteger(recovered)}`))
          }
          return tooltipHeading(trend.labels[index]) + tooltipRows(lines)
        },
      },
      xAxis: { ...theme.xAxis, data: trend.labels },
      yAxis: {
        ...theme.yAxis,
        min: axisMinimum,
        max: 100,
        axisLabel: { ...theme.yAxis.axisLabel, formatter: '{value}%' },
      },
      series: [
        createLineSeries('最终成功率', finalRates, chartColors.success, {
          areaOpacity: 0.18,
          lineWidth: 3,
        }),
        ...(recoveryRates.some(value => value !== null)
          ? [createLineSeries('切号恢复率', recoveryRates, chartColors.warning, {
              areaOpacity: 0,
              lineWidth: 2,
              showSymbol: true,
              symbol: 'diamond',
              symbolSize: 8,
              zIndex: 3,
              lineStyle: { type: 'dashed', width: 2 },
            })]
          : []),
      ],
    }, mode)
  }

  function updateDurationChart(mode: RenderMode = 'refresh') {
    const trend = snapshotFor(durationRange.value)?.metrics.trend
    if (!trend || !charts.duration) return
    const theme = getLineChartTheme()
    const source = trend.model_average_success_duration_ms || {}
    const models = Object.entries(source)
      .filter(([, values]) => values.some(value => value !== null))
      .sort(([left], [right]) => {
        const leftCalls = (trend.model_calls[left] || []).reduce((sum, value) => sum + Number(value || 0), 0)
        const rightCalls = (trend.model_calls[right] || []).reduce((sum, value) => sum + Number(value || 0), 0)
        return rightCalls - leftCalls || left.localeCompare(right)
      })
      .slice(0, 6)
    const measuredDurations = models.flatMap(([, values]) => (
      values.filter((value): value is number => value !== null)
    ))
    const useMinutes = Math.max(0, ...measuredDurations) >= 60_000
    const divisor = useMinutes ? 60_000 : 1_000
    const unit = useMinutes ? 'm' : 's'
    setChartOption('duration', {
      ...theme,
      color: models.map(([model]) => getModelColor(model)),
      tooltip: {
        ...theme.tooltip,
        formatter: (params: Array<{
          dataIndex: number
          marker: string
          seriesName: string
        }> | undefined) => {
          const index = Number(params?.[0]?.dataIndex ?? -1)
          if (index < 0) return ''
          const rows = (params || []).map(item => tooltipRow(
            item.seriesName,
            formatDuration(source[item.seriesName]?.[index]),
            item.marker,
          ))
          return tooltipHeading(trend.labels[index]) + tooltipRows(rows)
        },
      },
      legend: {
        ...theme.legend,
        data: models.map(([model]) => model),
        type: 'scroll',
        top: 0,
        right: 0,
      },
      grid: {
        ...theme.grid,
        top: models.length > 4 ? 58 : 48,
      },
      xAxis: { ...theme.xAxis, data: trend.labels },
      yAxis: {
        ...theme.yAxis,
        axisLabel: { ...theme.yAxis.axisLabel, formatter: `{value}${unit}` },
      },
      series: models.map(([model, values]) => createLineSeries(
        model,
        values.map(value => value === null ? null : Number((value / divisor).toFixed(1))),
        getModelColor(model),
        { areaOpacity: 0.18, lineWidth: 2 },
      )),
    }, mode)
  }

  function updateModelShareChart(mode: RenderMode = 'refresh') {
    if (!charts.modelShare) return
    const isMobile = window.innerWidth < 768
    modelShareMobile = isMobile
    const theme = getPieChartTheme(isMobile)
    const data = modelSharePerformance.value.map(row => (
      createPieDataItem(row.name, row.successful_calls, getModelColor(row.name))
    ))
    setChartOption('modelShare', {
      ...theme,
      color: modelSharePerformance.value.map(row => getModelColor(row.name)),
      tooltip: {
        ...theme.tooltip,
        formatter: (params: { marker: string; name: string; value: number; percent: number }) => (
          tooltipHeading(params.name) + tooltipRows([
            tooltipRow('成功调用', `${formatInteger(params.value)} 次`, params.marker || ''),
            tooltipRow('成功占比', `${params.percent.toFixed(1)}%`),
          ])
        ),
      },
      legend: {
        ...theme.legend,
        data: modelSharePerformance.value.map(row => row.name),
      },
      series: [{
        ...theme.series,
        data,
        label: {
          ...theme.series.label,
          formatter: '{d}%',
        },
      }],
    }, mode)
  }

  function updateModelRankChart(mode: RenderMode = 'refresh') {
    if (!charts.modelRank) return
    const theme = getLineChartTheme()
    const rows = [...modelRankPerformance.value]
      .sort((left, right) => left.successful_calls - right.successful_calls)
      .slice(-8)
    setChartOption('modelRank', {
      ...theme,
      tooltip: {
        ...theme.tooltip,
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params: Array<{ dataIndex: number; marker: string }> | undefined) => {
          const index = Number(params?.[0]?.dataIndex ?? -1)
          const row = rows[index]
          if (!row) return ''
          return tooltipHeading(row.name) + tooltipRows([
            tooltipRow('成功调用', `${formatInteger(row.successful_calls)} 次`, params?.[0]?.marker || ''),
            tooltipRow('成功率', formatPercent(row.success_rate)),
            tooltipRow('平均耗时', formatDuration(row.average_success_duration_ms)),
          ])
        },
      },
      legend: { show: false },
      grid: { left: 12, right: 54, top: 12, bottom: 16, containLabel: true },
      xAxis: {
        type: 'value',
        minInterval: 1,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: theme.xAxis.axisLabel,
        splitLine: theme.yAxis.splitLine,
      },
      yAxis: {
        type: 'category',
        data: rows.map(row => row.name),
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { ...theme.yAxis.axisLabel, width: 130, overflow: 'truncate' },
      },
      series: [{
        name: '成功次数',
        type: 'bar',
        data: rows.map(row => ({
          value: row.successful_calls,
          itemStyle: { color: getModelColor(row.name), borderRadius: [0, 4, 4, 0] },
        })),
        barMaxWidth: 24,
        label: { show: true, position: 'right', color: '#6b6b6b', formatter: '{c}' },
      }],
    }, mode)
  }

  function renderCharts(mode: RenderMode = 'refresh') {
    updateModelTrendChart(mode)
    updateCallTrendChart(mode)
    updateSuccessRateChart(mode)
    updateDurationChart(mode)
    updateModelShareChart(mode)
    updateModelRankChart(mode)
  }

  function bootstrapCharts() {
    if (chartsReady || !snapshot.value) return
    const echarts = (window as typeof window & {
      echarts?: { init: (element: HTMLElement) => ChartInstance }
    }).echarts
    if (!echarts) return
    const refs: Record<ChartKey, HTMLDivElement | null> = {
      modelTrend: modelTrendChartRef.value,
      callTrend: callTrendChartRef.value,
      successRate: successRateChartRef.value,
      duration: durationChartRef.value,
      modelShare: modelShareChartRef.value,
      modelRank: modelRankChartRef.value,
    }
    if (Object.values(refs).some(value => value === null)) return
    ;(Object.keys(refs) as ChartKey[]).forEach((key) => {
      charts[key] = echarts.init(refs[key] as HTMLDivElement)
    })
    chartsReady = true
    renderCharts('initial')
  }

  function scheduleChartUpdate(mode: RenderMode = 'refresh') {
    void nextTick(() => {
      requestAnimationFrame(() => {
        if (!chartsReady) bootstrapCharts()
        else renderCharts(mode)
      })
    })
  }

  function resizeCharts() {
    if (!chartsReady) return
    requestAnimationFrame(() => Object.values(charts).forEach(chart => chart?.resize()))
  }

  function disposeCharts() {
    Object.values(charts).forEach(chart => chart?.dispose())
    ;(Object.keys(charts) as ChartKey[]).forEach(key => { charts[key] = null })
    chartsReady = false
    modelShareMobile = null
  }

  function applySnapshots(values: DashboardResponse[]) {
    let changed = false
    const next = { ...snapshots.value }
    values.forEach((value) => {
      const previous = next[value.time_range]
      if (dashboardContentSignature(previous || null) === dashboardContentSignature(value)) return
      next[value.time_range] = value
      changed = true
    })
    if (!changed) return false
    snapshots.value = next
    snapshot.value = values[0] || snapshot.value
    scheduleChartUpdate()
    return true
  }

  async function loadDashboard(options: {
    silent?: boolean
    source?: 'auto' | 'manual'
  } = {}) {
    if (options.source === 'auto' && requestCount > 0) return undefined
    const requestedRanges = selectedRanges()
    requestCount += 1
    try {
      return await dashboardQuery.run(
        () => Promise.all(requestedRanges.map(timeRange => statsApi.overview(timeRange))),
        {
          silentLoading: options.silent ?? Boolean(snapshot.value),
          silentError: Boolean(snapshot.value),
          apply: applySnapshots,
          onError: (_message, error) => console.error('Failed to load dashboard:', error),
        },
      )
    } finally {
      requestCount = Math.max(0, requestCount - 1)
    }
  }

  function refreshDashboard() {
    return loadDashboard({ silent: Boolean(snapshot.value), source: 'manual' })
  }

  function handleResize() {
    const isMobile = window.innerWidth < 768
    if (chartsReady && modelShareMobile !== isMobile) updateModelShareChart()
    resizeCharts()
  }

  watch([
    modelTrendRange,
    callTrendRange,
    successRateRange,
    durationRange,
    modelShareRange,
    modelRankRange,
  ], () => {
    scheduleChartUpdate()
    if (pageRuntime.canRun.value) void loadDashboard({ silent: Boolean(snapshot.value), source: 'manual' })
  })

  pageRuntime.onActivate(() => {
    window.addEventListener('resize', handleResize)
    if (snapshot.value) scheduleChartUpdate()
    void loadDashboard({ silent: Boolean(snapshot.value), source: 'manual' })
    dashboardPolling.start()
    resizeCharts()
  })

  pageRuntime.onDeactivate(() => {
    window.removeEventListener('resize', handleResize)
    dashboardPolling.stop()
    dashboardQuery.invalidate()
  })

  pageRuntime.onHide(() => {
    window.removeEventListener('resize', handleResize)
    dashboardPolling.stop()
    dashboardQuery.invalidate()
  })

  pageRuntime.onShow(() => {
    window.addEventListener('resize', handleResize)
    dashboardPolling.start()
    resizeCharts()
    void loadDashboard({ silent: Boolean(snapshot.value), source: 'manual' })
  })

  onBeforeUnmount(() => {
    window.removeEventListener('resize', handleResize)
    dashboardPolling.stop()
    dashboardQuery.invalidate()
    disposeCharts()
  })

  return {
    stats,
    dashboardDataReady,
    isLoading,
    errorMessage,
    modelTrendRange,
    callTrendRange,
    successRateRange,
    durationRange,
    modelShareRange,
    modelRankRange,
    modelTrendChartRef,
    callTrendChartRef,
    successRateChartRef,
    durationChartRef,
    modelShareChartRef,
    modelRankChartRef,
    refreshDashboard,
  }
}
