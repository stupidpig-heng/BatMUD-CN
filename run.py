"""BatMUD CN — 程序入口（供 PyInstaller 打包）"""
import sys
from pathlib import Path

# 确保 src 在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.main import main
import asyncio

if __name__ == "__main__":
    asyncio.run(main())
