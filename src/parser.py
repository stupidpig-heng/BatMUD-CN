"""
文本解析器 - 处理 MUD 文本流
- Telnet IAC 序列识别与透传
- ANSI 转义序列提取
- 行缓冲与分句
- Prompt 智能检测
- GMCP/MSDP 结构化数据识别
"""

import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logger = logging.getLogger("batmud.parser")

# ---- ANSI 转义序列正则 ----
# 匹配 ANSI SGR (Select Graphic Rendition): \x1b[NN;NN;...m
ANSI_SGR_RE = re.compile(rb'\x1b\[[\d;]*m')
# 匹配其他 ANSI 序列 (cursor movement, clear screen, etc.)
ANSI_OTHER_RE = re.compile(rb'\x1b\[[\d;]*[A-HJKSTfnsu]|\x1b\[\?[\d;]*[hl]|\x1b\[[=?]\d+[hl]?')
# 综合 ANSI 匹配
ANSI_RE = re.compile(rb'\x1b\[[\d;]*[A-HJKSTfhlmnstu]|\x1b\[\?[\d;]*[hl]|\x1b\[[=?]\d+[hl]?')

# ---- Prompt 检测模式 ----
# 典型 MUD prompt: "Hp:1234/1234 Sp:567/567 Ep:89/100 >"
PROMPT_PATTERNS = [
    # HP/SP/EP/Mana 模式
    re.compile(rb'(?:Hp|HP|hp|Hit\s*points?)[:\s]*\d+/\d+', re.IGNORECASE),
    re.compile(rb'(?:Sp|SP|sp|Spell|Mana|MP|mp)[:\s]*\d+/\d+', re.IGNORECASE),
    re.compile(rb'(?:Ep|EP|ep|Endur|Sta|SP|sp)[:\s]*\d+/\d+', re.IGNORECASE),
    # 百分号进度条
    re.compile(rb'\[\s*\d+%\s*\]'),
    re.compile(rb'\d+%'),
    # 经验值行
    re.compile(rb'(?:Exp|XP|exp|xp)[:\s]*\d+'),
    # 金币/金钱
    re.compile(rb'(?:Gold|Gp|gp|Money)[:\s]*\d+'),
    # 数字后跟 > 或 ] 结尾的短行（如 "Exp:356 >"），要求前面有数字避免误判
    re.compile(rb'\d\s*[>\]]\s*$'),
]


@dataclass
class ANSISegment:
    """一个 ANSI 段: 偏移量和原始字节"""
    offset: int      # 在原始字节流中的偏移
    codes: bytes     # 原始 ANSI 转义字节


@dataclass
class ParsedLine:
    """解析后的文本行"""
    text: str                     # 纯文本 (去除 ANSI 和 telnet 控制码)
    raw_bytes: bytes              # 原始字节 (含 ANSI)
    ansi_segments: List[ANSISegment] = field(default_factory=list)
    is_telnet: bool = False       # 是否为 telnet 协商数据
    is_prompt: bool = False       # 是否为状态提示行
    is_gmcp: bool = False         # 是否为 GMCP/MSDP 数据
    skip_translation: bool = False  # 是否跳过翻译


class TelnetConstants:
    """Telnet 协议常量 (RFC 854)"""
    IAC  = 255  # Interpret As Command
    DONT = 254
    DO   = 253
    WONT = 252
    WILL = 251
    SB   = 250  # Subnegotiation Begin
    SE   = 240  # Subnegotiation End
    # Telnet 选项
    ECHO    = 1
    SGA     = 3    # Suppress Go Ahead
    TTYPE   = 24   # Terminal Type
    NAWS    = 31   # Negotiate About Window Size
    LINEMODE = 34
    NEW_ENVIRON = 39
    CHARSET = 42
    # MUD 扩展
    COMPRESS = 85   # MCCP
    COMPRESS2 = 86  # MCCP v2
    MSP    = 90   # MUD Sound Protocol
    MXP    = 91   # MUD eXtension Protocol
    GMCP   = 201  # Generic Mud Communication Protocol
    MSDP   = 69   # MUD Server Data Protocol


