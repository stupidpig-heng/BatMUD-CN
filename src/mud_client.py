"""
MUD Telnet 客户端
- 连接 BatMUD 服务器
- Telnet 协商 + MCCP 过滤
- 文本解析 + 翻译 + ANSI→HTML 转换
- 回调输出 HTML
"""

import asyncio
import logging
import re
from typing import Callable, Optional

from .parser import TextParser, ParsedLine, TelnetConstants
from .translator import Translator
from .ansi_html import ansi_to_html

logger = logging.getLogger("batmud.mud_client")

_COMPRESS_OPTIONS = {TelnetConstants.COMPRESS, TelnetConstants.COMPRESS2}

# 解析 ANSI SGR 参数码的正则
_SGR_CODE_RE = re.compile(rb'\x1b\[([\d;]*)m')

# ANSI 前景色 → CSS class 名映射 (与 ansi_html.py 保持一致)
_FG_CLASS = {
    30: 'c30', 31: 'c31', 32: 'c32', 33: 'c33',
    34: 'c34', 35: 'c35', 36: 'c36', 37: 'c37',
    90: 'c90', 91: 'c91', 92: 'c92', 93: 'c93',
    94: 'c94', 95: 'c95', 96: 'c96', 97: 'c97',
}
_BG_CLASS = {
    40: 'bg40', 41: 'bg41', 42: 'bg42', 43: 'bg43',
    44: 'bg44', 45: 'bg45', 46: 'bg46', 47: 'bg47',
    100: 'bg100', 101: 'bg101', 102: 'bg102', 103: 'bg103',
    104: 'bg104', 105: 'bg105', 106: 'bg106', 107: 'bg107',
}


def _extract_primary_style(ansi_segments, raw: bytes) -> dict:
    """按「覆盖文本字节数最多」的原则提取行的主色

    遍历行内所有 ANSI 状态切换，统计每种 (fg,bg,bold,...) 组合
    覆盖的文本字节数。返回覆盖字节最多的样式。

    这避免了高亮词（只占几个字节的颜色）溢出到整行翻译。
    """
    # 当前状态
    cur_fg = 37
    cur_bg = None
    cur_bold = False
    cur_underline = False
    cur_dim = False
    cur_blink = False
    cur_reverse = False

    # 统计每种状态覆盖的字节数
    # key: (fg, bg, bold, underline, dim, blink, reverse)
    state_bytes = {}

    def state_key(fg, bg, bold, underline, dim, blink, reverse):
        return (fg, bg, bold, underline, dim, blink, reverse)

    def apply_sgr_params(params_str):
        nonlocal cur_fg, cur_bg, cur_bold, cur_underline, cur_dim, cur_blink, cur_reverse
        if not params_str:
            params_str = '0'
        codes = [int(c) for c in params_str.split(';') if c]
        for c in codes:
            if c == 0:
                cur_fg, cur_bg = 37, None
                cur_bold = cur_underline = cur_dim = cur_blink = cur_reverse = False
            elif c == 1: cur_bold = True
            elif c == 2: cur_dim = True
            elif c == 4: cur_underline = True
            elif c == 5: cur_blink = True
            elif c == 7: cur_reverse = True
            elif c == 22: cur_bold = False
            elif c == 24: cur_underline = False
            elif c == 25: cur_blink = False
            elif c == 27: cur_reverse = False
            elif 30 <= c <= 37 or 90 <= c <= 97: cur_fg = c
            elif c == 39: cur_fg = 37
            elif 40 <= c <= 47 or 100 <= c <= 107: cur_bg = c
            elif c == 49: cur_bg = None

    # 按偏移量排序所有 ANSI 匹配
    matches = []
    for seg in ansi_segments:
        for match in _SGR_CODE_RE.finditer(seg.codes):
            matches.append((match.start(), match.group()))

    # 实际上 ansi_segments 里的 offset 是在整行 raw bytes 中的偏移
    # 但 _extract_ansi 存的是在 clean data 中的偏移...
    # 安全做法: 直接在 raw bytes 上扫描 ANSI 序列
    all_matches = list(_SGR_CODE_RE.finditer(raw))

    if not all_matches:
        # 没有 ANSI 码 → 全行默认色
        return {
            'fg': 37, 'bg': None,
            'bold': False, 'underline': False, 'dim': False, 'blink': False, 'reverse': False,
            'css_class': '', 'has_style': False,
        }

    pos = 0
    for match in all_matches:
        # 此 ANSI 码之前的文本归当前状态
        text_len = match.start() - pos
        if text_len > 0:
            key = state_key(cur_fg, cur_bg, cur_bold, cur_underline, cur_dim, cur_blink, cur_reverse)
            state_bytes[key] = state_bytes.get(key, 0) + text_len

        # 应用此 ANSI 码
        params_str = match.group(1).decode('ascii', errors='ignore')
        apply_sgr_params(params_str)
        pos = match.end()

    # 最后一段文本（最后一个 ANSI 码之后）
    text_len = len(raw) - pos
    if text_len > 0:
        # 去掉尾部 \r\n 计入
        stripped_len = len(raw.rstrip(b'\r\n')) - pos
        if stripped_len < 0:
            stripped_len = 0
        actual_len = max(text_len - (len(raw) - len(raw.rstrip(b'\r\n'))), 0)
        key = state_key(cur_fg, cur_bg, cur_bold, cur_underline, cur_dim, cur_blink, cur_reverse)
        state_bytes[key] = state_bytes.get(key, 0) + actual_len

    if not state_bytes:
        return {
            'fg': 37, 'bg': None,
            'bold': False, 'underline': False, 'dim': False, 'blink': False, 'reverse': False,
            'css_class': '', 'has_style': False,
        }

    # 找覆盖字节最多的状态
    dominant_key = max(state_bytes, key=state_bytes.get)
    fg, bg, bold, underline, dim, blink, reverse = dominant_key

    # 构建 CSS class
    classes = []
    if fg != 37:
        classes.append(_FG_CLASS.get(fg, ''))
    if bg is not None:
        classes.append(_BG_CLASS.get(bg, ''))
    if bold:
        classes.append('b')
    if underline:
        classes.append('u')
    if dim:
        classes.append('dim')
    if blink:
        classes.append('blink')
    if reverse:
        classes.append('rev')

    css_class = ' '.join(c for c in classes if c)

    return {
        'fg': fg, 'bg': bg,
        'bold': bold, 'underline': underline, 'dim': dim, 'blink': blink, 'reverse': reverse,
        'css_class': css_class,
        'has_style': bool(css_class),
    }


