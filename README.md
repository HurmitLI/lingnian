# 聆年 · 家庭记忆传家宝

聆年帮助家人通过语音采访留下往事，将原始录音、文字、照片和家人确认的故事放在一起，方便回听、查找和长期保存。

[访问聆年](https://snj9mfcngm6tv8vlfvj2c.apigateway-cn-beijing.volceapi.com/) · [项目文档](docs/README.md)

## 主要功能

- **语音采访**：一次问一个问题，支持连续讲述、暂停和继续，分开记录故事人物与实际讲述人。
- **故事整理**：保留原始转写、人工校对稿和 AI 整理稿，由家人确认后归档。
- **回忆档案**：查找故事、回听原声、补充照片，并继续未完成的记录。
- **家庭共建**：管理人物和成员，补充回忆，通过已有故事查找有来源的回答。
- **普通模式与老人模式**：普通模式提供完整入口；老人模式使用大字号、大按钮和简化导航，选择会保存在当前浏览器。
- **长期保存**：提供回忆录、原声和开放格式导出，具体能力取决于运行配置。

视频生成属于实验能力；生成任务完成不代表画面质量通过，也不代表还原了真实人物或历史现场。

## 使用

正式站点需要邀请码与账号。登录后在电脑左侧或手机页头选择“老人模式”，可以随时切回普通模式。

```text
建立人物档案 → 语音采访 → 检查整理内容 → 家人确认 → 保存故事与原声
```

真实录音、照片、个人资料、密钥和恢复材料不应提交到 Git。使用云端识别、整理或媒体服务前，应明确素材发送范围和授权；本机运行与云端部署的资料处理方式不同。

## 项目结构

| 目录 | 内容 |
| --- | --- |
| `frontend/` | Next.js、React、TypeScript 网页端 |
| `backend/` | FastAPI 接口、存储、采访与任务处理 |
| `generation-worker/` | 家用 Windows / ComfyUI 生成节点 |
| `scripts/` | 本地启动、检查与测试脚本 |
| `docs/` | 文档导航和节点接入说明 |

本分支同步普通模式与老人模式的前端源码。后端目录保留原有基线，不代表正式站点当前后端的完整源码；对接正式服务时需使用对应的 API 与登录配置。

## 本地开发

环境要求：Python 3.11～3.12、Node.js 20.9～24、uv。以下命令从项目根目录执行，启动脚本面向 macOS / zsh。

安装依赖：

```bash
uv sync --directory backend --extra dev
npm --prefix frontend ci
```

首次配置：

```bash
cp .env.example .env
```

在 `.env` 中填写自己的配置，不覆盖已有配置或提交真实凭据。默认模拟服务用于开发流程检查；如需本地 FunASR，可额外安装：

```bash
uv sync --directory backend --extra dev --extra asr
```

启动与停止：

```bash
./scripts/start_local.sh
./scripts/stop_local.sh
```

- 本地网页：[127.0.0.1:3011](http://127.0.0.1:3011/)
- 本地接口文档：[127.0.0.1:8011/docs](http://127.0.0.1:8011/docs)

前端对接已有后端时，参考 [生产配置示例](frontend/.env.production.example) 设置 `NIANNIAN_BACKEND_URL` 与登录模式。部署方法见 [产品上线部署手册](docs/开发文档/产品上线部署手册.md)。

## 开发检查

```bash
uv run --directory backend pytest
npm --prefix frontend run test
npm --prefix frontend run lint
npm --prefix frontend run typecheck
npm --prefix frontend run build
npm --prefix frontend run e2e
```

端到端测试使用隔离数据库、媒体目录和模拟模型；流程检查不能替代真实语音、生成质量或老人可用性验收。
