"""
ANSI 转义序列 → HTML 转换器
将包含 ANSI 颜色码的字节流转换为带 CSS class 的 HTML
"""

import re

# ANSI SGR 码 → CSS class 映射
# 注意: background 的 class 是 "bg-N" 格式
_FG_MAP = {
    30: 'c30', 31: 'c31', 32: 'c32', 33: 'c33',
    34: 'c34', 35: 'c35', 36: 'c36', 37: 'c37',
    90: 'c90', 91: 'c91', 92: 'c92', 93: 'c93',
    94: 'c94', 95: 'c95', 96: 'c96', 97: 'c97',
}
_BG_MAP = {
    40: 'bg40', 41: 'bg41', 42: 'bg42', 43: 'bg43',
    44: 'bg44', 45: 'bg45', 46: 'bg46', 47: 'bg47',
    100: 'bg100', 101: 'bg101', 102: 'bg102', 103: 'bg103',
    104: 'bg104', 105: 'bg105', 106: 'bg106', 107: 'bg107',
}


def ansi_to_html(data: bytes) -> str:
    """将含 ANSI 转义序列的字节流转换为 HTML

    Args:
        data: 原始字节流 (如 b'\\x1b[32mHello\\x1b[0m\\n')

    Returns:
        HTML 字符串
    """
    parts = []
    _ansi_to_html_parts(data, parts)
    return ''.join(parts)


def _ansi_to_html_parts(data: bytes, out: list):
    """将字节流解析为 HTML 片段，追加到 out 列表"""
    classes = []       # 当前 active 的 CSS class
    fg = None
    bg = None
    bold = False
    underline = False
    dim = False
    blink = False
    reverse = False

    text_buf = bytearray()
    i = 0
    n = len(data)

    def flush_text():
        """将 text_buf 中的文本输出为 HTML"""
        nonlocal fg, bg, bold, underline, dim, blink, reverse
        if not text_buf:
            return

        # 收集当前样式 class
        cls = []
        if fg is not None:
            cls.append(_FG_MAP.get(fg, ''))
        if bg is not None:
            cls.append(_BG_MAP.get(bg, ''))
        if bold:
            cls.append('b')
        if underline:
            cls.append('u')
        if dim:
            cls.append('dim')
        if blink:
            cls.append('blink')
        if reverse:
            cls.append('rev')

        text = text_buf.decode('utf-8', errors='replace')
        text_buf.clear()

        # HTML 转义
        text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        cls = [c for c in cls if c]
        if cls:
            out.append(f'<span class="{" ".join(cls)}">{text}</span>')
        else:
            out.append(text)

    while i < n:
        b = data[i]

        # ANSI ESC sequence: ESC (0x1B) + '[' (0x5B)
        if b == 0x1B and i + 1 < n and data[i + 1] == 0x5B:
            flush_text()
            i += 2  # skip ESC[

            # Collect params
            params = ''
            while i < n:
                c = data[i]
                if 0x40 <= c <= 0x7E:  # terminating char
                    cmd = chr(c)
                    i += 1
                    break
                params += chr(c)
                i += 1
            else:
                break  # unterminated

            if cmd == 'm':
                _apply_sgr(params, fg_update := [None], bg_update := [None],
                           bold_update := [None], underline_update := [None],
                           dim_update := [None], blink_update := [None],
                           reverse_update := [None])
                if fg_update[0] is not None:
                    fg = fg_update[0]
                if bg_update[0] is not None:
                    bg = bg_update[0]
                if bold_update[0] is not None:
                    bold = bold_update[0]
                if underline_update[0] is not None:
                    underline = underline_update[0]
                if dim_update[0] is not None:
                    dim = dim_update[0]
                if blink_update[0] is not None:
                    blink = blink_update[0]
                if reverse_update[0] is not None:
                    reverse = reverse_update[0]
            continue

        # Skip bare CR (\r)
        if b == 0x0D:
            i += 1
            continue

        # Normal byte: add to text buffer
        text_buf.append(b)
        i += 1

    flush_text()


def _apply_sgr(params_str, fg, bg, bold, underline, dim, blink, reverse):
    """解析 SGR 参数并更新样式"""
    if not params_str or params_str == '0':
        fg[0] = 37   # default white
        bg[0] = None
        bold[0] = False
        underline[0] = False
        dim[0] = False
        blink[0] = False
        reverse[0] = False
        return

    codes = [int(x) for x in params_str.split(';') if x]

    for code in codes:
        if code == 0:
            fg[0] = 37
            bg[0] = None
            bold[0] = False
            underline[0] = False
            dim[0] = False
            blink[0] = False
            reverse[0] = False
        elif code == 1:
            bold[0] = True
        elif code == 2:
            dim[0] = True
        elif code == 4:
            underline[0] = True
        elif code == 5:
            blink[0] = True
        elif code == 7:
            reverse[0] = True
        elif code == 22:
            bold[0] = False
        elif code == 24:
            underline[0] = False
        elif code == 25:
            blink[0] = False
        elif code == 27:
            reverse[0] = False
        elif code in _FG_MAP:
            fg[0] = code
        elif code == 39:
            fg[0] = 37
        elif code in _BG_MAP:
            bg[0] = code
        elif code == 49:
            bg[0] = None


# Quick test
if __name__ == '__main__':
    test = b'\x1b[32mHello \x1b[1;31mWorld\x1b[0m\nNice!'
    print(ansi_to_html(test))