class MudClient:
    """MUD 游戏客户端

    Usage:
        async def on_output(html: str):
            # send HTML to WebSocket
            pass

        client = MudClient(translator, on_output)
        await client.connect("batmud.bat.org", 23)
        await client.send("look\r\n")
    """

    def __init__(
        self,
        translator: Optional[Translator],
        on_output: Callable,
        on_disconnect: Callable = None,
        min_chars: int = 4,
    ):
        self.translator = translator
        self.on_output = on_output
        self.on_disconnect = on_disconnect
        self.min_chars = min_chars

        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.parser = TextParser(min_chars=min_chars)
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._pending_msg: Optional[ParsedLine] = None  # 跨 chunk 的未完成消息首行

    async def connect(self, host: str, port: int, timeout: float = 15):
        logger.info(f"Connecting to {host}:{port}...")
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        logger.info("Connected to BatMUD server")
        self._running = True
        task = asyncio.create_task(self._read_loop())
        self._tasks.append(task)

    async def send(self, data: str):
        if self.writer and self._running:
            self.writer.write(data.encode("utf-8", errors="replace"))
            await self.writer.drain()

    async def disconnect(self):
        self._running = False
        if self.writer:
            try:
                self.writer.close()
            except OSError:
                pass
        for task in self._tasks:
            if not task.done():
                task.cancel()
        self._tasks.clear()

    async def _read_loop(self):
        try:
            while self._running and self.reader:
                data = await self.reader.read(4096)
                if not data:
                    logger.info("Server closed connection")
                    break

                logger.info(f"[RAW_IN] len={len(data)}")
                logger.info(f"[RAW_IN] hex={data.hex(' ')}")
                logger.info(f"[RAW_IN] repr={repr(data)}")

                filtered = self._filter_compress(data)
                if not filtered:
                    continue

                parsed = self.parser.feed(filtered)
                parsed = self._merge_message_lines(parsed)
                for line in parsed:
                    try:
                        await self._process_line(line)
                    except Exception as e:
                        logger.error(f"Process line error: {e}", exc_info=True)

        except asyncio.CancelledError:
            pass
        except OSError as e:
            logger.error(f"Read error: {e}")
        except Exception as e:
            logger.error(f"Read loop error: {e}", exc_info=True)
        finally:
            self._running = False
            if self.on_disconnect:
                try:
                    await self.on_disconnect()
                except Exception:
                    pass

    async def _process_line(self, line: ParsedLine):
        raw = line.raw_bytes

        if line.is_telnet:
            return
        if not raw.strip():
            await self.on_output('\n', None)
            return

        # 构建调试信息
        debug = {
            'raw_hex': raw.hex(' '),
            'raw_repr': repr(raw),
            'ansi_count': len(line.ansi_segments),
            'text': line.text,
            'skip': line.skip_translation,
            'is_prompt': line.is_prompt,
        }

        if line.skip_translation or self.translator is None:
            # 不需要翻译: 直接 ANSI → HTML
            html = ansi_to_html(raw)
            debug['mode'] = 'passthrough'
            debug['html'] = html
        else:
            debug['mode'] = 'translate'

            # ---- 逐段翻译，保留 ANSI 颜色边界 ----
            # 1. 按 ANSI SGR 码拆分原始字节流
            segments = _split_raw_by_ansi(raw)

            # 2. 收集所有需要翻译的文本段（跳过 ≤2 字符的短指令如 i/s/n/eq）
            #    也跳过字母占比 < 40% 的段 — 这些通常是 ASCII 地图/字符画
            text_indices = []   # (seg_index, text_str)
            for idx, (is_ansi, data) in enumerate(segments):
                if not is_ansi:
                    text = data.decode('utf-8', errors='replace')
                    stripped = text.rstrip('\r\n')
                    stripped_clean = stripped.strip()
                    if not stripped_clean or len(stripped_clean) <= 2:
                        continue
                    alpha_count = sum(1 for c in stripped_clean if c.isascii() and c.isalpha())
                    if alpha_count == 0:
                        continue
                    # 字母占比过低 → 地图字符画/分隔线，跳过翻译
                    if alpha_count < len(stripped_clean) * 0.4:
                        continue
                    text_indices.append((idx, stripped))

            # 3. 逐段翻译（使用缓存）
            trans_map = {}
            for idx, stripped in text_indices:
                if stripped not in trans_map:
                    try:
                        trans_map[stripped] = await self.translator.translate(stripped)
                    except Exception:
                        trans_map[stripped] = stripped

            debug['segments'] = len(segments)
            debug['translated_segs'] = len(trans_map)
            debug['trans_map'] = {k: v for k, v in list(trans_map.items())[:5]}

            # 4. 重建字节流: 跟踪当前 ANSI 状态，决定每段的输出格式
            #    默认色 → 只输出中文
            #    有色   → 输出 English(中文)，颜色自然包裹
            state = {'fg': 37, 'bg': None, 'bold': False,
                     'underline': False, 'dim': False, 'blink': False, 'reverse': False}

            out = bytearray()
            for is_ansi, data in segments:
                if is_ansi:
                    out.extend(data)
                    # 更新 ANSI 状态
                    match = _SGR_CODE_RE.match(data)
                    if match:
                        params_str = match.group(1).decode('ascii', errors='ignore')
                        _apply_sgr_state(params_str, state)
                else:
                    text = data.decode('utf-8', errors='replace')
                    stripped = text.rstrip('\r\n')
                    trailing = text[len(stripped):]

                    translated = trans_map.get(stripped, stripped)
                    has_style = not _state_is_default(state)
                    has_alpha = any(c.isascii() and c.isalpha() for c in stripped)

                    if has_style and stripped.strip() and has_alpha and len(stripped.strip()) > 2:
                        # 有色文本(>2字符) → English(中文)
                        # 但若翻译结果与原文相同（未实际翻译/地图符号），只输出原文
                        if stripped != translated:
                            out.extend(f'{stripped}({translated})'.encode('utf-8', errors='replace'))
                        else:
                            out.extend(stripped.encode('utf-8', errors='replace'))
                    else:
                        # 默认色文本 → 只输出中文
                        out.extend(translated.encode('utf-8', errors='replace'))
                    out.extend(trailing.encode('utf-8', errors='replace'))

            # 5. 转换为 HTML
            html = ansi_to_html(bytes(out))
            debug['html'] = html

        await self.on_output(html, debug)

    @staticmethod
    def _has_unbalanced_ansi(line: ParsedLine) -> bool:
        """检测一行是否以未关闭的 ANSI 状态结尾（opener 多于 closer）"""
        raw = line.raw_bytes
        all_sgr = _SGR_CODE_RE.findall(raw)
        if not all_sgr:
            return False
        closers = sum(1 for m in all_sgr if m == b'0' or m == b'')
        openers = len(all_sgr) - closers
        return openers > closers

    @staticmethod
    def _is_continuation_line(line: ParsedLine) -> bool:
        """检测一行是否为续行（硬折行产生的缩进行或 ANSI 续行）"""
        if line.is_telnet or line.is_prompt or line.is_gmcp:
            return False
        if line.skip_translation:
            return False
        raw = line.raw_bytes

        # 模式1: 以空格缩进开头（频道消息续行）
        if raw.startswith(b' '):
            text = line.text.lstrip(' ')
            return len(text) > 0 and any(c.isascii() and c.isalpha() for c in text)

        # 模式2: 以 ANSI SGR 码 + 空格开头（NPC 对话续行）
        m = _SGR_CODE_RE.match(raw)
        if m and m.end() < len(raw) and raw[m.end():m.end()+1] == b' ':
            after = raw[m.end():].lstrip(b' ')
            return len(after) > 0

        return False

    def _merge_line_list(self, lines: list[ParsedLine]) -> ParsedLine:
        """将多个 ParsedLine 合并为一个"""
        if len(lines) == 1:
            return lines[0]

        # 合并 raw_bytes: 首行去尾 \r\n，续行去尾 \r\n，首字符非字母则去首空格，用空格连接
        merged_raw = bytearray()
        merged_raw.extend(lines[0].raw_bytes.rstrip(b'\r\n'))
        for line in lines[1:]:
            raw = line.raw_bytes
            raw = raw.rstrip(b'\r\n')

            # 去除 MUD 硬折行产生的 ANSI 断点：
            # 首行尾 \x1b[0m + 续行首 \x1b[<SGR>m → 只保留空格连接，
            # 避免翻译时句子被 ANSI 边界拆成两段独立翻译
            if merged_raw.endswith(b'\x1b[0m'):
                sgr_match = _SGR_CODE_RE.match(raw)
                if sgr_match:
                    merged_raw = merged_raw[:-len(b'\x1b[0m')]
                    raw = raw[sgr_match.end():]

            # 如果续行以空格开头（缩进续行），去掉缩进空格
            if raw.startswith(b' '):
                raw = raw.lstrip(b' ')
            merged_raw.extend(b' ')
            merged_raw.extend(raw)
        merged_raw.extend(b'\r\n')

        # 用临时 parser 重解析（避免干扰主 parser 的内部缓冲状态）
        temp_parser = TextParser(min_chars=self.min_chars)
        parsed_list = temp_parser.feed(bytes(merged_raw))
        if parsed_list:
            merged = parsed_list[0]
            merged.is_prompt = False
            merged.is_gmcp = False
            merged.is_telnet = False
            return merged
        # 极低概率 fallback：手动构造
        merged_text = ' '.join(l.text.strip() for l in lines)
        return ParsedLine(
            text=merged_text,
            raw_bytes=bytes(merged_raw),
            skip_translation=False,
        )

    def _merge_message_lines(self, lines: list[ParsedLine]) -> list[ParsedLine]:
        """合并 MUD 服务端硬折行产生的续行

        两类触发条件：
        1. 上一行 ANSI 状态未平衡（opener 多于 closer）
        2. 当前行匹配续行模式（缩进空格 / ANSI+空格开头）
        """
        if not lines:
            return lines

        # 跨 chunk：把上一轮缓冲的行前置
        if self._pending_msg is not None:
            lines.insert(0, self._pending_msg)
            self._pending_msg = None

        result = []
        idx = 0
        n = len(lines)

        while idx < n:
            line = lines[idx]

            if line.is_telnet:
                result.append(line)
                idx += 1
                continue

            # 跳过不应合并的行
            if line.is_prompt or line.is_gmcp:
                result.append(line)
                idx += 1
                continue

            # 开始一个合并组
            group = [line]
            idx += 1

            while idx < n:
                nxt = lines[idx]

                if nxt.is_telnet:
                    break
                if nxt.is_prompt or nxt.is_gmcp:
                    break

                # 判断是否续行
                prev = group[-1]
                is_cont = False

                # 条件1: 前一行的 ANSI 未平衡
                if self._has_unbalanced_ansi(prev):
                    is_cont = True
                # 条件2: 当前行匹配续行模式
                elif self._is_continuation_line(nxt):
                    is_cont = True

                if is_cont:
                    group.append(nxt)
                    idx += 1
                else:
                    break

            if len(group) > 1:
                merged = self._merge_line_list(group)
                result.append(merged)
            else:
                result.append(group[0])

        # 跨 chunk 缓冲：末行有未关闭 ANSI 则留给下一 chunk
        if result:
            last = result[-1]
            if (self._has_unbalanced_ansi(last)
                    and not last.is_prompt
                    and not last.is_gmcp):
                self._pending_msg = result.pop()

        return result

    @staticmethod
    def _filter_compress(data: bytes) -> bytes:
        result = bytearray()
        i = 0
        n = len(data)
        while i < n:
            if data[i] == TelnetConstants.IAC and i + 2 < n:
                cmd = data[i + 1]
                opt = data[i + 2]
                if opt in _COMPRESS_OPTIONS:
                    if cmd == TelnetConstants.SB:
                        end = data.find(
                            bytes([TelnetConstants.IAC, TelnetConstants.SE]), i + 3
                        )
                        if end != -1:
                            i = end + 2
                            continue
                    i += 3
                    continue
                if cmd == TelnetConstants.SB:
                    end = data.find(
                        bytes([TelnetConstants.IAC, TelnetConstants.SE]), i + 3
                    )
                    if end != -1:
                        result.extend(data[i : end + 2])
                        i = end + 2
                        continue
                result.extend(data[i : i + 3])
                i += 3
            else:
                result.append(data[i])
                i += 1
        return bytes(result)


