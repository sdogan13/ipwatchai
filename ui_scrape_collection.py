from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import async_playwright


SEARCH_URL = "https://www.turkpatent.gov.tr/arastirma-yap?form=trademark"
SCRAPED_METADATA_NAME = "scraped_metadata.json"

logger = logging.getLogger("turkpatent.ui_scrape")

_EMPTY_TEXT_MARKERS = {"", "null", "none", "n/a"}
_BLT_INPUT_PATTERNS = [
    re.compile(r"Marka\s*[Ii\u0130]lan\s*B[Uu\u00dc\u00fc]lten\s*No", re.I),
    re.compile(r"B[Uu\u00dc\u00fc]lten\s*No", re.I),
]
_GZ_INPUT_PATTERNS = [
    re.compile(r"Tescil.*B[Uu\u00dc\u00fc]lten", re.I),
]
_MARKA_INPUT_PATTERN = re.compile(r"Marka.*B[Uu\u00dc\u00fc]lten", re.I)


def _normalize_int(text: str) -> int:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else 0


def _normalize_issue_number(issue_no: str) -> str:
    return re.sub(r"\s+", "", str(issue_no or "").strip())


def _normalize_ui_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.translate(
        str.maketrans(
            {
                "ı": "i",
                "İ": "i",
                "ş": "s",
                "Ş": "s",
                "ğ": "g",
                "Ğ": "g",
                "ü": "u",
                "Ü": "u",
                "ö": "o",
                "Ö": "o",
                "ç": "c",
                "Ç": "c",
            }
        )
    )
    return re.sub(r"\s+", " ", text).strip().lower()


def _input_matches_patterns(
    *,
    placeholder: str | None,
    aria_label: str | None,
    name: str | None,
    patterns: List[re.Pattern[str]],
) -> bool:
    haystack = " ".join(
        part for part in (placeholder, aria_label, name) if part
    )
    normalized = _normalize_ui_text(haystack)
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in patterns)


def _normalize_sidecar_record(
    row: List[str],
    *,
    issue_no: str,
    issue_date: Optional[str],
    source_type: str,
) -> Dict[str, Any]:
    app_no = row[1].strip() if len(row) > 1 else ""
    name = row[2].strip() if len(row) > 2 else ""
    holder_title = row[3].strip() if len(row) > 3 else ""
    app_date = row[4].strip() if len(row) > 4 else ""
    reg_no = row[5].strip() if len(row) > 5 else ""
    status = row[6].strip() if len(row) > 6 else ""
    classes_raw = row[7].strip() if len(row) > 7 else ""
    nice_classes = re.findall(r"\d+", classes_raw)

    trademark = {
        "APPLICATIONDATE": app_date,
        "REGISTERNO": reg_no,
        "REGISTERDATE": "",
        "INTREGNO": "",
        "NAME": name,
        "NICECLASSES_RAW": ", ".join(nice_classes),
        "NICECLASSES_LIST": nice_classes,
        "TM_TYPE_CODE": "null",
        "VIENNACLASSES_RAW": "",
        "VIENNACLASSES_LIST": [],
        "BULLETIN_NO": "",
        "BULLETIN_DATE": "",
        "GAZETTE_NO": "",
        "GAZETTE_DATE": "",
        "EXTRA_COL_11": "",
        "EXTRA_COL_12": "",
    }
    if source_type == "GZ":
        trademark["GAZETTE_NO"] = issue_no
        if issue_date:
            trademark["GAZETTE_DATE"] = issue_date
    else:
        trademark["BULLETIN_NO"] = issue_no
        if issue_date:
            trademark["BULLETIN_DATE"] = issue_date

    return {
        "APPLICATIONNO": app_no,
        "STATUS": status,
        "IMAGE": app_no.replace("/", "_"),
        "TRADEMARK": trademark,
        "HOLDERS": [
            {
                "TPECLIENTID": "",
                "TITLE": holder_title,
                "ADDRESS": "",
                "TOWN_DISTRICT": "",
                "POSTALCODE": "",
                "CITY_PROVINCE": "",
                "COUNTRY": "TURKIYE",
            }
        ],
        "ATTORNEYS": [],
        "GOODS": [],
        "EXTRACTEDGOODS": [],
    }


