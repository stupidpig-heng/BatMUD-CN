"""
百度大模型文本翻译 API 客户端
- 模型: model_type='llm' (大模型翻译)
- 鉴权: MD5 签名 (兼容 sign 方式)
- 两级翻译缓存: 内存 LRU + SQLite 磁盘持久化
- 翻译指令 (reference): MUD 游戏语境优化
- 速率控制 (QPS 限制)

API 文档: https://fanyi-api.baidu.com/ait/api/aiTextTranslate
"""

import asyncio
import hashlib
import logging
import random
import sqlite3
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import aiohttp

logger = logging.getLogger("batmud.translator")

# 百度大模型翻译 API 端点
TRANSLATE_URL = "https://fanyi-api.baidu.com/ait/api/aiTextTranslate"

# QPS 限制
DEFAULT_QPS_LIMIT = 5

# MUD 游戏翻译指令（大模型专用）
MUD_REFERENCE = (
    "你是一个专业的 MUD (Multi-User Dungeon) 游戏翻译助手。请遵循以下规则："
    "1. 保留所有数字、标点符号和特殊格式"
    "2. 游戏专有名词保持翻译一致性（种族、职业、技能、装备名）"
    "3. 战斗描述要生动有力（如 slash→猛砍, dodge→闪避, critical→致命一击）"
    "4. 保持原文氛围：探索的神秘感、战斗的紧张感、NPC对话的个性"
    "5. 方向词简短（north→北, exit→出口）"
    "6. 翻译要简洁，适合实时游戏阅读"
    "7. 如果原文已是中文或无法翻译的内容，原样返回"
)


class LRUCache:
    """LRU 缓存"""

    def __init__(self, max_size: int = 2000):
        self._cache = OrderedDict()
        self._max_size = max_size

    def get(self, key: str) -> Optional[str]:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, key: str, value: str):
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
        self._cache[key] = value

    def __len__(self):
        return len(self._cache)

    @property
    def max_size(self):
        return self._max_size


class PersistentCache:
    """SQLite 磁盘持久化翻译缓存，重启后保留"""

    def __init__(self, db_path: str = "translation_cache.db"):
        self._db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None
        self._write_count = 0

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS translations "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_key ON translations(key)"
            )
            self._conn.commit()
        return self._conn

    def get(self, key: str) -> Optional[str]:
        try:
            row = self.conn.execute(
                "SELECT value FROM translations WHERE key = ?", (key,)
            ).fetchone()
            return row[0] if row else None
        except Exception:
            return None

    def put(self, key: str, value: str):
        try:
            self.conn.execute(
                "INSERT OR REPLACE INTO translations(key, value) VALUES (?, ?)",
                (key, value),
            )
            self._write_count += 1
            # 每 50 次写入提交一次，平衡性能与持久性
            if self._write_count % 50 == 0:
                self.conn.commit()
        except Exception:
            pass

    def flush(self):
        """强制落盘"""
        if self._conn is not None:
            try:
                self._conn.commit()
            except Exception:
                pass

    def stats(self) -> dict:
        try:
            row = self.conn.execute("SELECT COUNT(*) FROM translations").fetchone()
            size = row[0] if row else 0
        except Exception:
            size = -1
        return {"path": str(self._db_path), "entries": size}

    def close(self):
        self.flush()
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


