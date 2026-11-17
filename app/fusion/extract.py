"""文章素材提取：任意公开文章链接 → 图片清单 → 本地文件。

对齐 CREATOR OS 的「贴纸号内容提取」四步：粘贴链接 → 提取 → 预览下载 → 选图复用。
只用标准库解析 HTML；下载走 httpx（CreatorHub 已有依赖）。含 SSRF 防护与体积上限。
"""
from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from .paths import FUSION_DIR, ensure_dir

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_IMAGES = 60
SKIP_HOSTS = {"mmbiz.qpic.cn"}  # 微信图片 CDN 需要 referer，单独处理


class _ImageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images: list[dict] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "title":
            self._in_title = True
        if tag == "meta" and values.get("property") in {"og:image", "twitter:image"}:
            src = values.get("content", "")
            if src:
                self.images.append({"src": src, "width": 0, "height": 0})
        if tag == "img":
            src = (values.get("data-src") or values.get("data-original")
                   or values.get("data-lazy-src") or values.get("src") or "")
            if not src:
                return
            def _int(name: str) -> int:
                try:
                    return int(str(values.get(name, "0")).strip() or 0)
                except ValueError:
                    return 0
            self.images.append({"src": src, "width": _int("width"), "height": _int("height")})

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and not self.title:
            self.title = data.strip()


ULA = ipaddress.ip_network("fc00::/7")


def _public_host(host: str) -> bool:
    """拒绝回环、RFC1918/ULA、链路本地与保留地址，避免 SSRF。

    允许「公网 + 无关 IPv6 占位地址」共存（部分网络会同时返回 2001::1 这类地址），
    但只要有 IPv4 私网或 IPv6 ULA/回环，就直接拒绝。
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    saw_global = False
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if ip.version == 4:
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        else:
            if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast:
                return False
            if ip in ULA:
                return False
        saw_global = saw_global or ip.is_global
    return saw_global


def _validate(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("只支持 http/https 公开文章链接")
    if parsed.username or parsed.password:
        raise ValueError("链接不能携带账号密码")
    if not _public_host(parsed.hostname):
        raise ValueError("链接指向内网或不可达地址，已拒绝")
    return url.strip()


def _normalize(src: str, base: str) -> str | None:
    if not src or src.startswith(("data:", "javascript:", "blob:")):
        return None
    absolute = urljoin(base, src.strip())
    if not absolute.startswith(("http://", "https://")):
        return None
    return absolute


def _extension(url: str, content_type: str) -> str:
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}:
        return suffix
    mapping = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
               "image/webp": ".webp", "image/svg+xml": ".svg"}
    return mapping.get(content_type.split(";")[0].strip().lower(), ".jpg")


def extract_article_assets(url: str, limit: int = 30, download: bool = True) -> dict:
    """提取文章图片。download=False 时只返回清单，不落盘。"""
    url = _validate(url)
    limit = max(1, min(limit, MAX_IMAGES))
    headers = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"}
    with httpx.Client(timeout=25, follow_redirects=True, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()
        _validate(str(response.url))  # 重定向后的最终地址也要校验
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type and "xml" not in content_type:
            raise ValueError(f"该链接不是网页（{content_type or '未知类型'}）")
        html = response.text[:MAX_HTML_BYTES]
        final_url = str(response.url)

        parser = _ImageParser()
        parser.feed(html)
        seen: set[str] = set()
        candidates: list[dict] = []
        for item in parser.images:
            src = _normalize(item["src"], final_url)
            if not src or src in seen:
                continue
            # 跳过 1px 埋点与明显的图标
            if 0 < item["width"] <= 2 and 0 < item["height"] <= 2:
                continue
            seen.add(src)
            candidates.append({"url": src, "width": item["width"], "height": item["height"]})
            if len(candidates) >= limit:
                break

    if not download:
        return {"url": final_url, "title": parser.title, "count": len(candidates),
                "images": candidates, "dir": None,
                "note": "未下载；确认清单后再调用 download=true"}

    digest = hashlib.sha256(final_url.encode()).hexdigest()[:10]
    target = ensure_dir(FUSION_DIR / "extract" / f"{datetime.utcnow():%Y%m%d}-{digest}")
    files = []
    with httpx.Client(timeout=30, follow_redirects=True,
                      headers={"User-Agent": UA, "Referer": final_url}) as client:
        for index, item in enumerate(candidates, 1):
            try:
                with client.stream("GET", item["url"]) as stream:
                    stream.raise_for_status()
                    ctype = stream.headers.get("content-type", "")
                    if not ctype.startswith("image/"):
                        continue
                    size = 0
                    chunks = []
                    for chunk in stream.iter_bytes():
                        size += len(chunk)
                        if size > MAX_IMAGE_BYTES:
                            raise ValueError("图片超过 15MB")
                        chunks.append(chunk)
                    data = b"".join(chunks)
            except (httpx.HTTPError, ValueError):
                continue
            name = f"img_{index:03d}{_extension(item['url'], ctype)}"
            path = target / name
            path.write_bytes(data)
            files.append({"name": name, "url": item["url"], "bytes": len(data),
                          "width": item["width"], "height": item["height"]})
    return {"url": final_url, "title": parser.title, "count": len(files),
            "images": files, "dir": str(target),
            "note": "仅用于已获授权的素材整理；请自行确认版权与平台条款。"}