def _rows_to_sidecar_data(
    rows: List[List[str]],
    *,
    issue_no: str,
    issue_date: Optional[str],
    source_type: str,
) -> List[Dict[str, Any]]:
    return [
        _normalize_sidecar_record(
            row,
            issue_no=issue_no,
            issue_date=issue_date,
            source_type=source_type,
        )
        for row in rows
        if row and any((cell or "").strip() for cell in row)
    ]


async def _try_click(locator, timeout_ms: int = 1500) -> bool:
    try:
        await locator.first.click(timeout=timeout_ms)
        return True
    except Exception:
        return False


async def _try_dom_click(locator) -> bool:
    try:
        await locator.first.evaluate("(el) => el.click()")
        return True
    except Exception:
        return False


async def _clear_overlays(page) -> None:
    try:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)
    except Exception:
        pass

    candidates = [
        page.get_by_role("button", name=re.compile(r"kabul|accept|tamam|ok|anlad[i\u0131]m", re.I)),
        page.locator("button:has-text('Kabul')"),
        page.locator("button:has-text('Accept')"),
        page.locator("button[aria-label*='Close']"),
        page.locator("button[aria-label*='Kapat']"),
        page.locator("div[role='dialog'] button").first,
    ]
    for candidate in candidates:
        try:
            if await candidate.count() > 0:
                await candidate.first.click(timeout=800, force=True)
                await page.wait_for_timeout(300)
        except Exception:
            pass

    try:
        await page.mouse.click(2, 2)
        await page.wait_for_timeout(300)
    except Exception:
        pass

    try:
        await page.evaluate(
            """() => {
                document.querySelectorAll(
                    'section[class*="jss"], div[role="dialog"], .MuiDialog-root, .MuiBackdrop-root'
                ).forEach((el) => {
                    const style = window.getComputedStyle(el);
                    if (style.position === 'fixed' && parseInt(style.zIndex || 0, 10) > 50) {
                        el.style.display = 'none';
                    }
                });
            }"""
        )
    except Exception:
        pass


async def _ensure_search_tab(page) -> None:
    try:
        await page.get_by_text(
            re.compile(r"Marka\s*Ara(?:s|[\u015f])t[ıi]rma", re.I)
        ).first.click(timeout=2000)
    except Exception:
        pass


async def _expand_detailed_search(page) -> None:
    try:
        btn = page.get_by_text(re.compile(r"Detayl[ıi]\s*Arama", re.I)).first
        if await btn.count() > 0:
            await btn.click(timeout=2000)
            await page.wait_for_timeout(1000)
    except Exception:
        pass


async def _find_visible_input_by_patterns(page, patterns: List[re.Pattern[str]]):
    inputs = page.locator("input")
    try:
        count = await inputs.count()
    except Exception:
        return None

    for idx in range(count):
        candidate = inputs.nth(idx)
        try:
            if not await candidate.is_visible():
                continue
            input_type = ((await candidate.get_attribute("type")) or "text").lower()
            if input_type not in {"", "text", "search", "tel", "number"}:
                continue
            if _input_matches_patterns(
                placeholder=await candidate.get_attribute("placeholder"),
                aria_label=await candidate.get_attribute("aria-label"),
                name=await candidate.get_attribute("name"),
                patterns=patterns,
            ):
                return candidate
        except Exception:
            continue
    return None


async def _locate_blt_input(page):
    async def _find():
        for pattern in _BLT_INPUT_PATTERNS:
            candidate = page.locator("mat-form-field").filter(has_text=pattern).locator("input").first
            try:
                if await candidate.count() > 0 and await candidate.is_visible():
                    return candidate
            except Exception:
                continue
        fallback = await _find_visible_input_by_patterns(page, _BLT_INPUT_PATTERNS)
        if fallback is not None:
            return fallback
        fallback = page.locator(
            "input:visible[placeholder*='Bulten No' i], "
            "input:visible[aria-label*='Bulten No' i], "
            "input:visible[placeholder*='B\\u00fclten No' i], "
            "input:visible[aria-label*='B\\u00fclten No' i]"
        ).first
        try:
            if await fallback.count() > 0 and await fallback.is_visible():
                return fallback
        except Exception:
            pass
        return None

    found = await _find()
    if found:
        return found
    await _expand_detailed_search(page)
    return await _find()


async def _locate_gz_input(page):
    async def _find_direct():
        for pattern in _GZ_INPUT_PATTERNS:
            candidate = page.locator("mat-form-field").filter(has_text=pattern).locator("input").first
            try:
                if await candidate.count() > 0 and await candidate.is_visible():
                    return candidate
            except Exception:
                continue
        fallback = await _find_visible_input_by_patterns(page, _GZ_INPUT_PATTERNS)
        if fallback is not None:
            return fallback
        return None

    found = await _find_direct()
    if found:
        return found

    await _expand_detailed_search(page)
    found = await _find_direct()
    if found:
        return found

    try:
        marka_field = page.locator("mat-form-field").filter(has_text=_MARKA_INPUT_PATTERN).first
        if await marka_field.count() > 0:
            relative_input = marka_field.locator("input").locator("xpath=following::input[1]")
            if await relative_input.count() > 0:
                return relative_input.first
    except Exception:
        pass

    generic_inputs = page.locator(
        "input[placeholder*='Bulten' i], input[aria-label*='Bulten' i], "
        "input[placeholder*='B\\u00fclten' i], input[aria-label*='B\\u00fclten' i]"
    )
    try:
        count = await generic_inputs.count()
        if count >= 2:
            return generic_inputs.nth(1)
        if count == 1:
            return generic_inputs.first
    except Exception:
        pass
    return None


async def _click_search(page) -> None:
    candidates = [
        page.get_by_role("button", name=re.compile(r"Sorgula", re.I)),
        page.locator("button:has-text('SORGULA')"),
        page.locator("button:has-text('Sorgula')"),
    ]
    for button in candidates:
        if await _try_click(button, 8000):
            return
        if await _try_dom_click(button):
            return
    raise RuntimeError("Could not click SORGULA.")


async def _ensure_infinite_list(page) -> bool:
    try:
        switch = page.get_by_role("switch", name=re.compile(r"Sonsuz\s*Liste", re.I))
        if await switch.count() > 0:
            aria = ((await switch.first.get_attribute("aria-checked")) or "").lower()
            if aria != "true":
                await switch.first.click(force=True)
                await page.wait_for_timeout(800)
            return True
    except Exception:
        pass

    try:
        checkbox = page.locator("div:has-text('Sonsuz Liste') input[type=checkbox]").first
        if await checkbox.count() > 0:
            try:
                if not await checkbox.is_checked():
                    try:
                        await checkbox.check()
                    except Exception:
                        await page.locator("div:has-text('Sonsuz Liste')").first.click(force=True)
                    await page.wait_for_timeout(800)
            except Exception:
                await page.locator("div:has-text('Sonsuz Liste')").first.click(force=True)
                await page.wait_for_timeout(800)
            return True
    except Exception:
        pass
    return False


async def _read_total_count(page) -> Optional[int]:
    try:
        loc = page.locator(
            "xpath=//*[contains(translate(.,'I\\u0130','ii'),'kayit bulundu') or contains(., 'kay\\u0131t bulundu')]"
        ).first
        text = await loc.inner_text(timeout=2500)
        match = re.search(r"([\d\.,]+)\s*kay[ıi]t\s*bulundu", text, re.I)
        if match:
            return _normalize_int(match.group(1))
    except Exception:
        pass

    try:
        body_text = await page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        for pattern in (
            r"([\d\.,]+)\s*kay[ıi]t\s*bulundu",
            r"Toplam\s*[:\-]?\s*([\d\.,]+)\s*kay[ıi]t",
        ):
            match = re.search(pattern, body_text, re.I)
            if match:
                found = _normalize_int(match.group(1))
                if found > 0:
                    return found
    except Exception:
        pass
    return None


async def _detect_grid(page) -> Tuple[str, str]:
    if await page.locator(".dx-datagrid").count() > 0:
        return "css=.dx-datagrid-rowsview .dx-data-row", "css=.dx-datagrid-rowsview"
    if await page.locator("cdk-virtual-scroll-viewport").count() > 0:
        return (
            "css=cdk-virtual-scroll-viewport .cdk-virtual-scroll-content-wrapper > *",
            "css=cdk-virtual-scroll-viewport",
        )
    return "css=table tbody tr", "css=body"


