# 运行状态、日志与告警

## 本地查看

启动 API 与 Worker 后，在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m scripts.ops
```

命令只输出依赖、队列、HTTP 统计与告警，不输出运维密钥。开发环境自动在 `.runtime/secrets/ops.key` 生成随机密钥；生产环境必须显式配置至少 32 字符的 `OPS_TOKEN`。不要把它放进前端环境变量或浏览器存储。

| 接口 | 作用 | 访问条件 |
| --- | --- | --- |
| `GET /api/health` | API 进程存活 | 本机公开 |
| `GET /api/ready` | PostgreSQL、Redis、Qdrant、Worker、模型配置检查 | 本机公开；不就绪返回 503 |
| `GET /api/ops/status` | 队列、HTTP 汇总、当前告警 | `Authorization: Bearer <OPS_TOKEN>` |
| `GET /api/ops/metrics` | Prometheus 文本格式 HTTP 指标 | 同上 |

就绪探针最多缓存 3 秒。模型项只验证配置，不发送模型请求，不承诺平台有余额或实时可用；SMTP 属于可选功能，不阻断其余服务。当前 Qdrant 检查为服务 `/readyz`，不是资料质量或索引完整性检查。

## 不记录聊天正文的日志

API 和 Worker 分别输出 `.runtime/logs/app-api-<PID>.jsonl`、`app-worker-<PID>.jsonl`。每个进程文件 10 MiB 轮转、最多保留 3 个轮转文件。旧进程日志的时间保留策略另由数据生命周期维护处理。

所有 API 响应带 `X-Request-ID`；错误 JSON 使用同一编号。创建任务的请求日志记录服务器生成的 job ID，Worker 与模型调用日志使用同一 job ID，可据此关联请求、执行和模型记账。

格式器仅允许固定事件和少量字段，不保存请求正文、Cookie、Authorization、验证码、SMTP 异常原文、聊天文本、任意异常字符串或堆栈局部变量。访问日志使用路由模板而非实际 ID，排除查询参数；关闭 Uvicorn 默认访问日志，防止搜索内容随 URL 进入日志。

日志收集或 Redis 指标写入失败不会把已成功的业务请求改成失败。数据库连接和语句设置了超时；模型响应按 2 MiB 封顶，在读取响应体时检查执行期限，并在并发租期中为在途读取留出超时余量。

## 指标与本地告警

HTTP 次数、5xx、时长总和与分桶以 Redis 共享，按 UTC 日统计，保留 48 小时。标签只有路由模板、HTTP 方法、状态类别，不使用用户 ID、邮箱、原文或任意路径。Prometheus 抓取端应知道该计数在 UTC 零点重置，不把它误认为进程整个生命周期的累计值。

Worker 每分钟在独立维护线程检测依赖不就绪、Worker 心跳消失、排队超过 60 秒、索引删除失败、邮件发送失败、失败任务，以及当日至少 20 个请求且 5xx 占比不低于 10% 的情况。状态变化写入结构化日志；状态与当前告警可通过运维接口查看。

这些告警目前没有短信、邮件或外部监控推送，不表示有人值守；没有擅自接入第三方平台。维护线程与分析、索引、微信通道及邮件线程分开，慢速清理不阻塞主循环的 Worker 心跳。

故障排查顺序：先看 `/api/ready`，再用 `scripts.ops` 区分依赖、积压和任务失败；持有请求编号时，在 API 日志中定位 job ID，再查看同一 job ID 的 Worker 事件。日志故意不含原文与完整异常，不应为排错临时打开敏感请求日志。

参考：[Prometheus 指标与标签规范](https://prometheus.io/docs/practices/naming/)。
# 检索缓存指标补充

受保护的运维状态新增 `retrieval_cache`，Prometheus 文本新增 `xinyu_rag_cache_*`。这些是响应进程的缓存条目、估算字节、构建/命中/超预算计数，不含问题、来源 ID 或空间标识；API 与各 Worker 的缓存彼此独立，不能把单进程值当作集群合计。估算缓存字节也不是进程 RSS，测量定义见 [检索性能](./retrieval-performance.md)。
