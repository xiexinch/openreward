# 部署指南

本指南介绍如何将自定义环境部署到 OpenReward 平台。

## 部署流程

OpenReward 平台通过 GitHub 集成实现自动化部署：

1. 将环境代码推送到 GitHub 仓库
2. 在 [OpenReward 仪表板](https://openreward.ai) 中连接仓库
3. 配置计算资源
4. 每次推送到指定分支自动触发构建和部署

## 项目结构

一个可部署的环境项目应包含以下文件：

```
my-environment/
├── server.py              # 入口文件，导出 FastAPI app
├── requirements.txt       # Python 依赖
├── Dockerfile             # （可选）自定义镜像
├── pyproject.toml         # （可选）项目配置
└── README.md              # 环境说明
```

### server.py

```python
from openreward.environments import Server
from my_env import MyEnvironment

app = Server(environments=[MyEnvironment]).app
```

### requirements.txt

```
openreward>=0.1.0
# 其他依赖
```

### Dockerfile（可选）

如果使用标准 Python 镜像，通常不需要 Dockerfile。OpenReward 会自动构建。但如果需要系统级依赖（如 `pdftotext`、`libreoffice`），提供自定义 Dockerfile：

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
```

## 使用 CLI 创建环境

SDK 提供 `orwd create` 命令在 OpenReward 平台上注册环境：

```bash
# 创建公开环境
orwd create my-environment --description "数学问题求解环境"

# 创建私有环境
orwd create my-environment --description "内部评估环境" --private

# 在组织命名空间下创建
orwd create my-environment --description "团队环境" --namespace my-org
```

**前提条件：** 设置 `OPENREWARD_API_KEY` 环境变量。

## 配置计算资源

在 OpenReward 仪表板中配置：

| 配置项 | 说明 |
|---|---|
| CPU | 每个实例的 CPU 核心数 |
| 内存 | 每个实例的内存大小 |
| 并发实例数 | 同时运行的环境实例数量 |
| 自动扩展 | 根据负载自动增减实例 |

## 环境变量

部署时可以通过 OpenReward 仪表板配置环境变量：

| 变量 | 说明 |
|---|---|
| `OPENREWARD_API_KEY` | 用于 rollout 日志上传和沙箱创建 |
| `OPENREWARD_URL` | 覆盖平台地址（默认 `https://openreward.ai`） |
| `OPENREWARD_USE_STRUCTURED_LOGS` | 设置为 `1` 启用 JSON 日志 |

## Secrets

敏感信息（API 密钥、数据库密码）应使用 **Secrets** 功能，而不是硬编码或作为普通环境变量：

1. 在 OpenReward 仪表板的 Secrets 页面添加
2. 在环境代码中通过 `self.secrets` 访问

```python
class MyEnv(Environment):
    async def setup(self):
        api_key = self.secrets.get("OPENAI_API_KEY")
```

## 版本管理

每次推送代码到连接的分支都会触发新版本的构建。OpenReward 会自动：

1. 读取 `pyproject.toml` 或 `setup.py` 中的版本号
2. 构建 Docker 镜像
3. 运行健康检查
4. 滚动更新实例

如果构建失败，可以在仪表板查看构建日志。

## 健康检查

Server 自动暴露 `/health` 端点。OpenReward 平台会定期调用该端点检查环境健康状态。确保环境能在合理时间内响应（建议 < 5 秒）。

## 监控和日志

部署后可以通过以下方式监控：

- **构建日志** —— 每次推送的构建输出
- **运行日志** —— 环境实例的实时日志流
- **指标面板** —— 请求量、延迟、错误率

建议在生产环境设置 `OPENREWARD_USE_STRUCTURED_LOGS=1` 以获取结构化日志。

## 本地测试后再部署

在推送到生产前，先在本地验证：

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动本地服务器
uvicorn server:app --reload

# 3. 运行测试
pytest

# 4. 用客户端连接测试
python -c "
import asyncio
from openreward import AsyncOpenReward
client = AsyncOpenReward(api_key='test')
# ... 测试代码
"
```

## 常见问题

### 构建失败：依赖安装超时

在 `requirements.txt` 中使用国内镜像或指定精确版本号：

```
--index-url https://mirrors.aliyun.com/pypi/simple/
openreward==0.1.112
```

### 实例启动慢

如果 `setup()` 需要较长时间（如加载大模型），考虑：

1. 在 Dockerfile 中预加载资源到镜像
2. 使用异步 `setup()` 避免阻塞
3. 增加健康检查超时时间

### 内存不足

工具调用产生大量输出时可能导致内存问题：

1. 限制 `ToolOutput.blocks` 的大小
2. 对于沙箱命令，使用 `max_bytes` 参数限制输出
3. 在仪表板中增加实例内存配置
