"""
Web 服务器 - HTTP + WebSocket
- 托管静态前端文件
- WebSocket 实时双向通信
- 每个浏览器连接创建一个 MUD 会话
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

import aiohttp
from aiohttp import web

from .mud_client import MudClient
from .translator import Translator
from .config_loader import Config

logger = logging.getLogger("batmud.web")

# 静态文件目录（兼容 PyInstaller 打包）
import sys as _sys
if getattr(_sys, 'frozen', False):
    STATIC_DIR = Path(_sys._MEIPASS) / "static"
else:
    STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# MIME 类型
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class GameSession:
    """一个浏览器 ↔ MUD 的会话

    管理 WebSocket 连接和 MudClient 的生命周期
    """

    def __init__(self, ws: web.WebSocketResponse, config: Config, translator: Optional[Translator]):
        self.ws = ws
        self.config = config
        self.translator = translator
        self.mud: Optional[MudClient] = None
        self._input_queue: asyncio.Queue = asyncio.Queue()

    async def run(self):
        """运行游戏会话"""
        # 创建 MUD 客户端
        self.mud = MudClient(
            translator=self.translator,
            on_output=self._on_mud_output,
            on_disconnect=self._on_mud_disconnect,
            min_chars=self.config.min_chars,
        )

        # 连接到游戏服务器
        try:
            await self.mud.connect(self.config.server_host, self.config.server_port)
        except Exception as e:
            logger.error(f"Failed to connect to game server: {e}")
            await self.ws.send_json({
                "type": "error",
                "data": f"无法连接到游戏服务器: {e}",
            })
            return

        # 发送连接成功消息
        await self.ws.send_json({
            "type": "status",
            "data": "connected",
        })

        # 并行处理: 读取 WebSocket 消息 + 处理输入队列
        try:
            async for msg in self.ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    await self._handle_ws_message(data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {self.ws.exception()}")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSE:
                    break
        except Exception as e:
            logger.error(f"WebSocket loop error: {e}")
        finally:
            await self.mud.disconnect()

    async def _handle_ws_message(self, data: dict):
        """处理来自浏览器的消息"""
        msg_type = data.get("type", "")

        if msg_type == "cmd":
            command = data.get("data", "")
            if command and self.mud:
                await self.mud.send(command)
        elif msg_type == "raw":
            # 直接发送原始字节（高级用法，保留）
            raw_data = data.get("data", "")
            if raw_data and self.mud and self.mud.writer:
                self.mud.writer.write(raw_data.encode("utf-8", errors="replace"))
                await self.mud.writer.drain()

    async def _on_mud_output(self, html: str, debug: dict = None):
        """MUD 服务端有数据 → 发送 HTML 到浏览器"""
        if self.ws.closed:
            return
        try:
            await self.ws.send_json({
                "type": "html",
                "data": html,
            })
            if debug:
                await self.ws.send_json({
                    "type": "debug",
                    "data": debug,
                })
        except Exception as e:
            logger.warning(f"WebSocket send error: {e}")

    async def _on_mud_disconnect(self):
        """MUD 服务器断开"""
        if not self.ws.closed:
            try:
                await self.ws.send_json({
                    "type": "status",
                    "data": "disconnected",
                })
            except Exception:
                pass


class WebServer:
    """Web 服务器

    提供 HTTP 静态文件服务和 WebSocket 游戏通信
    """

    def __init__(self, config: Config, translator: Optional[Translator]):
        self.config = config
        self.translator = translator
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None

    async def start(self):
        """启动 Web 服务器"""
        self._app = web.Application()
        self._app.router.add_get("/", self._handle_index)
        self._app.router.add_get("/ws", self._handle_websocket)
        # 通配路由: 匹配所有静态文件
        self._app.router.add_get("/{path:.*}", self._handle_static)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        host = self.config.web_host
        port = self.config.web_port
        site = web.TCPSite(self._runner, host, port)
        await site.start()

        logger.info(f"Web server started: http://{host}:{port}")

    async def stop(self):
        """停止 Web 服务器"""
        if self._runner:
            await self._runner.cleanup()

    async def _handle_index(self, request: web.Request) -> web.Response:
        """首页"""
        return await self._serve_file("index.html")

    async def _handle_static(self, request: web.Request) -> web.Response:
        """静态文件"""
        path = request.match_info.get("path", "index.html")
        if not path or path == "/":
            path = "index.html"
        return await self._serve_file(path)

    async def _serve_file(self, filename: str) -> web.Response:
        """服务静态文件"""
        # 安全检查：防止路径遍历
        filepath = (STATIC_DIR / filename).resolve()
        if not str(filepath).startswith(str(STATIC_DIR.resolve())):
            raise web.HTTPForbidden()

        if not filepath.exists():
            raise web.HTTPNotFound()

        ext = filepath.suffix.lower()
        content_type = MIME.get(ext, "application/octet-stream")

        return web.FileResponse(
            path=filepath,
            headers={"Cache-Control": "no-cache"},
        )

    async def _handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket 连接处理"""
        ws = web.WebSocketResponse(max_msg_size=0)
        await ws.prepare(request)

        logger.info(f"New WebSocket connection from {request.remote}")

        session = GameSession(ws, self.config, self.translator)
        await session.run()

        logger.info("WebSocket session ended")
        return ws
