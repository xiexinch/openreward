"""由 `orwd init` 生成的示例沙盒环境服务器。"""
from openreward.environments import Server

from sandbox_env import SandboxEnv

if __name__ == "__main__":
    server = Server([SandboxEnv])
    server.run()
