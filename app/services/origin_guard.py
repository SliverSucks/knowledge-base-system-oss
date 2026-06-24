"""跨站请求来源校验（CSRF 深度防御）。

直装版只在 127.0.0.1 监听，CORS 已挡浏览器跨域读响应；但 multipart POST
属于 simple request，浏览器即便不允许读响应也会照常发送请求。如果用户
在另一个网页上被诱导访问恶意页面，攻击者可以让浏览器对 127.0.0.1 上的
API 发起破坏性请求（例如 /v1/system/backup/import 覆盖数据）。

本模块在中间件层校验 Origin / Referer 头：
- 没有 Origin / Referer：放行（curl / 直装的 console / 服务端到服务端调用）
- Origin / Referer 指向 127.0.0.1 / localhost：放行
- 其他（含 file://、null、外部域名）：拒绝写类请求

只校验写类 HTTP 方法（POST/PUT/PATCH/DELETE）。GET/HEAD/OPTIONS 不动。
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse


_LOOPBACK_HOST_RE = re.compile(r"^(127\.0\.0\.1|localhost|\[::1\]|::1)$")
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def is_loopback_origin(origin: Optional[str]) -> bool:
    """判断 origin 字符串是否指向本机环回。

    - None / 空：True（放行）
    - 'null' / file:// 协议：False（拒绝）
    - http(s)://(127.0.0.1|localhost)[:port]：True
    - 其他：False
    """
    if not origin:
        return True
    if origin == "null":
        return False
    parsed = urlparse(origin)
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname or ""
    return bool(_LOOPBACK_HOST_RE.fullmatch(host))


def should_block_request(
    method: str,
    origin: Optional[str],
    referer: Optional[str],
) -> bool:
    """判断是否拦截。

    只检查写类方法；Origin 优先，没有再退到 Referer。两个都没有视为放行
    （server-to-server / curl）。
    """
    if method not in _WRITE_METHODS:
        return False
    if origin is not None:
        return not is_loopback_origin(origin)
    if referer is not None:
        return not is_loopback_origin(referer)
    return False