async def _get_last_row_position(page, row_sel: str) -> int:
    return await page.evaluate(
        """(sel) => {
            const css = sel.replace('css=', '');
            const rows = document.querySelectorAll(css);
            if (rows.length === 0) return 0;
            const last = rows[rows.length - 1];
            const idx = last.getAttribute('aria-rowindex') ||
                        last.getAttribute('data-rowindex') ||
                        last.getAttribute('data-idx');
            if (idx) return parseInt(idx, 10);
            return rows.length;
        }""",
        row_sel,
    )


def _is_body_scroll(scroll_target_sel: str) -> bool:
    target = (scroll_target_sel or "").lower().strip()
    return target in {"css=body", "body", "css=html", "html", "css=document", "document"}


async def _js_scroll_to_bottom(page, scroll_target_sel: str, offset: int = 0) -> None:
    if _is_body_scroll(scroll_target_sel):
        await page.evaluate(
            """(off) => {
                const el = document.scrollingElement || document.documentElement;
                if (!el) return;
                const maxTop = Math.max(0, el.scrollHeight - el.clientHeight - off);
                el.scrollTop = maxTop;
                el.dispatchEvent(new Event('scroll', { bubbles: true }));
            }""",
            offset,
        )
        return
    loc = page.locator(scroll_target_sel).first
    await loc.evaluate(
        """(el, off) => {
            try {
                const tgt = el.querySelector('.dx-scrollable-container') || el;
                const maxTop = Math.max(0, tgt.scrollHeight - tgt.clientHeight - off);
                tgt.scrollTop = maxTop;
                tgt.dispatchEvent(new Event('scroll', { bubbles: true }));
            } catch (e) {}
        }""",
        offset,
    )


async def _js_scroll_by(page, scroll_target_sel: str, dy: int) -> None:
    if _is_body_scroll(scroll_target_sel):
        await page.evaluate(
            """(delta) => {
                const el = document.scrollingElement || document.documentElement;
                if (!el) return;
                el.scrollTop = el.scrollTop + delta;
                el.dispatchEvent(new Event('scroll', { bubbles: true }));
            }""",
            dy,
        )
        return
    loc = page.locator(scroll_target_sel).first
    await loc.evaluate(
        """(el, delta) => {
            try {
                const tgt = el.querySelector('.dx-scrollable-container') || el;
                tgt.scrollTop = tgt.scrollTop + delta;
                tgt.dispatchEvent(new Event('scroll', { bubbles: true }));
            } catch (e) {}
        }""",
        dy,
    )


async def _wheel_inside_scroll_target(page, scroll_target_sel: str, delta_y: int) -> None:
    try:
        if _is_body_scroll(scroll_target_sel):
            viewport = page.viewport_size
            if viewport:
                await page.mouse.move(viewport["width"] / 2, viewport["height"] / 2)
            await page.mouse.wheel(0, delta_y)
            return
        base = page.locator(scroll_target_sel).first
        inner = base.locator(".dx-scrollable-container").first
        target = inner if await inner.count() > 0 else base
        box = await target.bounding_box()
        if box:
            x = box["x"] + box["width"] / 2
            y = box["y"] + min(box["height"] - 5, box["height"] / 2)
            await page.mouse.move(x, y)
            try:
                await page.mouse.click(x, y)
            except Exception:
                pass
        await page.mouse.wheel(0, delta_y)
    except Exception:
        try:
            await page.mouse.wheel(0, delta_y)
        except Exception:
            pass


async def _wait_until_position_changes(page, row_sel: str, prev_pos: int, timeout_s: float = 10.0) -> int:
    deadline = asyncio.get_running_loop().time() + timeout_s
    best = prev_pos
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.05)
        cur = await _get_last_row_position(page, row_sel)
        if cur > best:
            return cur
        best = max(best, cur)
    return best


async def _jiggle_recovery(page, scroll_target_sel: str) -> None:
    await _wheel_inside_scroll_target(page, scroll_target_sel, delta_y=-225)
    await asyncio.sleep(1.0)
    await _js_scroll_to_bottom(page, scroll_target_sel, offset=0)
    await asyncio.sleep(0.2)
    try:
        if _is_body_scroll(scroll_target_sel):
            await page.keyboard.press("End")
    except Exception:
        pass
    await _wheel_inside_scroll_target(page, scroll_target_sel, delta_y=2600)


