<!-- newmax-app:app-d2394da4-2efd-47a8-9913-8b3c10b2055d:start -->
# 文件日报

[built by newmax](https://newmax.ai)

自动汇总常用目录中每天新建与修改的文件，按软件归属和类型分组，按日期回溯查看

## 功能 / Features

- 自动检测并监控本机常用目录（下载/文档/桌面），并按内置归属规则把文件归到微信、QQ、钉钉、飞书等软件名下；支持用户自定义监控目录与归属规则
- 本地索引服务扫描监控目录，用创建时间判定“当日新建”、修改时间判定“当日修改”，构建并持久化按日期组织的文件索引（应用重启后保留，增量更新不全量重扫）
- 日期选择视图：前一天/后一天/日期选择器/近30天时间线可任选日期，统计条显示当天文件数、总大小、涉及分组数
- 当日清单支持“按软件”与“按类型”两种分组切换，并支持文件名实时搜索过滤
- 手动刷新：增量更新带可见进度（目录/文件计数）；设置中可强制全量重建
- 文件行提供“打开所在文件夹”操作；监控目录缺失时在设置中显示状态
- 服务生命周期安全：数据目录单例锁、原子发布 connection\.json、Bearer 鉴权 /health、UI 提供停止服务按钮
- 打开有文件的日期自动生成实时小结：软件来源与大小占比、内容构成（类型）、存放位置规则评估（根部堆积/程序类散落/临时缓存/大文件）、管理能力 0\-100 打分；可按需调用 AI 深度分析（软件身份、内容性质、存放合理性、整理建议），结果按日期缓存可回看

## 源码 / Source

- [浏览应用源码 / Browse source](newmax-apps/app-d2394da4-2efd-47a8-9913-8b3c10b2055d/)
- [应用原始文档 / App documentation](newmax-apps/app-d2394da4-2efd-47a8-9913-8b3c10b2055d/README.md)
- 版本 / Version: 0\.1\.0
- NewMax runtime: ^1\.0\.0
- 权限 / Permissions: data\.kv, files\.appData, notifications, ai\.chat
- [本次发布 / Release](https://github.com/li784693346/newmax-app-li/releases/tag/app-08068fc6-44b5-49f3-9e97-cd0184d2c13b)

## 安装 / Install

1. 安装并打开 / Install and open [NewMax](https://newmax.ai).
2. 本地应用 → 激活应用。 / Local apps → Activate app.
3. 粘贴下方完整口令，等待下载校验。 / Paste the full code below and wait for verification.
4. 检查来源与权限后，点击「信任并安装」。 / Review the publisher and permissions, then choose Trust and install.

```text
NEWMAX-APP:1:li784693346/newmax-app-li/app-08068fc6-44b5-49f3-9e97-cd0184d2c13b:54eb100b79bd63c4be1c243387244f0e0ae0bcbcc6b9f79fe98b718e0c97701e
```

无需 GitHub 登录；安装后需手动打开应用。口令对应此版本，旧口令不会自动升级。
No GitHub sign-in is required. Open the app manually after installation. Each code installs a fixed snapshot.

## 使用说明 / Notes

这是依赖 NewMax 的应用源码，不是独立系统安装程序。个人数据、账号密钥与开发对话不随包分发；外部服务、连接器和依赖可能需要自行配置。
This app requires NewMax. Personal data, credentials and development chats are not included. External services, connectors and dependencies may need separate setup.

许可证请查看源码中的 LICENSE 或原始文档；此发布流程不会自动添加或修改许可。
See the source LICENSE or original documentation for licensing. Publishing does not assign or change a license.
<!-- newmax-app:app-d2394da4-2efd-47a8-9913-8b3c10b2055d:end -->

# newmax-app-li
Public NewMax local app shares
