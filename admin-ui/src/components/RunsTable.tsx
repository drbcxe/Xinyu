import { useState } from 'react'
import type { ExperimentRun } from '../types'

export default function RunsTable({ runs }: { runs: ExperimentRun[] }) {
  const [page, setPage] = useState(0)
  const pages = Math.max(1, Math.ceil(runs.length / 50))
  const currentPage = Math.min(page, pages - 1)
  return (
    <>
      <div className="runs-table-scroll" tabIndex={0} role="region" aria-label="实验记录表，可横向滚动">
        <table className="runs-table">
          <caption className="visually-hidden">当前文件的实验记录及来源行号</caption>
          <thead><tr>
            <th scope="col">实验编号</th><th scope="col">模型</th><th scope="col">指标值</th>
            <th scope="col">指标 / 单位</th><th scope="col">优化方向</th><th scope="col">数据版本</th>
            <th scope="col">划分</th><th scope="col">评估协议</th><th scope="col">随机种子</th><th scope="col">CSV 行号</th>
          </tr></thead>
          <tbody>{runs.slice(currentPage * 50, currentPage * 50 + 50).map((run) => (
            <tr key={run.id}>
              <th scope="row">{run.run_id}</th><td>{run.model_name}</td><td className="metric-cell">{run.metric_value}</td>
              <td>{run.metric_name}<small>{run.metric_unit}</small></td>
              <td>{run.optimization === 'min' ? '越小越好' : '越大越好'}</td>
              <td>{run.dataset_version}</td><td>{run.split_id}</td><td>{run.protocol_version}</td><td>{run.seed}</td><td>{run.source_line}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <div className="table-pagination">
        <span>共 {runs.length} 条实验 · 第 {currentPage + 1} / {pages} 页</span>
        {pages > 1 && <div>
          <button className="refresh-button" type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>上一页</button>
          <button className="refresh-button" type="button" disabled={currentPage === pages - 1} onClick={() => setPage(currentPage + 1)}>下一页</button>
        </div>}
      </div>
    </>
  )
}