async def _scrape_current_view(page, row_sel: str, data_store: Dict[str, List[str]]) -> None:
    try:
        new_data = await page.evaluate(
            """(sel) => {
                const css = sel.replace('css=', '');
                const rows = Array.from(document.querySelectorAll(css));
                return rows.map((row) => {
                    const cells = Array.from(row.querySelectorAll("td, div[role='gridcell']"));
                    return cells.map((c) => c.innerText.trim());
                });
            }""",
            row_sel,
        )
        for row_data in new_data:
            if any((cell or "").strip() for cell in row_data):
                key = "|".join(row_data[1:min(5, len(row_data))])
                data_store[key] = row_data
    except Exception:
        pass


async def _scroll_and_capture(
    page,
    row_sel: str,
    scroll_target_sel: str,
    *,
    max_seconds: int,
    limit: int,
    stagnation_limit: int,
) -> List[List[str]]:
    captured_data: Dict[str, List[str]] = {}
    await asyncio.sleep(0.5)

    total = await _read_total_count(page)
    start_t = asyncio.get_running_loop().time()
    last_pos = await _get_last_row_position(page, row_sel)
    stagnation = 0

    try:
        await _wheel_inside_scroll_target(page, scroll_target_sel, delta_y=1)
    except Exception:
        pass

    while True:
        if not total:
            total = await _read_total_count(page)

        await _scrape_current_view(page, row_sel, captured_data)
        current_len = len(captured_data)

        if total and current_len >= total:
            break
        if limit > 0 and current_len >= limit:
            break
        if max_seconds > 0 and (asyncio.get_running_loop().time() - start_t > max_seconds):
            break

        prev = last_pos
        await _js_scroll_to_bottom(page, scroll_target_sel, offset=80)
        await asyncio.sleep(0.02)
        await _js_scroll_by(page, scroll_target_sel, dy=-260)
        await asyncio.sleep(0.02)
        await _js_scroll_to_bottom(page, scroll_target_sel, offset=0)
        await asyncio.sleep(0.02)
        await _wheel_inside_scroll_target(page, scroll_target_sel, delta_y=2800)

        new_pos = await _wait_until_position_changes(page, row_sel, prev, timeout_s=2.5)
        if new_pos <= prev:
            stagnation += 1
            if total and current_len >= total:
                break
            await _js_scroll_by(page, scroll_target_sel, dy=-650 - (stagnation * 90))
            await asyncio.sleep(0.15)
            await _js_scroll_to_bottom(page, scroll_target_sel, offset=0)
            await asyncio.sleep(0.05)
            await _wheel_inside_scroll_target(page, scroll_target_sel, delta_y=3200)

            new_pos2 = await _wait_until_position_changes(page, row_sel, prev, timeout_s=3.0)
            new_pos = max(new_pos, new_pos2)
            if new_pos <= prev:
                await _jiggle_recovery(page, scroll_target_sel)
                new_pos3 = await _wait_until_position_changes(page, row_sel, prev, timeout_s=4.0)
                new_pos = max(new_pos, new_pos3)
            if stagnation > stagnation_limit:
                break
        else:
            stagnation = 0
            last_pos = new_pos

    return list(captured_data.values())


