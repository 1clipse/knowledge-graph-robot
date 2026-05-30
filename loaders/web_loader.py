from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

from loguru import logger


_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata",
    "metadata.google.internal",
}


class URLSafetyError(ValueError):
    """Raised when a URL is unsafe to fetch from the ingest pipeline."""


class WebLoader:
    def __init__(self, timeout: int = 30, max_redirects: int = 3) -> None:
        self._timeout = timeout
        self._max_redirects = max_redirects

    def load(self, url: str, selector: str | None = None) -> str:
        try:
            import httpx
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("httpx and beautifulsoup4 are required. Run: pip install httpx beautifulsoup4")
            raise

        safe_url = validate_public_http_url(url)
        logger.info(f"Fetching URL: {safe_url}")
        try:
            with httpx.Client(timeout=self._timeout, follow_redirects=False) as client:
                response = self._get_with_safe_redirects(client, safe_url)
                response.raise_for_status()
        except httpx.HTTPError as e:
            logger.error(f"HTTP request failed: {e}")
            raise

        soup = BeautifulSoup(response.text, "html.parser")

        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()

        if selector:
            elements = soup.select(selector)
            text = "\n\n".join(el.get_text(separator="\n", strip=True) for el in elements)
        else:
            main_content = soup.find("main") or soup.find("article") or soup.find("body")
            text = main_content.get_text(separator="\n", strip=True) if main_content else ""

        text = self._clean_text(text)
        logger.info(f"Loaded web page: {safe_url} ({len(text)} chars)")
        return text

    def load_multiple(self, urls: list[str], selector: str | None = None) -> list[str]:
        results: list[str] = []
        for url in urls:
            try:
                text = self.load(url, selector)
                results.append(text)
            except Exception as e:
                logger.error(f"Failed to load {url}: {e}")
                results.append("")
        return results

    def _get_with_safe_redirects(self, client, url: str):
        current_url = url
        for _ in range(self._max_redirects + 1):
            response = client.get(current_url)
            if response.status_code not in (301, 302, 303, 307, 308):
                return response

            location = response.headers.get("Location")
            if not location:
                return response
            redirected_url = urljoin(current_url, location)
            current_url = validate_public_http_url(redirected_url)

        raise URLSafetyError(f"Too many redirects while fetching URL: {url}")

    def _clean_text(self, text: str) -> str:
        import re
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()


def validate_public_http_url(url: str) -> str:
    """Validate a URL is safe for server-side fetching.

    Only public HTTP/HTTPS URLs are allowed. Local, private, link-local,
    multicast, reserved, and unspecified IP ranges are blocked. Hostnames are
    resolved before fetch so DNS names pointing to private networks are rejected.
    Redirect targets must pass this same function.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise URLSafetyError("Only HTTP/HTTPS URLs are allowed")
    if not parsed.hostname:
        raise URLSafetyError("URL must include a hostname")
    if parsed.username or parsed.password:
        raise URLSafetyError("URL userinfo is not allowed")

    hostname = parsed.hostname.strip().rstrip(".").lower()
    if hostname in _BLOCKED_HOSTNAMES:
        raise URLSafetyError("URL hostname is not allowed")

    for ip in _resolve_host_ips(hostname):
        if _is_blocked_ip(ip):
            raise URLSafetyError(f"URL resolves to a non-public IP address: {ip}")

    return parsed.geturl()


def _resolve_host_ips(hostname: str) -> set[ipaddress._BaseAddress]:
    try:
        literal_ip = ipaddress.ip_address(hostname)
        return {literal_ip}
    except ValueError:
        pass

    try:
        addr_infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise URLSafetyError(f"Cannot resolve URL hostname: {hostname}") from exc

    ips: set[ipaddress._BaseAddress] = set()
    for info in addr_infos:
        address = info[4][0]
        try:
            ips.add(ipaddress.ip_address(address))
        except ValueError:
            continue
    if not ips:
        raise URLSafetyError(f"Cannot resolve URL hostname: {hostname}")
    return ips


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )
