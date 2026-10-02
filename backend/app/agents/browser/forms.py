"""Generic form filling helpers for job application pages."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from playwright.sync_api import Locator, Page

logger = logging.getLogger("app.browser.forms")


def _first_name(full_name: str) -> str:
    parts = full_name.strip().split()
    return parts[0] if parts else ""


def _last_name(full_name: str) -> str:
    parts = full_name.strip().split()
    return " ".join(parts[1:]) if len(parts) > 1 else ""


def clean_location(profile: dict) -> str:
    """Repair locations like 'Aditya Goyal\\nJaipur, India' → 'Jaipur, India'.

    Resume parsing sometimes prepends the candidate's name (and newlines) to
    the location; LinkedIn rejects that in its Easy Apply location field.
    """
    raw = str(profile.get("location") or "")
    name = str(profile.get("name") or "").strip()
    lines = [ln.strip() for ln in raw.replace("\r", "\n").split("\n") if ln.strip()]
    lines = [ln for ln in lines if not name or ln.lower() != name.lower()]
    loc = " ".join(lines) if lines else ""
    loc = loc.split("|")[0].strip()
    loc = re.sub(r"\s+", " ", loc)
    if not loc or loc.lower() == name.lower():
        loc = "Jaipur, India"
    return loc


def _select_typeahead(page: Page) -> bool:
    """Select the first visible autocomplete suggestion (LinkedIn location box)."""
    try:
        opts = page.locator(
            '[role="option"]:visible, .artdeco-typeahead-result:visible, '
            'ul[role="listbox"] li:visible'
        )
        if opts.count() > 0:
            opts.first.click(timeout=2000)
            return True
    except Exception:
        pass
    return False


def _try_fill(locator: Locator, value: str) -> bool:
    if not value:
        return False
    try:
        if locator.count() == 0:
            return False
        target = locator.first
        if not target.is_visible():
            return False
        target.fill(value)
        return True
    except Exception:
        return False


def _profile_years(profile: dict) -> int:
    return max(len(profile.get("experience") or []), 2)


def repair_invalid_fields(page: Page, profile: dict, modal: Locator | None = None) -> tuple[int, int]:
    """Fix fields LinkedIn's constraint validation rejects.

    Returns (fixed, still_invalid). Called before every Next/Review click so a
    bad value (stale draft text, wrong characters, out-of-range number) never
    blocks the modal from advancing. Uses native el.validity so we repair
    exactly what LinkedIn is complaining about.
    """
    years = _profile_years(profile)
    scope = modal if modal is not None else page.locator(
        "dialog, .jobs-easy-apply-modal, div[role='dialog']"
    )
    fields = scope.locator("input, textarea, select")
    fixed = remaining = 0
    try:
        count = min(fields.count(), 50)
    except Exception:
        return 0, 0

    for i in range(count):
        el = fields.nth(i)
        try:
            if not el.is_visible():
                continue
            state = el.evaluate(
                """el => ({
                    invalid: !el.validity.valid,
                    valueMissing: !!el.validity.valueMissing,
                    rangeUnderflow: !!el.validity.rangeUnderflow,
                    rangeOverflow: !!el.validity.rangeOverflow,
                    tag: el.tagName.toLowerCase(),
                    type: (el.type || '').toLowerCase(),
                    value: el.value || '',
                    pattern: el.getAttribute('pattern') || '',
                    min: el.min || '',
                    max: el.max || '',
                    label: (((el.labels && el.labels[0]) ? el.labels[0].innerText : '')
                        + ' ' + (el.getAttribute('aria-label') || '')
                        + ' ' + (el.placeholder || '')).trim(),
                })"""
            )
        except Exception:
            continue
        if not state["invalid"]:
            continue

        tag, typ = state["tag"], state["type"]
        hint = (state["label"] or "").lower()
        try:
            if tag == "select" and state["valueMissing"]:
                el.select_option(index=1)
                fixed += 1
                continue
            if typ in ("radio", "checkbox"):
                if state["valueMissing"]:
                    name = el.get_attribute("name") or ""
                    opts = (
                        page.locator(f"input[name='{name}']")
                        if name
                        else el.locator("xpath=ancestor::fieldset[1]//input")
                    )
                    for j in range(min(opts.count(), 6)):
                        cand = opts.nth(j)
                        if cand.is_visible() and cand.is_enabled():
                            cand.check()
                            break
                    fixed += 1
                continue

            new_value: str | None = None
            if state["valueMissing"]:
                if "year" in hint or "how many" in hint:
                    new_value = str(years)
                elif "city" in hint or "location" in hint:
                    new_value = clean_location(profile)
                elif typ == "email" or "email" in hint:
                    new_value = profile.get("email", "")
                elif any(k in hint for k in ("linkedin", "github", "portfolio", "website")):
                    new_value = (
                        profile.get("linkedin")
                        or profile.get("github")
                        or profile.get("portfolio")
                        or ""
                    )
                elif any(k in hint for k in ("why", "describe", "tell us", "interested", "motivat", "cover")):
                    new_value = (
                        "I'm a strong match for this role and my resume has further detail. "
                        "I'd welcome the chance to contribute quickly."
                    )
            elif state["rangeUnderflow"]:
                try:
                    new_value = str(int(float(state["min"] or years)))
                except ValueError:
                    new_value = str(years)
            elif state["rangeOverflow"]:
                try:
                    new_value = str(int(float(state["max"] or years)))
                except ValueError:
                    new_value = str(years)
            else:
                digits = re.sub(r"\D", "", state["value"])
                if digits:
                    new_value = digits
                elif "year" in hint:
                    new_value = str(years)
                else:
                    cleaned = re.sub(r"[^\w @.:/+#-]", "", state["value"]).strip()
                    new_value = cleaned or None

            if not new_value:
                remaining += 1
                logger.warning(
                    "Invalid field not auto-fixable: label=%r value=%r pattern=%r min=%r max=%r",
                    state["label"], state["value"], state["pattern"], state["min"], state["max"],
                )
                continue

            el.fill(new_value)
            el.press("Tab")
            still = el.evaluate("el => !el.validity.valid")
            if still:
                remaining += 1
                logger.warning(
                    "Field still invalid after repair: label=%r value=%r pattern=%r min=%r max=%r",
                    state["label"], new_value, state["pattern"], state["min"], state["max"],
                )
            else:
                fixed += 1
                logger.info("Repaired invalid field %r -> %r", state["label"][:60], new_value)
        except Exception as exc:
            remaining += 1
            logger.debug("Repair failed for %r: %s", state["label"], exc)

    return fixed, remaining


def fill_standard_fields(page: Page, profile: dict) -> int:
    """Fill common application fields. Returns count of fields filled."""
    filled = 0
    name = profile.get("name", "")
    email = profile.get("email", "")
    phone = profile.get("phone", "")
    location = clean_location(profile)
    city = location.split(",")[0].strip()
    linkedin = profile.get("linkedin", "")
    github = profile.get("github", "") or profile.get("portfolio", "")

    pairs: list[tuple[str, str]] = [
        ("input[name*='email' i]", email),
        ("input[type='email']", email),
        ("input[name*='phone' i]", phone),
        ("input[type='tel']", phone),
        ("input[name*='first' i]", _first_name(name)),
        ("input[name*='last' i]", _last_name(name)),
        ("input[name*='name' i]", name),
        ("input[placeholder*='email' i]", email),
        ("input[placeholder*='phone' i]", phone),
        ("input[placeholder*='name' i]", name),
        ("input[name*='location' i]", location),
        ("input[name*='city' i]", city),
        ("input[placeholder*='location' i]", location),
        ("input[placeholder*='city' i]", city),
        ("input[aria-label*='location' i]", location),
        ("input[aria-label*='city' i]", city),
        ("input[name*='linkedin' i]", linkedin),
        ("input[name*='github' i]", github),
        ("textarea[name*='cover' i]", profile.get("summary", "")[:2000]),
    ]

    location_filled = False
    for selector, value in pairs:
        if _try_fill(page.locator(selector), value):
            filled += 1
            if "location" in selector or "city" in selector:
                location_filled = True

    if location_filled:
        page.wait_for_timeout(900)
        _select_typeahead(page)

    # Label-based fills (Playwright get_by_label)
    label_map = {
        r"email": email,
        r"phone|mobile": phone,
        r"first name": _first_name(name),
        r"last name": _last_name(name),
        r"full name|^name$": name,
        r"location|city": location,
        r"linkedin": linkedin,
    }
    for pattern, value in label_map.items():
        if not value:
            continue
        try:
            loc = page.get_by_label(re.compile(pattern, re.I))
            if _try_fill(loc, value):
                filled += 1
        except Exception:
            pass

    logger.info("Filled %d standard fields", filled)
    filled += fill_custom_questions(page, profile)
    return filled


def fill_custom_questions(page: Page, profile: dict) -> int:
    """Fill common screening questions (work auth, sponsorship, experience)."""
    filled = 0
    skills = profile.get("skills") or []
    years = _profile_years(profile)

    qa_pairs: list[tuple[str, str]] = [
        (r"authorized|legally authorized|eligible to work|work authorization", "Yes"),
        (r"require sponsorship|visa sponsorship|need sponsorship", "No"),
        (r"willing to relocate", "Yes"),
        (r"remote|work from home", "Yes"),
        (r"years?.{0,5}experience|how many years", str(years)),
        (r"expected salary|desired salary|salary expectation|expected ctc", "Negotiable"),
        (r"linkedin profile|linkedin url", profile.get("linkedin", "")),
        (r"github|portfolio|website", profile.get("github", "") or profile.get("portfolio", "")),
        (r"how soon|start date|available to start|availability|notice period", "Immediately"),
        (r"how did you hear", "LinkedIn"),
        (r"gender|race|ethnicity|veteran|military status|disability", "Prefer not to answer"),
    ]

    for skill in skills[:8]:
        qa_pairs.append((rf"experience with {re.escape(skill)}|{re.escape(skill)}", "Yes"))

    for pattern, answer in qa_pairs:
        if not answer:
            continue
        try:
            loc = page.get_by_label(re.compile(pattern, re.I))
            if loc.count() > 0:
                target = loc.first
                tag = target.evaluate("el => el.tagName.toLowerCase()")
                lab = target.evaluate(
                    "el => (((el.labels && el.labels[0]) ? el.labels[0].innerText : '')"
                    " + ' ' + (el.getAttribute('aria-label') || '')).trim()"
                )
                effective = answer
                if answer == "Yes" and re.search(r"year|how many", lab, re.I):
                    effective = str(years)
                if tag == "select":
                    target.select_option(label=re.compile(effective, re.I))
                    filled += 1
                elif _try_fill(target, effective):
                    filled += 1
        except Exception:
            pass
        try:
            radio = page.get_by_role("radio", name=re.compile(answer, re.I))
            if radio.count() > 0 and radio.first.is_visible():
                radio.first.click()
                filled += 1
        except Exception:
            pass

    # Generic blank required text inputs in modals
    try:
        skills = profile.get("skills") or []
        blurb = (
            f"I'm a strong match for this role with hands-on experience in "
            f"{', '.join(skills[:4]) or 'Python and machine learning'}. "
            "My resume has further detail and I'd welcome the chance to contribute quickly."
        )
        for inp in page.locator("input[required]:not([type='hidden']), textarea[required]").all()[:6]:
            if not inp.is_visible():
                continue
            if inp.input_value():
                continue
            placeholder = (inp.get_attribute("placeholder") or "").lower()
            name = (inp.get_attribute("name") or "").lower()
            hint = placeholder + " " + name
            if "year" in hint:
                inp.fill(str(years))
                filled += 1
            elif "city" in hint or "location" in hint:
                inp.fill(clean_location(profile))
                filled += 1
            elif any(k in hint for k in ("why", "describe", "tell us", "interested", "motivat", "cover")):
                inp.fill(blurb)
                filled += 1
    except Exception:
        pass

    return filled


def fill_greenhouse_fields(page: Page, profile: dict) -> int:
    """Greenhouse uses predictable field names on boards.greenhouse.io."""
    filled = fill_standard_fields(page, profile)
    name = profile.get("name", "")
    email = profile.get("email", "")
    phone = profile.get("phone", "")
    linkedin = profile.get("linkedin", "")

    gh_pairs: list[tuple[str, str]] = [
        ("#first_name", _first_name(name)),
        ("#last_name", _last_name(name)),
        ("#email", email),
        ("#phone", phone),
        ("input[name='job_application[first_name]']", _first_name(name)),
        ("input[name='job_application[last_name]']", _last_name(name)),
        ("input[name='job_application[email]']", email),
        ("input[name='job_application[phone]']", phone),
        ("input[name*='linkedin' i]", linkedin),
        ("input[name*='website' i]", profile.get("github", "") or profile.get("portfolio", "")),
    ]
    for selector, value in gh_pairs:
        if _try_fill(page.locator(selector), value):
            filled += 1

    logger.info("Greenhouse: filled %d fields total", filled)
    return filled


def upload_resume(page: Page, resume_path: str | Path) -> bool:
    path = Path(resume_path)
    if not path.exists():
        logger.warning("Resume not found: %s", path)
        return False

    selectors = (
        "input[type='file']",
        "input[accept*='pdf' i]",
        "input[name*='resume' i]",
        "input[name*='cv' i]",
    )
    for selector in selectors:
        try:
            loc = page.locator(selector)
            if loc.count() == 0:
                continue
            loc.first.set_input_files(str(path.resolve()))
            logger.info("Uploaded resume via %s", selector)
            return True
        except Exception as exc:
            logger.debug("Upload failed for %s: %s", selector, exc)

    return False


def click_first_matching(page: Page, texts: list[str], *, timeout_ms: int = 5000) -> bool:
    for text in texts:
        try:
            btn = page.get_by_role("button", name=re.compile(text, re.I))
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click(timeout=timeout_ms)
                logger.info("Clicked button matching %r", text)
                return True
        except Exception:
            pass
        try:
            link = page.get_by_role("link", name=re.compile(text, re.I))
            if link.count() > 0 and link.first.is_visible():
                link.first.click(timeout=timeout_ms)
                logger.info("Clicked link matching %r", text)
                return True
        except Exception:
            pass
    return False


def click_apply_entrypoint(page: Page) -> bool:
    return click_first_matching(
        page,
        [
            r"apply for this job",
            r"^easy apply$",
            r"apply now",
            r"quick apply",
            r"1.?click apply",
            r"^apply$",
            r"continue to apply",
            r"start application",
        ],
    )
