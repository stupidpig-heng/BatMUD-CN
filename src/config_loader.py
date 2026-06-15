"""
Configuration loader — reads config.yaml
"""

import sys
from pathlib import Path
from typing import Any, Dict

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not found. Run: pip install pyyaml")
    sys.exit(1)


class Config:
    def __init__(self, data: Dict[str, Any]):
        self._data = data

    # -- Server --
    @property
    def server_host(self) -> str:
        return self._data.get("server", {}).get("host", "batmud.bat.org")

    @property
    def server_port(self) -> int:
        return int(self._data.get("server", {}).get("port", 23))

    # -- Web --
    @property
    def web_host(self) -> str:
        return self._data.get("web", {}).get("host", "127.0.0.1")

    @property
    def web_port(self) -> int:
        return int(self._data.get("web", {}).get("port", 8080))

    # -- Baidu API --
    @property
    def baidu_app_id(self) -> str:
        return self._data.get("baidu", {}).get("app_id", "")

    @property
    def baidu_secret_key(self) -> str:
        return self._data.get("baidu", {}).get("secret_key", "")

    @property
    def baidu_timeout(self) -> int:
        return int(self._data.get("baidu", {}).get("timeout", 15))

    @property
    def baidu_model_type(self) -> str:
        return self._data.get("baidu", {}).get("model_type", "llm")

    # -- Translation --
    @property
    def translation_enabled(self) -> bool:
        return bool(self._data.get("translation", {}).get("enabled", True))

    @property
    def cache_size(self) -> int:
        return int(self._data.get("translation", {}).get("cache_size", 2000))

    @property
    def cache_file(self) -> str:
        return self._data.get("translation", {}).get("cache_file", "translation_cache.db")

    @property
    def min_chars(self) -> int:
        return int(self._data.get("translation", {}).get("min_chars", 4))

    @property
    def max_batch(self) -> int:
        return int(self._data.get("translation", {}).get("max_batch", 3))

    # -- Logging --
    @property
    def log_level(self) -> str:
        return self._data.get("logging", {}).get("level", "INFO")

    @property
    def log_file(self) -> str:
        return self._data.get("logging", {}).get("file", "")

    @property
    def show_translations(self) -> bool:
        return bool(self._data.get("logging", {}).get("show_translations", True))

    def validate(self) -> list[str]:
        errors = []
        if not self.baidu_app_id:
            errors.append("config.yaml: baidu.app_id is required")
        if not self.baidu_secret_key:
            errors.append("config.yaml: baidu.secret_key is required")
        return errors


def _is_missing_credentials(config: Config) -> bool:
    """检查是否缺少有效的 API 凭据（空值或占位符）"""
    app_id = config.baidu_app_id.strip('"\' ')
    secret = config.baidu_secret_key.strip('"\' ')
    if not app_id or not secret:
        return True
    # 占位符检测
    placeholders = ['替换', '你的', '填入', 'app', 'secret', '123456']
    for ph in placeholders:
        if ph.lower() in app_id.lower() or ph.lower() in secret.lower():
            return True
    return False


def _create_default_config(path: str):
    """在指定路径创建默认 config.yaml"""
    content = """# ============================================================
# BatMUD CN - 配置文件
# ============================================================

# ---- 远程 BatMUD 服务器 ----
server:
  host: batmud.bat.org
  port: 23

# ---- Web 服务器 (浏览器访问地址) ----
web:
  host: 127.0.0.1
  port: 8080

# ---- 百度大模型翻译 API ----
# 从 https://fanyi-api.baidu.com/ 获取
# 需开通"大模型文本翻译API"服务
baidu:
  app_id: ""  # 首次运行时会引导输入
  secret_key: ""  # 首次运行时会引导输入
  model_type: "llm"
  timeout: 15

# ---- 翻译设置 ----
translation:
  enabled: true
  cache_size: 2000
  cache_file: "translation_cache.db"
  min_chars: 4
  max_batch: 3

# ---- 日志 ----
logging:
  level: INFO
  file: ""
  show_translations: true
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _prompt_credentials(config_path: str):
    """交互式引导用户输入 API 凭据并写入 config.yaml"""
    print()
    print("=" * 55)
    print("  首次运行 — 请配置百度大模型翻译 API 凭据")
    print("=" * 55)
    print()
    print("  需要开通「大模型文本翻译API」服务（免费）")
    print("  开通地址: https://fanyi-api.baidu.com/")
    print()
    print("  如果还没有 API 凭据，请先访问上面的链接注册并开通。")
    print()
    print("-" * 55)

    app_id = input("  APP ID: ").strip()
    while not app_id:
        app_id = input("  APP ID 不能为空，请重新输入: ").strip()

    secret_key = input("  密钥 (Secret Key): ").strip()
    while not secret_key:
        secret_key = input("  密钥不能为空，请重新输入: ").strip()

    # 读取原始文件
    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 替换 app_id 和 secret_key（保留原有引号格式）
    import re
    content = re.sub(
        r'(app_id:\s*)"[^"]*"',
        f'\\1"{app_id}"',
        content,
    )
    content = re.sub(
        r'(secret_key:\s*)"[^"]*"',
        f'\\1"{secret_key}"',
        content,
    )

    with open(config_path, "w", encoding="utf-8") as f:
        f.write(content)

    print()
    print("  凭据已保存到 config.yaml")
    print("=" * 55)
    print()


def load_config(config_path: str = None) -> Config:
    if config_path is None:
        # 仅在 exe 同目录查找 config.yaml
        cwd_config = Path.cwd() / "config.yaml"
        if cwd_config.exists():
            config_path = str(cwd_config)
        elif getattr(sys, 'frozen', False):
            # 打包模式：从内置模板创建 config.yaml
            _create_default_config(str(cwd_config))
            config_path = str(cwd_config)
        else:
            # 源码模式：从项目目录查找
            project_config = Path(__file__).resolve().parent.parent / "config.yaml"
            if project_config.exists():
                config_path = str(project_config)
            else:
                print("ERROR: config.yaml not found")
                sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        print("ERROR: config.yaml is empty or invalid")
        sys.exit(1)

    config = Config(data)

    # 首次运行或凭据为空 → 交互式输入
    if _is_missing_credentials(config):
        _prompt_credentials(config_path)
        # 重新加载
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        config = Config(data)

    # 输入后再次校验
    errors = config.validate()
    if errors:
        print("=" * 50)
        print("Config errors:")
        for e in errors:
            print(f"  x {e}")
        print("=" * 50)
        sys.exit(1)

    return config