def _apply_sgr_state(params_str: str, state: dict):
    """将一个 ANSI SGR 参数序列应用到状态字典（原地修改）"""
    if not params_str:
        params_str = '0'
    codes = [int(c) for c in params_str.split(';') if c]
    for c in codes:
        if c == 0:
            state['fg'], state['bg'] = 37, None
            state['bold'] = state['underline'] = state['dim'] = state['blink'] = state['reverse'] = False
        elif c == 1: state['bold'] = True
        elif c == 2: state['dim'] = True
        elif c == 4: state['underline'] = True
        elif c == 5: state['blink'] = True
        elif c == 7: state['reverse'] = True
        elif c == 22: state['bold'] = False
        elif c == 24: state['underline'] = False
        elif c == 25: state['blink'] = False
        elif c == 27: state['reverse'] = False
        elif 30 <= c <= 37: state['fg'] = c
        elif c == 39: state['fg'] = 37
        elif 40 <= c <= 47: state['bg'] = c
        elif c == 49: state['bg'] = None
        elif 90 <= c <= 97: state['fg'] = c
        elif 100 <= c <= 107: state['bg'] = c


def _state_is_default(state: dict) -> bool:
    """当前 ANSI 状态是否为默认（无色无样式）"""
    return (
        state['fg'] == 37 and state['bg'] is None
        and not state['bold'] and not state['underline']
        and not state['dim'] and not state['blink'] and not state['reverse']
    )


