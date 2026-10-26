import { useEffect, useRef } from 'react'
import { BarChart } from 'echarts/charts'
import type { BarSeriesOption } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import type { GridComponentOption, TooltipComponentOption } from 'echarts/components'
import { init, use as registerCharts } from 'echarts/core'
import type { ComposeOption } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'

import type { ComparisonResult } from '../types'

registerCharts([BarChart, GridComponent, TooltipComponent, CanvasRenderer])
type ChartOption = ComposeOption<BarSeriesOption | GridComponentOption | TooltipComponentOption>

export default function ComparisonChart({ result }: { result: ComparisonResult }) {
  const container = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!container.current) return
    const chart = init(container.current)
    const raw = [result.baseline.value, result.candidate.value]
    const values = raw.map(Number)
    const scale = Math.max(...values.map(Math.abs)) || 1
    const names = [`基线 ${result.baseline.run_id}`, `候选 ${result.candidate.run_id}`]
    const option: ChartOption = {
      animation: !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      grid: { left: 20, right: 20, top: 38, bottom: 44 },
      tooltip: {
        trigger: 'item', renderMode: 'richText',
        formatter: (params) => {
          const entry = Array.isArray(params) ? params[0] : params
          return entry ? `${names[entry.dataIndex]}\n${result.metric.name}：${raw[entry.dataIndex]} ${result.metric.unit}` : ''
        },
      },
      xAxis: {
        type: 'category', data: names,
        axisLine: { lineStyle: { color: '#e1e8f4' } }, axisTick: { show: false },
        axisLabel: { color: '#8799b6', width: 140, overflow: 'truncate', interval: 0 },
      },
      yAxis: { type: 'value', axisLabel: { show: false }, splitLine: { lineStyle: { color: '#edf1f8' } } },
      series: [{
        type: 'bar', barMaxWidth: 64,
        data: values.map((value, index) => ({ value: value / scale, itemStyle: { color: index === 0 ? '#bccae1' : '#4c88ed', borderRadius: [6, 6, 0, 0] } })),
        label: { show: true, position: 'top', color: '#617ca7', width: 150, overflow: 'truncate', formatter: (params) => raw[params.dataIndex] ?? '' },
      }],
    }
    chart.setOption(option)
    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(container.current)
    return () => { observer.disconnect(); chart.dispose() }
  }, [result])
  return <div className="comparison-chart" ref={container} aria-hidden="true" />
}