class TextParser:
    """MUD 文本流解析器

    处理从服务端接收的字节流:
    1. 提取并透传 telnet IAC 序列
    2. 提取 ANSI 转义序列
    3. 缓冲文本直到形成完整行
    4. 检测 prompt 行、GMCP 数据等
    """

    def __init__(self, min_chars: int = 4):
        self.min_chars = min_chars
        self._buffer = bytearray()
        self._telnet_buffer = bytearray()  # 正在构建的 telnet 子协商

    def feed(self, data: bytes) -> List[ParsedLine]:
        """输入原始字节，返回解析出的行列表

        Args:
            data: 从服务端接收的原始字节

        Returns:
            解析出的 ParsedLine 列表
        """
        results: List[ParsedLine] = []
        i = 0

        while i < len(data):
            byte = data[i]

            # ---- Telnet IAC 处理 ----
            if byte == TelnetConstants.IAC:
                iac_end = self._find_iac_end(data, i)
                if iac_end is None:
                    # IAC 序列不完整，缓冲剩余数据
                    self._telnet_buffer.extend(data[i:])
                    break

                iac_data = data[i:iac_end]
                # 先输出缓冲区中已有的文本
                if len(self._buffer) > 0:
                    lines = self._flush_buffer()
                    results.extend(lines)

                # 将 IAC 数据作为特殊行输出
                results.append(ParsedLine(
                    text="",
                    raw_bytes=bytes(iac_data),
                    is_telnet=True,
                    skip_translation=True,
                ))
                i = iac_end
                continue

            # ---- 处理缓冲的 telnet 数据 ----
            if len(self._telnet_buffer) > 0:
                self._telnet_buffer.append(byte)
                # 检查是否形成完整的 IAC 序列
                if len(self._telnet_buffer) >= 2:
                    iac_end = self._find_iac_end(bytes(self._telnet_buffer), 0)
                    if iac_end is not None:
                        results.append(ParsedLine(
                            text="",
                            raw_bytes=bytes(self._telnet_buffer[:iac_end]),
                            is_telnet=True,
                            skip_translation=True,
                        ))
                        self._telnet_buffer.clear()
                i += 1
                continue

            # ---- 普通字节 ----
            self._buffer.append(byte)

            # 检测行结束
            if byte == ord('\n'):
                # 行结束，刷新缓冲区
                lines = self._flush_buffer()
                results.extend(lines)

            i += 1

        return results

    def flush(self) -> List[ParsedLine]:
        """强制刷新缓冲区中的所有剩余数据"""
        results = []
        if len(self._buffer) > 0:
            results.extend(self._flush_buffer())
        return results

    def _flush_buffer(self) -> List[ParsedLine]:
        """将缓冲区内容解析为一行或多行"""
        if len(self._buffer) == 0:
            return []

        raw = bytes(self._buffer)
        self._buffer.clear()

        # 去掉尾部的 \r\n 或 \n
        clean = raw.rstrip(b'\r\n')

        if len(clean) == 0:
            # 空行
            return [ParsedLine(text="", raw_bytes=raw, skip_translation=True)]

        # 提取 ANSI 段
        ansi_segments = self._extract_ansi(clean)

        # 获取纯文本
        text = self._strip_ansi(clean).decode('utf-8', errors='replace')

        # 检测是否为 prompt
        is_prompt = self._detect_prompt(clean)

        # 检测是否为 GMCP/MSDP
        is_gmcp = self._detect_gmcp(clean)

        # 是否跳过翻译
        skip = (
            len(text.strip()) < self.min_chars
            or is_prompt
            or is_gmcp
        )

        return [ParsedLine(
            text=text,
            raw_bytes=raw,
            ansi_segments=ansi_segments,
            is_prompt=is_prompt,
            is_gmcp=is_gmcp,
            skip_translation=skip,
        )]

    def _extract_ansi(self, data: bytes) -> List[ANSISegment]:
        """提取 ANSI 转义序列及其位置"""
        segments = []
        for match in ANSI_RE.finditer(data):
            segments.append(ANSISegment(
                offset=match.start(),
                codes=match.group(),
            ))
        return segments

    @staticmethod
    def _strip_ansi(data: bytes) -> bytes:
        """去除 ANSI 转义序列"""
        return ANSI_RE.sub(b'', data)

    def _detect_prompt(self, data: bytes) -> bool:
        """检测是否为 MUD 状态提示行"""
        # 去掉 ANSI 后检测
        clean = self._strip_ansi(data).strip()

        if len(clean) == 0:
            return False

        # 太长的行不太可能是 prompt
        if len(clean) > 100:
            return False

        # 检查常见 prompt 模式
        for pattern in PROMPT_PATTERNS:
            if pattern.search(clean):
                return True

        # Prompt 特征: 包含多组数字/数字对
        # 例如: "Hp:1234/1234 Sp:567/567 Ep:89/100 >"
        ratio_count = len(re.findall(rb'\d+/\d+', clean))
        if ratio_count >= 2:
            return True

        return False

    def _detect_gmcp(self, data: bytes) -> bool:
        """检测是否为 GMCP 或 MSDP 结构化数据"""
        clean = self._strip_ansi(data).strip()

        # GMCP 数据通常是 JSON 或特定格式
        if clean.startswith(b'{') or clean.startswith(b'['):
            return True

        # MSDP 数据
        if clean.startswith(b'IAC') or clean.startswith(b'\xff'):
            return True

        return False

    def _find_iac_end(self, data: bytes, start: int) -> Optional[int]:
        """查找 IAC 序列的结束位置

        IAC 序列格式:
        - 2字节命令: IAC <command>  (如 IAC WILL, IAC DO, etc.)
        - 3字节命令: IAC WILL/DONT/DO/WONT <option>
        - 子协商: IAC SB <option> <data> IAC SE

        Returns:
            序列结束位置（指向序列后的下一个字节），不完整则返回 None
        """
        if start >= len(data) or data[start] != TelnetConstants.IAC:
            return None

        if start + 1 >= len(data):
            return None  # 不完整

        command = data[start + 1]

        # IAC IAC (转义的 IAC 字节)
        if command == TelnetConstants.IAC:
            return start + 2

        # 子协商结束 IAC SE
        if command == TelnetConstants.SE:
            return start + 2

        # 2字节命令
        if command in (TelnetConstants.SB, TelnetConstants.WILL,
                        TelnetConstants.WONT, TelnetConstants.DO,
                        TelnetConstants.DONT):
            if start + 2 >= len(data):
                return None  # 不完整
            option = data[start + 2]

            # 子协商 SB <option> <data...> IAC SE
            if command == TelnetConstants.SB:
                # 查找 IAC SE 结束标记
                pos = start + 3
                while pos < len(data) - 1:
                    if data[pos] == TelnetConstants.IAC and data[pos + 1] == TelnetConstants.SE:
                        return pos + 2
                    pos += 1
                return None  # 子协商未结束

            # 普通3字节命令
            return start + 3

        # 未知命令，安全起见返回2字节
        return start + 2

    def reset(self):
        """重置解析器状态"""
        self._buffer.clear()
        self._telnet_buffer.clear()