def _split_raw_by_ansi(raw: bytes) -> list:
    """按 ANSI SGR 序列边界拆分字节流

    Returns:
        list of (is_ansi: bool, data: bytes) tuples
        is_ansi=True 表示这是一个 ANSI 转义序列
        is_ansi=False 表示这是普通文本
    """
    segments = []
    pos = 0
    for match in _SGR_CODE_RE.finditer(raw):
        start, end = match.start(), match.end()
        if start > pos:
            segments.append((False, raw[pos:start]))
        segments.append((True, match.group()))
        pos = end
    if pos < len(raw):
        segments.append((False, raw[pos:]))
    return segments


# 玩家对话模式: "Name says:" / "Name tells you:" / "Name asks '...'"
_DIALOGUE_RE = re.compile(
    r'(?:says?|tells?(?:\s+you)?|asks?|exclaims?|shouts?|whispers?)'
    r'(?:[:,]\s|\s\')',
    re.IGNORECASE,
)
_DIALOGUE_BRACKET_RE = re.compile(r'\{[^}]*\}[:,]|\([^)]*\)[:,]')

def _is_player_dialogue(text: str) -> bool:
    """检测一行文本是否为玩家/NPC 对话"""
    if not text or len(text) > 120:
        return False
    # 带花括号/圆括号的名字: "Name {ghost}: ..." 或 "Name (race): ..."
    if _DIALOGUE_BRACKET_RE.search(text):
        return True
    # "Name says/tells/asks: ..."
    if _DIALOGUE_RE.search(text):
        return True
    return False
