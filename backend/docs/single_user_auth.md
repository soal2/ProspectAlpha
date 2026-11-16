# 单用户会话登录

这是整个部署共用的一个操作者，不是多用户账号体系。登录后可以看到该部署里的全部文档和分析任务。上传大小、JSON 大小、文档数量和分析任务数量都是部署级上限，不会按人拆分。

密码和会话签名密钥只放在后端环境变量里。前端构建不接收、也不打包这些值。

## 必须配置的变量

生产环境（`APP_ENV=production`）缺少任一项都会拒绝启动：

- `API_AUTH_USERNAME`
- `API_AUTH_PASSWORD`：至少 12 个字符，只写在密钥管理或宿主环境里
- `API_AUTH_SESSION_SECRET`：至少 32 个字符，用于签名会话 cookie

不要把这些值写进仓库、日志、URL 或接口响应。登录失败的响应只有“用户名或密码不正确”。

本地若要暂时不登录，必须同时满足：`APP_ENV` 不是 `production`，且 `API_AUTH_DISABLED=1`。生产环境忽略这个开关并拒绝启动。

## 会话

`POST /auth/login` 校验用户名和密码后，设置：

- `pa_session`：HttpOnly，路径 `/`，带过期时间。生产环境强制 `Secure`。
- `pa_csrf`：给浏览器脚本读取的 CSRF 值，不是会话密钥。

`GET /auth/session` 在已登录时返回 `csrf_token`，供页面刷新后继续使用。`POST /auth/logout` 删除服务端会话并清掉 cookie，旧 cookie 立即失效。

默认有效期是 28800 秒，可用 `API_AUTH_SESSION_TTL_SECONDS` 调整。会话保存在当前 API 进程内存中；进程重启后全部失效。当前启动命令只跑一个 uvicorn 进程。不要把同一个服务扩成多个互不共享内存的 worker，否则登录状态不会跨进程。

登录失败按来源地址计数，默认 15 分钟内 5 次失败后返回 429。`API_AUTH_TRUST_PROXY=1` 时使用 `X-Forwarded-For` 的最后一跳；未开启时使用直接连接地址，避免客户端伪造该头绕过限制。

## 同源代理

推荐浏览器只访问一个源，由该源把 `/api` 反代到后端。本地 Vite 已把 `/api` 代理到 `http://127.0.0.1:8000`。此时：

- 前端不设置 `VITE_API_BASE_URL`，请求走相对路径 `/api`
- `API_AUTH_COOKIE_SAMESITE=lax`
- `CORS_ALLOW_ORIGINS` 包含页面源，例如 `http://localhost:5173`

同源的 `POST`、`PUT`、`PATCH`、`DELETE` 必须带请求头 `X-CSRF-Token`，值与登录或 `/auth/session` 返回的 `csrf_token` 一致，并且 `Origin` 必须落在允许的源里。`GET`（含 SSE 和下载）只校验会话 cookie。`/health` 保持最小未登录探活，不返回鉴权配置。

## 跨源静态前端

GitHub Pages 与 API 不在同一主机时，Lax cookie 不会在跨站请求中发出。需要同时：

- `API_AUTH_COOKIE_SAMESITE=none`（生产环境会强制 `Secure`）
- `CORS_ALLOW_ORIGINS` 写成确切的页面源，不要用 `*`
- 前端 `VITE_API_BASE_URL` 写成 API 的绝对地址
- 浏览器请求使用 `credentials: "include"`

`render.yaml` 里页面源是 `https://soal2.github.io`，因此清单把 `SameSite` 设为 `none`，并把用户名、密码和会话密钥标成仅在宿主里填写。更换为同源反代后，把 `SameSite` 改回 `lax`。
