"""
BatMUD CN — Web Client + Translation Engine
Starts the web server (HTTP + WebSocket + Telnet client + Translator)
"""

import asyncio
import logging
import signal
import sys

from .config_loader import load_config
from .translator import Translator
from .web_server import WebServer


def setup_logging(config):
    fmt = logging.Formatter(
        '%(asctime)s [%(levelname)-5s] %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)

    root = logging.getLogger("batmud")
    root.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))
    root.addHandler(console)
    root.propagate = False

    if config.log_file:
        fh = logging.FileHandler(config.log_file, encoding='utf-8')
        fh.setFormatter(fmt)
        root.addHandler(fh)


async def main():
    print()
    print("=" * 50)
    print("  BatMUD CN - Web Client + Translation")
    print("  LLM-Powered by Baidu AI Translate")
    print("=" * 50)
    print()

    config = load_config()
    setup_logging(config)
    logger = logging.getLogger("batmud")

    # Init translator
    translator = None
    if config.translation_enabled:
        model_label = "LLM (大模型)" if config.baidu_model_type == "llm" else "NMT (机器翻译)"
        logger.info(f"Initializing Baidu Translator [{model_label}]...")
        translator = Translator(
            app_id=config.baidu_app_id,
            secret_key=config.baidu_secret_key,
            model_type=config.baidu_model_type,
            timeout=config.baidu_timeout,
            cache_size=config.cache_size,
            cache_file=config.cache_file,
        )
        try:
            result = await translator.translate("hello world")
            if result and result != "hello world":
                logger.info(f"Baidu LLM API OK. Test: hello world -> {result}")
            else:
                logger.warning("Translation test unexpected, continuing...")
        except Exception as e:
            logger.error(f"Baidu API init failed: {e}")
            logger.error("请确认已开通「大模型文本翻译API」服务")
            logger.error("开通链接: https://fanyi-api.baidu.com/")
            await translator.close()
            sys.exit(1)

    # Start web server
    server = WebServer(config, translator)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def signal_handler():
        logger.info("Shutting down...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            signal.signal(sig, lambda s, f: signal_handler())

    await server.start()
    logger.info("=" * 50)
    logger.info(f"Open your browser: http://{config.web_host}:{config.web_port}")
    logger.info("The game will auto-connect when you open the page.")
    logger.info("Press Ctrl+C to stop.")
    logger.info("=" * 50)

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        pass

    await server.stop()
    if translator:
        stats = translator.cache_stats
        logger.info(f"Cache: {stats['size']} entries")
        await translator.close()
    logger.info("Goodbye!")


if __name__ == "__main__":
    asyncio.run(main())
