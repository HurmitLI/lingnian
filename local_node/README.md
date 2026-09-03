# 聆年本地生成节点

这是家用电脑主动轮询云端的独立节点。它不接受公网入站连接，拒绝未授权、无用途或超预算任务，并用 SQLite 保留幂等与恢复状态。

普通用户请双击仓库根目录的 `启动聆年影像节点.cmd`。生产令牌不写入文件；运行 `python -m lingnian_node.credentials set-token` 保存到 Windows 凭据管理器。

状态依次为：空闲、领取任务、下载素材、生成中、上传中、成功、失败。模拟模式从 `runtime/mock-cloud/inbox` 领取 JSON，并把加密结果放到 `runtime/mock-cloud/uploaded`。