class Translator:
    """百度大模型文本翻译 API 客户端

    鉴权: MD5 签名  sign = MD5(appid + q + salt + secret_key)
    模型: model_type='llm' (大模型翻译，默认)
    翻译指令: reference 参数用于定制翻译风格
    """

    def __init__(
        self,
        app_id: str,
        secret_key: str,
        model_type: str = "llm",
        reference: str = MUD_REFERENCE,
        timeout: int = 10,
        cache_size: int = 2000,
        cache_file: str = "translation_cache.db",
    ):
        self.app_id = app_id
        self.secret_key = secret_key
        self.model_type = model_type       # 'llm' 大模型 | 'nmt' 机器翻译
        self.reference = reference          # 翻译指令（仅 llm 模式生效）
        self.timeout = timeout
        self.cache = LRUCache(max_size=cache_size)
        self.disk_cache = PersistentCache(db_path=cache_file)

        self._session: Optional[aiohttp.ClientSession] = None
        self._last_request_time: float = 0
        self._min_interval: float = 1.0 / DEFAULT_QPS_LIMIT
        self._lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            t = aiohttp.ClientTimeout(total=self.timeout + 5)
            self._session = aiohttp.ClientSession(timeout=t)
        return self._session

    async def translate(self, text: str) -> str:
        """翻译单条文本（内存 → 磁盘 → API 三级查找）"""
        if not text or not text.strip():
            return text

        stripped = text.strip()
        if not any(c.isascii() and c.isalpha() for c in stripped):
            return text

        cache_key = self._make_key(text)

        # 1. 内存缓存
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        # 2. 磁盘缓存
        disk_cached = self.disk_cache.get(cache_key)
        if disk_cached is not None:
            self.cache.put(cache_key, disk_cached)  # 回填内存
            return disk_cached

        # 3. API 调用
        try:
            result = await self._call_api(text)
            self.cache.put(cache_key, result)
            self.disk_cache.put(cache_key, result)
            return result
        except Exception as e:
            logger.warning(f"翻译失败: {e}")
            return text

    async def translate_batch(self, texts: list[str]) -> list[str]:
        """批量翻译（内存 → 磁盘 → API）"""
        if not texts:
            return []

        need_trans = []
        results = {}

        for t in texts:
            if not t.strip() or not any(c.isascii() and c.isalpha() for c in t.strip()):
                results[t] = t
                continue
            key = self._make_key(t)
            # 内存
            cached = self.cache.get(key)
            if cached is not None:
                results[t] = cached
                continue
            # 磁盘
            disk_cached = self.disk_cache.get(key)
            if disk_cached is not None:
                self.cache.put(key, disk_cached)
                results[t] = disk_cached
                continue
            need_trans.append(t)

        if not need_trans:
            return [results.get(t, t) for t in texts]

        combined = "\n".join(need_trans)

        try:
            combined_result = await self._call_api(combined)
            parts = combined_result.split("\n")
            while len(parts) < len(need_trans):
                parts.append(need_trans[len(parts)])
            parts = parts[:len(need_trans)]

            for orig, trans in zip(need_trans, parts):
                key = self._make_key(orig)
                self.cache.put(key, trans)
                self.disk_cache.put(key, trans)
                results[orig] = trans

        except Exception as e:
            logger.warning(f"批量翻译失败: {e}")
            for t in need_trans:
                results[t] = t

        return [results.get(t, t) for t in texts]

    async def _call_api(self, query: str) -> str:
        """调用百度大模型翻译 API"""
        salt = str(random.randint(10000, 99999))
        sign_str = self.app_id + query + salt + self.secret_key
        sign = hashlib.md5(sign_str.encode("utf-8")).hexdigest()

        params = {
            "q": query,
            "from": "en",
            "to": "zh",
            "appid": self.app_id,
            "salt": salt,
            "sign": sign,
            "model_type": self.model_type,
        }

        # 大模型翻译指令（仅 llm 模式生效）
        if self.model_type == "llm" and self.reference:
            params["reference"] = self.reference

        # QPS 速率控制
        async with self._lock:
            elapsed = time.time() - self._last_request_time
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_request_time = time.time()

        session = await self._get_session()

        async with session.get(TRANSLATE_URL, params=params) as resp:
            data = await resp.json()

        # 错误处理
        if "error_code" in data:
            code = str(data["error_code"])
            msg = data.get("error_msg", "unknown")
            err_map = {
                "52000": "成功(无结果)",
                "52001": "请求超时",
                "52002": "系统错误",
                "52003": "未授权 - 请检查 APPID 或是否已开通大模型翻译服务",
                "54000": "参数错误",
                "54001": "签名错误 - 请检查密钥",
                "54003": "访问频率受限",
                "54004": "账户余额不足",
                "54005": "长query请求频繁",
                "58000": "客户端IP非法",
                "58001": "译文语言不支持",
                "58002": "服务已关闭",
                "58004": "model_type 参数错误 - 需为 'llm' 或 'nmt'",
                "59002": "翻译指令(reference)过长",
                "59003": "请求文本过长",
                "59004": "QPS超限",
                "90107": "认证未通过",
            }
            friendly = err_map.get(code, msg)
            raise RuntimeError(f"翻译API错误 [{code}]: {friendly}")

        # 提取结果
        trans_result = data.get("trans_result", [])
        if trans_result:
            dst = trans_result[0].get("dst", query)
            return dst

        return query

    @staticmethod
    def _make_key(text: str) -> str:
        normalized = text.strip()
        if len(normalized) <= 100:
            return normalized
        h = hashlib.md5(normalized.encode()).hexdigest()[:8]
        return f"{normalized[:80]}#{h}"

    async def close(self):
        self.disk_cache.flush()
        self.disk_cache.close()
        if self._session and not self._session.closed:
            await self._session.close()

    @property
    def cache_stats(self) -> dict:
        disk = self.disk_cache.stats()
        return {
            "memory": len(self.cache),
            "memory_max": self.cache.max_size,
            "disk_entries": disk["entries"],
            "disk_path": disk["path"],
        }
