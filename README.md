# 念念 · 家庭记忆传家宝

第一阶段纵向切片已经封存；第二阶段开发已在 `第二阶段分支` 完成，当前等待用户本人完成安全操作与最终人工验收：

```text
测试建档 → 一个温和问题 → 录音/上传 → 本地转写
→ 人工校对 → 忠实整理 → 人工确认 → 故事时间线
```

## 当前安全边界

- 真实资料模式仍被后端锁定；当前只使用虚构测试资料。
- 默认 `ASR_PROVIDER=mock`、`LLM_PROVIDER=mock`，用于离线验证流程，不代表真实模型验收。
- 文本敏感字段与媒体的 AES-256-GCM 加密迁移已实现；当前家庭仍未初始化正式主密钥，只有人工完成“钥匙串初始化 → 恢复包下载 → 恢复验证 → 显式启用”后才会解锁真实资料。
- 真实家庭录音只允许本机转写；人工校对稿默认不发送千问，必须按单次文本明确授权，且原始录音永不发送千问。
- 服务只绑定 `127.0.0.1`，不得直接暴露公网。

## 已实现

- FastAPI `/api/v1`、Pydantic 结构校验和统一脱敏错误；
- SQLite + SQLAlchemy + Alembic 初始迁移；
- 原始音频与派生 WAV 分开保存，MIME/文件头/大小/路径和 SHA-256 校验；
- 持久化转写与整理任务，进程中断后标记为可重试；
- 原始转写、人工校对稿、模型草稿和正式故事分层保存；
- 只有人工确认后才能归档；跳过会清除未归档音频和文本；
- FunASR/Paraformer 本地转写与通义千问兼容接口；
- Next.js 最小 Web 界面，包含录音、上传、校对、退回、确认和时间线；
- Mac 钥匙串主密钥初始化、家庭离线恢复包下载和安全状态界面；
- 恢复包回读验证和真实资料启用门：未验证恢复包时后端拒绝启用；
- 应用层加密存储：姓名、档案字段、转写、草稿、故事、回忆录和媒体使用独立 nonce 的 AES-256-GCM，密钥、恢复口令不进入 SQLite、`.env`、日志或 Git；
- 本机整库备份与恢复演练：使用 SQLite 在线快照、资产清单和 SHA-256，先在全新目录校验数据库/资产，再用离线恢复包解密抽样文本与全部加密媒体；备份不包含主密钥、恢复口令或恢复包；
- 云模型后端授权门：按家庭、会话、用途和校对稿哈希校验一次性授权，修改文字后旧授权自动失效；
- 七个人生阶段各三道本地题目，优先选择尚未问过的题目；“不要再问”会在后端阻断，“先问我”必须得到本次确认；
- 家庭人物与关系数据底座、阶段覆盖度，以及只从人工确认故事写入的长期记忆索引；
- 家庭成员与关系维护界面：新增、改名、建立/删除关系和删除普通成员；讲述者不能在家谱界面误删；
- 本机应用内提醒：不接微信、短信、邮件或系统通知，“不要再问”会暂停同话题提醒；
- 版本化 Markdown 与打印版 PDF 回忆录：只使用人工确认故事，保留来源清单与 SHA-256 完整性校验；PDF 失败不影响 Markdown；
- 照片/老物件图片触发：校验 JPEG、PNG 和 WebP 真实内容、尺寸与 SHA-256，只提问安全的固定问题，不识别人脸或猜测人物、地点、年代与事件；
- 390 px 移动宽度布局、加载/失败/重试/空状态。

## 环境

- Python：`/Users/hurmit/.local/bin/python3.11`（不要使用系统 Python 3.9）
- Node：当前已用本机 Node 24.19.0 完成构建；项目声明兼容 Node 22～24
- 后端端口：8011
- 前端端口：3011

## 首次安装

后端使用 `backend/uv.lock` 锁定完整依赖。推荐按锁文件安装基础与测试依赖：

```bash
cd /Users/hurmit/Desktop/念念
/Users/hurmit/.local/bin/uv sync --directory backend --extra dev
```

如要使用真实本地 FunASR，再安装 ASR 可选依赖（包含 PyTorch，体积较大）：

```bash
/Users/hurmit/.local/bin/uv sync --directory backend --extra dev --extra asr
```

如果换到没有 `uv` 的环境，也可以用 Python 3.11 和 `pip install -e 'backend[dev,asr]'` 安装，但该方式不会严格按锁文件复现。

前端依赖：

```bash
cd /Users/hurmit/Desktop/念念/frontend
npx -y npm@10.9.4 install
```

不要把真实 Key 写进源码。需要改配置时，把根目录 `.env.example` 复制为 `.env`，真实 `.env` 已被 Git 忽略。

## 启动

```bash
cd /Users/hurmit/Desktop/念念
./scripts/start_local.sh
```

打开：<http://127.0.0.1:3011>

停止：

```bash
./scripts/stop_local.sh
```

## 使用真实本地 FunASR

在根目录 `.env` 中设置：

```dotenv
ASR_PROVIDER=funasr
ASR_MODEL_ID=paraformer-zh
MODEL_CACHE_ROOT=./backend/.model-cache
```

首次转写会下载约 1 GB 的模型到 `backend/.model-cache/`。该目录不会进入 Git。默认 mock 模式不会下载模型。

## 使用通义千问

当前只允许用虚构测试文本做云模型冒烟。在 `.env` 中设置：

```dotenv
LLM_PROVIDER=qwen
LLM_MODEL=qwen3.7-plus-2026-05-26
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=在本机填写，不要发到对话或提交到 Git
```

当前本机 `.env` 已完成 Key 配置，并已用虚构文本通过真实通义千问冒烟；Key 不进入源码、日志或 Git。

## 验证命令

```bash
cd /Users/hurmit/Desktop/念念/backend
.venv/bin/pytest
.venv/bin/alembic -c alembic.ini upgrade head

cd /Users/hurmit/Desktop/念念/frontend
npm run lint
npm run typecheck
npm run build
```

验收状态见 [第一阶段验收记录](docs/第一阶段验收记录.md)、[第二阶段验收记录](docs/第二阶段验收记录.md) 和 [第二阶段人工验收清单](docs/第二阶段人工验收清单.md)。
