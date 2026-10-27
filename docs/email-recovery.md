# 邮箱绑定与密码找回

本功能使用已有 SMTP 服务，不要求更换模型或购买其他服务。真实 SMTP 授权码仅填写本地 `.env`，不进入 Git，也不要发到聊天中。

## 本地配置

```dotenv
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=your-account@example.com
SMTP_PASSWORD=填邮箱服务商的授权码
SMTP_FROM=your-account@example.com
SMTP_TIMEOUT_SECONDS=10
```

以上是占位示例。端口和加密方式按服务商要求填写；465 常见对应 `SMTP_SECURITY=ssl`，587 常见对应 `starttls`。不要同时启用两种模式。程序校验证书，不允许远程明文 SMTP；`plain` 只允许回环地址，用于隔离测试邮件服务器。

保存后重新运行 `scripts/start.ps1 -NoBrowser`，让 API 和 Worker 一起读取配置。未配置时账号原有功能可用，申请邮件会显示“邮件服务尚未配置”，不会假装发送成功。

## 页面流程

1. 登录后打开“账号管理 → 绑定 / 更换找回邮箱”。填写邮箱、当前密码和图形验证码。
2. 后台发送 6 位邮箱验证码；在申请的浏览器填写，验证后邮箱才生效。邮箱必须唯一。
3. 登录窗口点击“忘记密码”，填写用户名、提前验证的邮箱和图形验证码。
4. 在同一浏览器填写邮件验证码、新密码及确认密码。重置后所有旧会话失效，重新登录访问原空间。

注册仍可先不绑定邮箱。没有验证过邮箱的账号不能使用邮件找回；产品不会凭一个未验证的地址重置密码。

## 后端对应

| 接口 | 用途 |
| --- | --- |
| `GET /api/relationship/auth/email` | 脱敏邮箱、是否验证、邮件服务是否已配置 |
| `POST /api/relationship/auth/email/send` | 验证当前密码与图形验证码，创建绑定邮件 |
| `POST /api/relationship/auth/email/verify` | 消费一次性验证码并绑定邮箱 |
| `GET /api/relationship/auth/email/delivery/{id}` | 账号及申请浏览器内查看绑定邮件投递状态 |
| `POST /api/relationship/auth/recovery/send` | 申请找回；账号不匹配时返回同样的通用提示 |
| `POST /api/relationship/auth/recovery/verify` | 消费验证码、更新密码并撤销旧会话 |

Redis 承担 IP / 邮箱 / 全局限流和 60 秒冷却。PostgreSQL 持有带行锁的验证码状态，验证码 10 分钟过期、最多 5 次尝试、使用后不可重放，并绑定浏览器、用途和密码版本。密码变更后，之前的验证码失效。

邮件进入 PostgreSQL outbox，由独立 Worker 发送，网络请求不持有账号行锁。临时 SMTP 故障最多退避重试 3 次；固定 Message-ID 有助于识别重试，但 SMTP 不能承诺恰好一次送达。发送成功仅表示 SMTP 接受，不保证已进收件箱。

outbox 的验证码与收件地址以 Fernet 加密，发送或终止后擦除负载。请把 `.runtime/secrets/email.key` 与数据库分别安全备份；遗失密钥会使未发送邮件无法解密。日志只记通用错误码、任务 ID 和异常类型，不写邮箱验证码、邮件正文、SMTP 异常文本或授权码。

## 验证边界

`tests/test_email_recovery.py` 在独立 PostgreSQL 测试库与空 Redis DB 中验证绑定、找回、一次性、过期、尝试次数、浏览器绑定、密码版本失效、会话撤销、加密 outbox、限流和 SMTP 重试。另启动隔离的回环 SMTP 协议服务，真实使用标准库 SMTP 客户端完成一封合成邮件传输。

这些检查不代表你的真实 SMTP 账号已联通。目前真实服务凭据仍待本地填写，真实投递需要配置后验证。

设计参考：[Python smtplib](https://docs.python.org/3/library/smtplib.html)、[OWASP 密码找回指南](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html)。
