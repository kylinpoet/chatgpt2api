<template>
  <div class="space-y-5">
    <PageLoadingState
      v-if="!dashboardDataReady && isLoading"
      title="正在加载概览"
      description="读取最新账号、调用趋势和模型统计。"
    />

    <StateBlock
      v-else-if="!dashboardDataReady"
      :title="errorMessage || '暂无概览数据'"
      compact
      dashed
    >
      <Button class="mt-4" size="sm" variant="outline" @click="refreshDashboard">重新加载</Button>
    </StateBlock>

    <template v-else>
      <section class="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatCard
          v-for="stat in stats"
          :key="stat.label"
          :label="stat.label"
          :value="stat.value"
          :icon="stat.icon"
          :icon-bg="stat.iconBg"
          :icon-color="stat.iconColor"
        />
      </section>

      <section class="grid grid-cols-1 gap-4">
        <ChartCard title="模型调用分布">
          <template #actions>
            <TimeRangeTabs v-model="modelTrendRange" aria-label="模型调用分布时间范围" />
          </template>
          <div ref="modelTrendChartRef" class="h-72 w-full px-2"></div>
        </ChartCard>
      </section>

      <section class="grid grid-cols-1 gap-4">
        <ChartCard title="调用趋势">
          <template #actions>
            <TimeRangeTabs v-model="callTrendRange" aria-label="调用趋势时间范围" />
          </template>
          <div ref="callTrendChartRef" class="h-56 w-full"></div>
        </ChartCard>
      </section>

      <section class="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ChartCard title="成功率趋势">
          <template #actions>
            <TimeRangeTabs v-model="successRateRange" aria-label="成功率趋势时间范围" />
          </template>
          <div ref="successRateChartRef" class="h-56 w-full"></div>
        </ChartCard>

        <ChartCard title="平均成功耗时">
          <template #actions>
            <TimeRangeTabs v-model="durationRange" aria-label="平均成功耗时时间范围" />
          </template>
          <div ref="durationChartRef" class="h-56 w-full"></div>
        </ChartCard>
      </section>

      <section class="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ChartCard title="模型成功占比">
          <template #actions>
            <TimeRangeTabs v-model="modelShareRange" aria-label="模型成功占比时间范围" />
          </template>
          <div ref="modelShareChartRef" class="h-56 w-full"></div>
        </ChartCard>

        <ChartCard title="模型成功排行">
          <template #actions>
            <TimeRangeTabs v-model="modelRankRange" aria-label="模型成功排行时间范围" />
          </template>
          <div ref="modelRankChartRef" class="h-56 w-full"></div>
        </ChartCard>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { Button, ChartCard, StatCard } from 'nanocat-ui'
import PageLoadingState from '@/components/ai/PageLoadingState.vue'
import StateBlock from '@/components/ai/StateBlock.vue'
import TimeRangeTabs from '@/components/ai/TimeRangeTabs.vue'
import { useDashboardPage } from './dashboard/useDashboardPage'

defineOptions({ name: 'Dashboard' })

const {
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
} = useDashboardPage()
</script>
