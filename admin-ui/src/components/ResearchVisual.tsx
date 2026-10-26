import { useId } from 'react'

import Icon from './Icon'

// An abstract illustration of connected tools, not a fabricated execution trace.
export default function ResearchVisual() {
  const id = useId().replaceAll(':', '')
  return <div className="research-visual" aria-hidden="true">
    <div className="visual-glow" />
    <svg className="orbital-lines" viewBox="0 0 520 420">
      <defs><linearGradient id={`${id}-orbit`} x1="0" y1="0" x2="1" y2="1"><stop stopColor="#c5d8ff" stopOpacity=".65" /><stop offset=".5" stopColor="#4a88ff" stopOpacity=".08" /><stop offset="1" stopColor="#769fff" stopOpacity=".5" /></linearGradient></defs>
      <g stroke={`url(#${id}-orbit)`} strokeWidth=".8"><ellipse cx="265" cy="210" rx="220" ry="92" transform="rotate(-28 265 210)" /><ellipse cx="265" cy="210" rx="178" ry="143" transform="rotate(-28 265 210)" /><ellipse cx="265" cy="210" rx="150" ry="187" transform="rotate(35 265 210)" /><path d="M55 210h420M265 22v376" strokeDasharray="2 7" /></g>
      <g fill="#b7ceff" stroke="none"><circle cx="75" cy="277" r="3" /><circle cx="410" cy="105" r="3" /><circle cx="344" cy="348" r="2" /><circle cx="174" cy="51" r="2" /></g>
    </svg>
    <div className="visual-core"><div className="core-edge" /><div className="core-face"><span className="intelligence-mark"><i /><i /><i /><i /></span><span>心语 Xinyu</span><small>RESEARCH INTELLIGENCE</small></div></div>
    <div className="orbit-label label-data"><span><Icon name="file" /></span><div><strong>实验数据</strong><small>DATA</small></div></div>
    <div className="orbit-label label-agent"><span><Icon name="spark" /></span><div><strong>工具协作</strong><small>AGENT</small></div></div>
    <div className="orbit-label label-memory"><span><Icon name="memory" /></span><div><strong>项目记忆</strong><small>MEMORY</small></div></div>
    <span className="visual-caption">CONNECT THE DOTS. ADVANCE YOUR RESEARCH.</span>
  </div>
}
