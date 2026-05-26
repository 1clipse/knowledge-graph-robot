from __future__ import annotations

from typing import List, Optional

from loguru import logger


class WebLoader:
    def __init__(self, timeout: int = 30) -> None:
        self._timeout = timeout

    def load(self, url: str, selector: Optional[str] = None) -> str:
        try:
            import httpx
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("httpx and beautifulsoup4 are required. Run: pip install httpx beautifulsoup4")
            raise

        logger.info(f"Fetching URL: {url}")
        try:
            with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
                response = client.get(url)
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
        logger.info(f"Loaded web page: {url} ({len(text)} chars)")
        return text

    def load_multiple(self, urls: List[str], selector: Optional[str] = None) -> List[str]:
        results: List[str] = []
        for url in urls:
            try:
                text = self.load(url, selector)
                results.append(text)
            except Exception as e:
                logger.error(f"Failed to load {url}: {e}")
                results.append("")
        return results

    def _clean_text(self, text: str) -> str:
        import re
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()