class UIScrapeSession:
    def __init__(self, page):
        self.page = page

    async def collect_blt_issue(
        self,
        issue_no: str,
        issue_date: Optional[str],
        out_dir: Path,
        *,
        max_scroll_seconds: int = 0,
        limit: int = 0,
    ) -> Dict[str, Any]:
        return await self._collect_issue(
            issue_no=issue_no,
            issue_date=issue_date,
            out_dir=out_dir,
            source_type="BLT",
            max_scroll_seconds=max_scroll_seconds,
            limit=limit,
            stagnation_limit=50,
        )

    async def collect_gz_issue(
        self,
        issue_no: str,
        issue_date: Optional[str],
        out_dir: Path,
        *,
        max_scroll_seconds: int = 0,
        limit: int = 0,
    ) -> Dict[str, Any]:
        return await self._collect_issue(
            issue_no=issue_no,
            issue_date=issue_date,
            out_dir=out_dir,
            source_type="GZ",
            max_scroll_seconds=max_scroll_seconds,
            limit=limit,
            stagnation_limit=30,
        )

    async def _collect_issue(
        self,
        *,
        issue_no: str,
        issue_date: Optional[str],
        out_dir: Path,
        source_type: str,
        max_scroll_seconds: int,
        limit: int,
        stagnation_limit: int,
    ) -> Dict[str, Any]:
        normalized_issue = _normalize_issue_number(issue_no)
        if not normalized_issue:
            return {"status": "failed", "records": 0, "error": "Missing issue number"}

        page = self.page
        await page.goto(SEARCH_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(1000)
        await _clear_overlays(page)
        await _ensure_search_tab(page)

        locator = await (_locate_gz_input(page) if source_type == "GZ" else _locate_blt_input(page))
        if locator is None:
            return {
                "status": "failed",
                "records": 0,
                "error": f"{source_type} input field not found",
            }

        try:
            await locator.click(timeout=4000)
            await locator.fill(normalized_issue)
        except Exception:
            await locator.click(force=True, timeout=2000)
            await locator.fill(normalized_issue, force=True)

        await locator.press("Enter")
        await page.wait_for_timeout(1000)
        await _click_search(page)

        row_sel, scroll_target_sel = await _detect_grid(page)
        await page.wait_for_selector(row_sel, timeout=20000)
        await _ensure_infinite_list(page)

        rows = await _scroll_and_capture(
            page,
            row_sel,
            scroll_target_sel,
            max_seconds=max_scroll_seconds,
            limit=limit,
            stagnation_limit=stagnation_limit,
        )
        data = _rows_to_sidecar_data(
            rows,
            issue_no=normalized_issue,
            issue_date=issue_date,
            source_type=source_type,
        )
        if not data:
            return {"status": "empty", "records": 0, "error": None}

        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / SCRAPED_METADATA_NAME
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        return {
            "status": "success",
            "records": len(data),
            "output_path": output_path,
        }


async def _collect_with_ephemeral_session(
    *,
    issue_no: str,
    issue_date: Optional[str],
    out_dir: Path,
    source_type: str,
    headless: bool,
    max_scroll_seconds: int,
    limit: int,
) -> Dict[str, Any]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        page = await context.new_page()
        try:
            session = UIScrapeSession(page)
            if source_type == "GZ":
                return await session.collect_gz_issue(
                    issue_no,
                    issue_date,
                    out_dir,
                    max_scroll_seconds=max_scroll_seconds,
                    limit=limit,
                )
            return await session.collect_blt_issue(
                issue_no,
                issue_date,
                out_dir,
                max_scroll_seconds=max_scroll_seconds,
                limit=limit,
            )
        finally:
            await context.close()
            await browser.close()


async def collect_blt_issue(
    issue_no: str,
    issue_date: Optional[str],
    out_dir: Path,
    *,
    session: Optional[UIScrapeSession] = None,
    headless: bool = True,
    max_scroll_seconds: int = 0,
    limit: int = 0,
) -> Dict[str, Any]:
    if session is not None:
        return await session.collect_blt_issue(
            issue_no,
            issue_date,
            out_dir,
            max_scroll_seconds=max_scroll_seconds,
            limit=limit,
        )
    return await _collect_with_ephemeral_session(
        issue_no=issue_no,
        issue_date=issue_date,
        out_dir=out_dir,
        source_type="BLT",
        headless=headless,
        max_scroll_seconds=max_scroll_seconds,
        limit=limit,
    )


async def collect_gz_issue(
    issue_no: str,
    issue_date: Optional[str],
    out_dir: Path,
    *,
    session: Optional[UIScrapeSession] = None,
    headless: bool = True,
    max_scroll_seconds: int = 0,
    limit: int = 0,
) -> Dict[str, Any]:
    if session is not None:
        return await session.collect_gz_issue(
            issue_no,
            issue_date,
            out_dir,
            max_scroll_seconds=max_scroll_seconds,
            limit=limit,
        )
    return await _collect_with_ephemeral_session(
        issue_no=issue_no,
        issue_date=issue_date,
        out_dir=out_dir,
        source_type="GZ",
        headless=headless,
        max_scroll_seconds=max_scroll_seconds,
        limit=limit,
    )
