# BUILD_PLAN · 文件日报

## 架构
- **前端** `src/index.html`：无框架单页；通过 `sdk.files.readText("connection.json")` 获取本地服务 baseUrl+token，`fetch` 调 API。视图偏好走 `sdk.data`。
- **后端** `backend/server.py`：纯标准库 ThreadingHTTPServer，监听 127.0.0.1 随机端口。
  - 数据目录：`settings.json`（roots/rules/skipDirs）、`index.json`（files/watermark/lastScan）、`service.lock`（单例，pid 探活接管陈旧锁）、`connection.json`（原子 tmp+os.replace 发布 {appId,pid,baseUrl,token}）。
  - 扫描：os.walk 顶层=监控根；目录 mtime 水位剪枝（增量）；全量上限 300s 带告警；增量后对旧记录 stat 校验存在性并更新元数据。
  - 归属：软件规则（最长前缀）→ 否则监控根 label。
  - API：/health(Bearer)、GET /api/overview|day|config|scan/status、POST /api/scan|config|reveal|stop。CORS `*` + OPTIONS。
- **运行时** `runtime/`：CPython 3.12.10 embeddable（官方 zip 解压），manifest.services 以 `runtime/python.exe` 启动。

## 阶段
| 阶段 | 状态 | 备注 |
| --- | --- | --- |
| requirements | ✅ | 已与用户确认三方向 |
| plan | ✅ | 本地服务 + 单页 |
| generate | ✅ | server.py + index.html + manifest |
| validate (API 冒烟) | ✅ | health/scan(full/incremental)/overview/day/config/reveal 403·404·ok/双实例锁/stop 清理 —— 全部通过（2026-11-07） |
| run (preview) | ⏳ | 宿主启动 services → 独立窗口 |
| snapshot/acceptance | ⏳ | inspect_app_ui + evaluate_app 真实交互 |

| v0.2 实时小结 | 🔄 | R10/R11：四栏规则小结 + AI 深度分析（sdk.ai.chat，握手校验，按日期缓存）；manifest requiredCapabilities 增 ai.chat |

## 已知风险与对策
- Documents 过大扫描慢 → 进度条 + 300s 超时告警 + 可跳过目录（skipDirs）。
- 旧版 Electron 无 services 支持 → readiness 契约按 host 文档；若 preview 不启动服务，UI 显示未连接指引（不假装可用）。
- reveal 弹资源管理器属预期行为，验收时注意截图前关闭多余窗口。
