"""epub 解析 + 雙語寫回。借鏡 bilingual_book_maker/book_maker/loader/epub_loader.py。"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup, NavigableString, Tag
from ebooklib import epub, ITEM_DOCUMENT

TRANSLATABLE_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote"}
EXCLUDED_TAGS = {"code", "pre", "sup", "sub", "script", "style"}
SKIP_RE = re.compile(r"^[\s\d\W_]+$")  # 純數字 / 標點 / 空白
URL_RE = re.compile(r"^https?://\S+$")


@dataclass
class Paragraph:
    """一個可譯段落的指標。idx 全書唯一遞增。"""
    idx: int
    chapter_id: str
    tag_id: int
    text: str

    def is_skippable(self) -> bool:
        t = self.text.strip()
        if not t or len(t) < 2:
            return True
        if SKIP_RE.match(t):
            return True
        if URL_RE.match(t):
            return True
        return False


@dataclass
class EpubLoader:
    book_path: Path
    _book: epub.EpubBook = field(init=False, default=None)
    _paragraphs: list[Paragraph] = field(init=False, default_factory=list)
    _para_locations: dict[int, tuple[str, int]] = field(init=False, default_factory=dict)
    n_chapters: int = 0

    def __post_init__(self) -> None:
        self._book = epub.read_epub(str(self.book_path), options={"ignore_ncx": False})

    def extract_paragraphs(self) -> list[Paragraph]:
        if self._paragraphs:
            return self._paragraphs

        idx = 0
        chapter_count = 0
        for item in self._book.get_items_of_type(ITEM_DOCUMENT):
            chapter_count += 1
            soup = BeautifulSoup(item.get_content(), "html.parser")
            tag_id = 0
            for tag in soup.find_all(TRANSLATABLE_TAGS):
                if tag.find_parent(TRANSLATABLE_TAGS):
                    continue
                if tag.find_parent(EXCLUDED_TAGS):
                    continue
                text = tag.get_text(separator=" ", strip=True)
                p = Paragraph(idx=idx, chapter_id=item.get_id(), tag_id=tag_id, text=text)
                if not p.is_skippable():
                    self._paragraphs.append(p)
                    self._para_locations[idx] = (item.get_id(), tag_id)
                    idx += 1
                tag_id += 1

        self.n_chapters = chapter_count
        return self._paragraphs

    def write_temp_bilingual(self, translations: dict[int, str], out_path: Path) -> None:
        """部分翻譯也能寫出，未譯段落保留英文。"""
        self._write(translations, out_path, partial=True)

    def write_final_bilingual(self, translations: dict[int, str], out_path: Path) -> None:
        self._write(translations, out_path, partial=False)

    def _write(self, translations: dict[int, str], out_path: Path, partial: bool) -> None:
        new_book = self._clone_book()

        by_chapter: dict[str, dict[int, str]] = {}
        for idx, zh in translations.items():
            chap_id, tag_id = self._para_locations[idx]
            by_chapter.setdefault(chap_id, {})[tag_id] = zh

        for item in new_book.get_items_of_type(ITEM_DOCUMENT):
            chap_id = item.get_id()
            if chap_id not in by_chapter:
                continue
            soup = BeautifulSoup(item.get_content(), "html.parser")
            local_tag_id = 0
            chapter_translations = by_chapter[chap_id]
            for tag in soup.find_all(TRANSLATABLE_TAGS):
                if tag.find_parent(TRANSLATABLE_TAGS):
                    continue
                if tag.find_parent(EXCLUDED_TAGS):
                    continue
                if local_tag_id in chapter_translations:
                    self._insert_translation(tag, chapter_translations[local_tag_id])
                local_tag_id += 1
            item.set_content(str(soup).encode("utf-8"))

        epub.write_epub(str(out_path), new_book)

    def _clone_book(self) -> epub.EpubBook:
        new = epub.read_epub(str(self.book_path), options={"ignore_ncx": False})
        return new

    @staticmethod
    def _insert_translation(tag: Tag, zh_text: str) -> None:
        """在原段落後插一個 zh 段落。class="zh-translation" + lang + inline style（藍色 + 較小字級）。"""
        zh_tag = copy.copy(tag)
        zh_tag.clear()
        zh_tag.string = zh_text
        zh_tag.attrs["class"] = "zh-translation"
        zh_tag.attrs["lang"] = "zh-Hant"
        zh_tag.attrs["style"] = "color: #1e6cb6; font-size: 0.92em; line-height: 1.55;"
        tag.insert_after(zh_tag)
