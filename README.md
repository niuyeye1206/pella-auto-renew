# pella-auto-renew

Pella.app 免费服务器自动续期：每天定时登录面板，领取所有可用的
"Claim 16 Hours" 续期链接（每条 +16 小时，每天刷新两条）。

## 续期机制（2026-11-11 实测）

- 服务器详情页右侧 EXPIRY 面板里有两条续期链接（按钮文字均为
  "Claim 16 Hours"，链接形如 `https://pella.app/renew/<32 位 hex>`）。
- 每条领取成功 +16 小时，两条都领 = +32 小时/天，服务器每天消耗 24 小时，
  净增约 8 小时/天，续得上。
- 链接每 24 小时刷新一次；领过后按钮变 "Claimed"。
- 登录页为 Clerk 多步表单（先邮箱 -> Continue -> 密码），登录页无
  Cloudflare Turnstile，可直接用无头浏览器登录。

## 定时

- 每天北京时间 06:30 和 18:30 各跑一次（`30 22 * * *` / `30 10 * * *` UTC）。
- 每次领取所有未领的链接；无可领链接时只汇报剩余时间，不报错。

## Secrets（仓库 Settings -> Secrets and variables -> Actions）

| Secret | 说明 |
|---|---|
| `PELLA_EMAIL` | Pella 账号邮箱 |
| `PELLA_PASSWORD` | Pella 账号密码 |
| `TG_BOT_TOKEN` | Telegram 机器人 token（通知用） |
| `TG_CHAT_ID` | Telegram 通知目标 chat id |

## 手动触发

Actions -> Pella Auto Renew -> Run workflow。
