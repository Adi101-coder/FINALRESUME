"""Generic form filling helpers for job application pages."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from playwright.sync_api import Locator, Page

from app.agents.browser.captcha import pause_for_manual_fill
from app.config import settings

logger = logging.getLogger("app.browser.forms")


def phone_for_apply(profile: dict) -> str:
    """Phone from the resume, else AUTO_APPLY_PHONE so Easy Apply can proceed."""
    parsed = (profile.get("phone") or "").strip()
    if parsed:
        return parsed
    return (settings.auto_apply_phone or "").strip()


def phone_country_label(profile: dict) -> str:
    """LinkedIn 'Phone country code' option (e.g. India / +91)."""
    phone = re.sub(r"[^\d+]", "", phone_for_apply(profile))
    loc = clean_location(profile).lower()
    if phone.startswith("+91") or phone.startswith("91") and len(phone) >= 12:
        return "India"
    if "india" in loc or "jaipur" in loc or "bengaluru" in loc or "bangalore" in loc:
        return "India"
    digits = re.sub(r"\D", "", phone)
    if re.fullmatch(r"[6-9]\d{9}", digits or ""):
        return "India"
    if phone.startswith("+1") or "united states" in loc or loc.endswith("usa"):
        return "United States"
    return "India"


def modal_has_validation_errors(scope: Locator) -> bool:
    try:
        if scope.get_by_text(re.compile(r"this field is required", re.I)).count() > 0:
            return True
        if scope.locator("[aria-invalid='true']:visible").count() > 0:
            return True
    except Exception:
        pass
    return False


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
    if loc.lower() == name.lower():
        loc = ""
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
    """Actual years of experience from the resume (0 for freshers).

    Never use the number of experience entries — a fresher with 4 projects/
    internships listed must answer 0 years, not 4.
    """
    from app.services.experience_level import estimate_experience_years

    years = estimate_experience_years(profile)
    if years is None:
        return 0
    return max(0, int(round(years)))


def _years_option_index(labels: list[str], years: int) -> int | None:
    """Index of the <select> option for `years` without substring traps
    (the answer "0" must never match "10+ years")."""
    num = str(years)
    pat = re.compile(rf"(?<!\d){re.escape(num)}(?!\d)")
    for i, lab in enumerate(labels):
        if lab and pat.search(lab):
            return i
    if years == 0:
        for i, lab in enumerate(labels):
            low = (lab or "").lower()
            if "less than 1" in low or "<1" in low or "no experience" in low:
                return i
    return None


def _select_answer(target, answer: str) -> bool:
    """Answer a <select> field; numeric answers pick a boundary-safe option."""
    if not answer:
        return False
    if _select_native_option(target, answer):
        return True
    aliases = _answer_aliases(answer)
    for alias in aliases:
        try:
            target.select_option(label=re.compile(re.escape(alias), re.I))
            return True
        except Exception:
            continue
    return False


def _answer_aliases(answer: str) -> list[str]:
    low = answer.lower()
    aliases = [answer]
    if low in ("prefer not to answer", "prefer not to say"):
        aliases.extend(
            [
                "Decline to self-identify",
                "I don't wish to answer",
                "Don't wish to answer",
                "Prefer not to say",
                "Prefer not to answer",
            ]
        )
    if low == "negotiable":
        aliases.extend(["Open", "Flexible", "Depends on benefits", "Competitive"])
    if low == "immediately":
        aliases.extend(["ASAP", "Immediate", "2 weeks", "Within 2 weeks"])
    if low == "yes":
        aliases.extend(["Yes, I am", "I am authorized", "Authorized"])
    if low == "no":
        aliases.extend(["No, I am not", "Not required", "I do not require"])
    return aliases


def _option_matches(option_text: str, desired: str) -> bool:
    opt = re.sub(r"\s+", " ", (option_text or "").strip().lower())
    want = re.sub(r"\s+", " ", (desired or "").strip().lower())
    if not opt or not want:
        return False
    if opt == want:
        return True
    if want.isdigit():
        return bool(re.search(rf"(?<!\d){re.escape(want)}(?!\d)", opt))
    if want in opt or opt in want:
        return True
    for month in _MONTH_NAMES:
        m3 = month[:3].lower()
        if want == month.lower() or want.startswith(m3):
            if m3 in opt or month.lower() in opt:
                return True
    return False


def _select_native_option(select, label: str) -> bool:
    try:
        options = select.evaluate(
            """el => Array.from(el.options || []).map(o => ({
                label: (o.label || o.textContent || '').trim(),
                value: (o.value || '').trim(),
                disabled: !!o.disabled,
            }))"""
        ) or []
    except Exception:
        return False
    if not options:
        return False

    if label.isdigit():
        idx = _years_option_index([o["label"] for o in options], int(label))
        if idx is not None:
            try:
                select.select_option(index=idx)
                return True
            except Exception:
                pass

    for i, opt in enumerate(options):
        if opt.get("disabled"):
            continue
        lab = opt.get("label") or ""
        val = opt.get("value") or ""
        if re.search(r"select|choose|month|year", lab, re.I) and not val:
            continue
        if _option_matches(lab, label) or _option_matches(val, label):
            try:
                select.select_option(index=i)
                return True
            except Exception:
                continue
    return False


def resume_value_for_hint(hint: str, profile: dict) -> str:
    """Single source of truth: form values derivable only from the parsed resume."""
    text = re.sub(r"\s+", " ", (hint or "")).strip()
    if not text:
        return ""

    years = _profile_years(profile)
    date_pick = _select_repair_value(text.lower(), profile, years)
    if date_pick:
        return date_pick

    answer = _resume_answer(text, profile)
    if answer:
        return answer

    skills = [str(s) for s in (profile.get("skills") or []) if s]
    low = text.lower()
    for skill in skills[:15]:
        skill_l = skill.lower()
        if skill_l and skill_l in low:
            if re.search(r"how many years|years of", low):
                return str(years)
            if re.search(r"experience with|proficiency|familiar|knowledge of|have you used", low):
                return "Yes"
    return ""


def _screening_answer_for_hint(hint: str, profile: dict) -> str:
    """Answer screening controls only from parsed resume data."""
    return resume_value_for_hint(hint, profile)


def _field_is_empty(el: Locator) -> bool:
    try:
        tag = (el.evaluate("el => el.tagName") or "").lower()
        typ = (el.get_attribute("type") or "").lower()
        if typ in ("radio", "checkbox"):
            name = el.get_attribute("name") or ""
            if name:
                group = el.page.locator(f"input[name='{name}']")
                for i in range(min(group.count(), 12)):
                    if group.nth(i).is_checked():
                        return False
            return not el.is_checked()
        if tag == "select":
            idx = el.evaluate("el => el.selectedIndex")
            lab = el.evaluate(
                "el => el.selectedIndex >= 0 ? (el.options[el.selectedIndex].label || '').trim() : ''"
            )
            if idx in (None, -1, 0) and re.search(r"select|choose", lab or "", re.I):
                return True
            return not (lab or "").strip()
        return not (el.input_value() or "").strip()
    except Exception:
        return True


def needs_user_input_for_resume_gaps(page: Page, profile: dict, modal: Locator | None = None) -> bool:
    """True when required visible fields lack an answer on the uploaded resume."""
    scope = modal if modal is not None else page.locator(
        "dialog, .jobs-easy-apply-modal, div[role='dialog']"
    )
    try:
        if modal_has_validation_errors(scope):
            return True
    except Exception:
        pass

    years = _profile_years(profile)
    selectors = (
        "input:not([type='hidden']):not([type='file']):not([type='submit'])",
        "textarea",
        "select",
        '[role="combobox"]',
    )
    seen: set[str] = set()
    for sel in selectors:
        try:
            controls = scope.locator(sel)
            count = min(controls.count(), 40)
        except Exception:
            continue
        for i in range(count):
            el = controls.nth(i)
            try:
                if not el.is_visible():
                    continue
                if not _field_is_empty(el):
                    continue
                required = el.evaluate(
                    "el => el.required || el.getAttribute('aria-required') === 'true'"
                )
                hint = _control_hint(el)
                key = hint[:80]
                if key in seen:
                    continue
                seen.add(key)
                if not required and not re.search(
                    r"this field is required", scope.inner_text() or "", re.I
                ):
                    # Still treat empty comboboxes on experience screens as gaps.
                    if not re.search(r"month|year|combobox", hint, re.I):
                        continue
                if not resume_value_for_hint(hint, profile) and not _select_repair_value(
                    hint.lower(), profile, years
                ):
                    logger.info("Resume gap — user input needed: %r", hint[:100])
                    return True
            except Exception:
                continue

    for selector in ("fieldset", "[role='radiogroup']"):
        try:
            groups = scope.locator(selector)
            for i in range(min(groups.count(), 20)):
                group = groups.nth(i)
                if not group.is_visible():
                    continue
                hint = _control_hint(group)
                if not hint:
                    continue
                checked = group.locator("input[type='radio']:checked, input[type='checkbox']:checked")
                if checked.count() > 0:
                    continue
                if not resume_value_for_hint(hint, profile):
                    logger.info("Resume gap (choice question): %r", hint[:100])
                    return True
        except Exception:
            continue
    return False


def wait_for_user_if_resume_gaps(
    page: Page, profile: dict, modal: Locator | None = None, *, reason: str | None = None
) -> bool:
    """Pause without bot clicks so the user can fill fields missing from the resume."""
    scope = modal if modal is not None else page.locator(
        "dialog, .jobs-easy-apply-modal, div[role='dialog']"
    )
    if not needs_user_input_for_resume_gaps(page, profile, scope):
        return False
    pause_for_manual_fill(
        page,
        reason
        or (
            "Some answers are not on your resume. "
            "Fill them in the browser — the bot will not click for "
            f"{settings.auto_apply_manual_fill_sec} seconds."
        ),
        timeout_sec=settings.auto_apply_manual_fill_sec,
    )
    fill_from_resume_only(page, profile)
    return True


def fill_from_resume_only(page: Page, profile: dict) -> int:
    """Fill every field we can strictly from the parsed resume profile."""
    return fill_standard_fields(page, profile)


def _click_answer_in_container(container: Locator, answer: str) -> bool:
    if not answer:
        return False
    for alias in _answer_aliases(answer):
        try:
            radio = container.get_by_role("radio", name=re.compile(re.escape(alias), re.I))
            if radio.count() > 0 and radio.first.is_visible():
                radio.first.click(timeout=2000)
                return True
        except Exception:
            pass
        try:
            label = container.locator("label").filter(has_text=re.compile(re.escape(alias), re.I))
            if label.count() > 0 and label.first.is_visible():
                label.first.click(timeout=2000)
                return True
        except Exception:
            pass
        try:
            option = container.get_by_role("button", name=re.compile(re.escape(alias), re.I))
            if option.count() > 0 and option.first.is_visible():
                option.first.click(timeout=2000)
                return True
        except Exception:
            pass
    return False


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
                pick = _select_repair_value(hint, profile, years) or resume_value_for_hint(
                    state["label"] or hint, profile
                )
                if pick and (_select_native_option(el, pick) or _choose_dropdown(el, pick)):
                    fixed += 1
                else:
                    remaining += 1
                continue
            if "country code" in hint and state["valueMissing"]:
                if _fill_phone_country_code(page, profile):
                    fixed += 1
                else:
                    remaining += 1
                continue
            if typ in ("radio", "checkbox"):
                if state["valueMissing"]:
                    container = el.locator("xpath=ancestor::fieldset[1]")
                    if container.count() == 0:
                        container = scope
                    group_hint = state["label"] or _control_hint(el)
                    answer = _screening_answer_for_hint(group_hint, profile)
                    if answer and _click_answer_in_container(container, answer):
                        fixed += 1
                    else:
                        remaining += 1
                continue

            new_value: str | None = None
            full_hint = state["label"] or ""
            if state["valueMissing"]:
                new_value = resume_value_for_hint(full_hint, profile) or None
            elif state["rangeUnderflow"] or state["rangeOverflow"]:
                resume_val = resume_value_for_hint(full_hint, profile)
                if resume_val and resume_val.isdigit():
                    new_value = resume_val
                elif "year" in hint or "how many" in hint:
                    new_value = str(years)
            else:
                resume_val = resume_value_for_hint(full_hint, profile)
                if resume_val:
                    new_value = resume_val
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


def _select_repair_value(hint: str, profile: dict, years: int) -> str:
    h = (hint or "").lower()
    exp = _primary_experience(profile)
    start_month, start_year, _ = _parse_resume_date(str(exp.get("start_date") or ""))
    end_month, end_year, current = _parse_resume_date(str(exp.get("end_date") or ""))
    if not start_year:
        start_year = _fallback_year(profile)

    if "country code" in h or "dial code" in h or "phone country" in h:
        return phone_country_label(profile)
    if "from month" in h or (h.endswith("month") and "to " not in h):
        return start_month
    if "to month" in h or "end month" in h:
        return end_month
    if "from year" in h or (h.endswith("year") and "to " not in h and "how many" not in h):
        return start_year
    if "to year" in h or "end year" in h:
        return end_year if not current else ""
    if "year" in h or "how many" in h:
        return str(years)
    if "month" in h:
        return start_month
    return ""


def _fill_phone_country_code(page: Page, profile: dict) -> int:
    """Set LinkedIn's required Phone country code combobox (India / +91)."""
    label = phone_country_label(profile)
    if not label:
        return 0
    dialog = page.locator("div[role='dialog'], .jobs-easy-apply-modal")
    root = dialog.last if dialog.count() > 0 else page
    locators = [
        root.get_by_label(re.compile(r"phone country code|country code", re.I)),
        root.locator('[aria-label*="country code" i]'),
        root.locator('select[id*="country" i], select[name*="country" i]'),
    ]
    for loc in locators:
        try:
            if loc.count() == 0:
                continue
            target = loc.first
            if not target.is_visible():
                continue
            if _choose_dropdown(target, label) or _choose_dropdown(target, "+91"):
                logger.info("Set phone country code to %s", label)
                return 1
        except Exception:
            continue
    return 0


def fill_standard_fields(page: Page, profile: dict) -> int:
    """Fill common application fields. Returns count of fields filled."""
    filled = 0
    name = profile.get("name", "")
    email = profile.get("email", "")
    phone = phone_for_apply(profile)
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

    filled += _fill_phone_country_code(page, profile)

    # Label-based fills (Playwright get_by_label)
    label_map = {
        r"email": email,
        r"phone|mobile": phone,
        r"first name": _first_name(name),
        r"last name": _last_name(name),
        r"full name|^name$": name,
        r"location|city": location,
        r"linkedin": linkedin,
        r"website.*portfolio|blog or portfolio": _portfolio_url(profile),
        r"desired salary|expected salary": _salary_from_resume(profile),
        r"expected graduation|graduation date": _expected_graduation_display(profile),
    }
    for pattern, value in label_map.items():
        if not value:
            continue
        try:
            loc = page.get_by_label(re.compile(pattern, re.I))
            if _try_fill(loc, value):
                filled += 1
            elif loc.count() > 0:
                tag = loc.first.evaluate("el => el.tagName.toLowerCase()")
                if tag == "select" and (_select_answer(loc.first, value) or _choose_dropdown(loc.first, value)):
                    filled += 1
        except Exception:
            pass

    logger.info("Filled %d standard fields", filled)
    filled += fill_linkedin_easy_apply_sections(page, profile)
    filled += fill_grouped_questions(page, profile)
    filled += fill_custom_questions(page, profile)
    filled += fill_required_comboboxes(page, profile)
    filled += fill_experience_editor(page, profile)
    filled += fill_resume_fields(page, profile)
    return filled


def fill_linkedin_easy_apply_sections(page: Page, profile: dict) -> int:
    """Fill LinkedIn Easy Apply blocks (long labels + select/combobox/radio)."""
    dialog = page.locator("div[role='dialog'], .jobs-easy-apply-modal")
    if dialog.count() == 0:
        return 0
    root = dialog.last
    filled = 0
    sections = root.locator(
        ".jobs-easy-apply-form-section__group, "
        ".jobs-easy-apply-form-element, "
        "div[data-test-form-element]"
    )
    try:
        count = min(sections.count(), 30)
    except Exception:
        return 0

    for i in range(count):
        block = sections.nth(i)
        try:
            if not block.is_visible():
                continue
            hint = (block.inner_text() or "").strip()[:600]
            if not hint:
                continue
            answer = resume_value_for_hint(hint, profile)
            if not answer:
                continue

            for sel in block.locator("select:visible").all()[:2]:
                if _field_is_empty(sel) and (
                    _select_answer(sel, answer) or _choose_dropdown(sel, answer)
                ):
                    filled += 1

            for combo in block.locator('[role="combobox"]:visible').all()[:2]:
                if _choose_dropdown(combo, answer):
                    filled += 1

            for inp in block.locator(
                "input:not([type='hidden']):not([type='file']):not([type='radio']):not([type='checkbox'])"
            ).all()[:3]:
                if not inp.is_visible() or not _field_is_empty(inp):
                    continue
                if _try_fill(inp, answer):
                    filled += 1

            if _click_answer_in_container(block, answer):
                filled += 1
        except Exception:
            continue

    if filled:
        logger.info("Filled %d LinkedIn Easy Apply section fields from resume", filled)
    return filled


def _control_hint(target) -> str:
    try:
        return target.evaluate(
            """el => {
                const bits = [
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('placeholder') || '',
                    el.getAttribute('name') || '',
                    el.id || '',
                ];
                if (el.labels) {
                    for (const label of el.labels) bits.push(label.innerText || '');
                }
                const block = el.closest('div, fieldset, li');
                const heading = block && block.querySelector('label, legend, span');
                if (heading) bits.push(heading.innerText || '');
                return bits.join(' ').replace(/\\s+/g, ' ').trim().slice(0, 300);
            }"""
        ) or ""
    except Exception:
        return ""


def _education_list(profile: dict) -> list[dict]:
    return [item for item in (profile.get("education") or []) if isinstance(item, dict)]


def _graduation_year(profile: dict) -> int | None:
    years: list[int] = []
    for edu in _education_list(profile):
        for key in ("end_date", "start_date"):
            for match in re.finditer(r"(?:19|20)\d{2}", str(edu.get(key) or "")):
                years.append(int(match.group(0)))
    return max(years) if years else None


def _expected_graduation_display(profile: dict) -> str:
    for edu in _education_list(profile):
        end = str(edu.get("end_date") or "").strip()
        if not end:
            continue
        if re.search(r"present|expected|anticipated", end, re.I):
            year = _graduation_year(profile)
            return f"May {year}" if year else end
        month, year, _ = _parse_resume_date(end)
        if year and month:
            return f"{month} {year}"
        if year:
            return year
        return end
    year = _graduation_year(profile)
    return str(year) if year else ""


def _enrolled_through_hint(hint: str, profile: dict) -> str:
    """Yes/No when resume education extends through a year mentioned in the question."""
    match = re.search(r"(?:summer|through|until)\s+(\d{4})", hint, re.I)
    if not match:
        match = re.search(r"(\d{4})\?", hint)
    if not match:
        return ""
    through = int(match.group(1))
    grad = _graduation_year(profile)
    if grad is not None and grad >= through:
        return "Yes"
    for edu in _education_list(profile):
        end = str(edu.get("end_date") or "")
        if re.search(r"present|expected|current", end, re.I):
            return "Yes"
    return ""


def _work_auth_without_sponsorship(profile: dict) -> str:
    raw = (profile.get("raw_text") or "").lower()
    if re.search(
        r"u\.?s\.?\s*citizen|authorized to work in the (?:u\.?s\.?|united states)"
        r"|permanent resident|green card|no sponsorship required|do not require sponsorship",
        raw,
    ):
        return "Yes"
    if re.search(r"require sponsorship|need sponsorship|visa sponsorship|f-1.*sponsor", raw):
        return "No"
    return ""


def _salary_from_resume(profile: dict) -> str:
    raw = profile.get("raw_text") or ""
    for pattern in (
        r"(?:expected|desired)\s+salary[:\s]*\$?\s*([\d,]+(?:\.\d+)?)\s*(?:k|K)?",
        r"(?:expected|desired)\s+ctc[:\s]*([\d,]+(?:\.\d+)?)\s*(?:lpa|LPA)?",
    ):
        match = re.search(pattern, raw, re.I)
        if match:
            return match.group(1).replace(",", "")
    return ""


def _onsite_willingness(hint: str, profile: dict) -> str:
    raw = (profile.get("raw_text") or "").lower()
    if re.search(r"willing to relocate|open to relocate|relocate for|on-?site", raw):
        return "Yes"
    loc = clean_location(profile).lower()
    for place in re.findall(r"\b([A-Za-z][A-Za-z\s]{2,30}),?\s*([A-Z]{2})\b", hint):
        city, state = place[0].lower(), place[1].lower()
        if city in loc or state in loc:
            return "Yes"
    return ""


def _portfolio_url(profile: dict) -> str:
    for key in ("portfolio", "github", "linkedin"):
        val = (profile.get(key) or "").strip()
        if val:
            return val
    return ""


def _resume_answer(hint: str, profile: dict) -> str:
    """Pick a resume fact for a form label. Empty string means leave the field alone."""
    text = re.sub(r"\s+", " ", hint or "").strip().lower()
    if not text or re.search(r"\b(search|password|captcha)\b", text):
        return ""

    name = profile.get("name") or ""
    exp = _primary_experience(profile)
    education = _education_list(profile)
    edu = education[0] if education else {}
    skills = [str(skill) for skill in (profile.get("skills") or []) if skill]
    summary = (profile.get("summary") or "").strip()
    bullets = exp.get("bullets") or []
    description = summary or "\n".join(str(b).strip() for b in bullets if str(b).strip())

    rules: list[tuple[str, str]] = [
        (r"e-?mail", profile.get("email") or ""),
        (r"country code|dial code|phone country", phone_country_label(profile)),
        (r"phone|mobile|contact number", phone_for_apply(profile)),
        (r"first name", _first_name(name)),
        (r"last name|surname", _last_name(name)),
        (r"linkedin", profile.get("linkedin") or ""),
        (
            r"website.*portfolio|blog or portfolio|portfolio|personal website|github",
            _portfolio_url(profile),
        ),
        (r"desired salary|expected salary|salary expectation", _salary_from_resume(profile)),
        (
            r"expected graduation|graduation date|date of graduation",
            _expected_graduation_display(profile),
        ),
        (
            r"actively enrolled|enrolled in.*(degree|program)|degree program through",
            _enrolled_through_hint(hint, profile),
        ),
        (
            r"work on-site|on-site.*internship|able to work on-site|in-person position",
            _onsite_willingness(hint, profile),
        ),
        (
            r"authorized.*work.*(u\.?s\.?|united states).*without.*sponsorship"
            r"|legally authorized.*without.*sponsorship",
            _work_auth_without_sponsorship(profile),
        ),
        (r"github|portfolio|personal website|^website", _portfolio_url(profile)),
        (r"school|university|college|institution", edu.get("institution") or ""),
        (r"field of study|major", edu.get("field") or ""),
        (r"\bdegree\b", edu.get("degree") or ""),
        (r"\b(gpa|cgpa|grade)\b", edu.get("cgpa") or ""),
        (r"your title|job title|position(?!s)|current role|\brole\b", exp.get("role") or ""),
        (r"\b(company|employer|organization)\b", exp.get("company") or ""),
        (r"\b(city|location)\b", clean_location(profile) or (exp.get("location") or "").strip()),
        (r"skill", ", ".join(skills[:12])),
        (r"years?.{0,12}experience|how many years", str(_profile_years(profile))),
        (r"notice|available to start|start date|how soon", (profile.get("notice_period") or "").strip()),
        (r"summary|about you|cover letter|why.*(role|company|interested)|description|responsib", description[:1800]),
        (r"\b(full name|^name$|your name)\b", name),
    ]
    for pattern, value in rules:
        if value and re.search(pattern, text):
            return str(value).strip()
    return ""


def fill_resume_fields(page: Page, profile: dict) -> int:
    """Fill whatever the current form asks, using only facts from the parsed resume."""
    dialog = page.locator("div[role='dialog'], .jobs-easy-apply-modal")
    root = dialog.last if dialog.count() > 0 else page
    controls = root.locator(
        "input:not([type='hidden']):not([type='file']):not([type='checkbox']):not([type='radio']):not([type='submit']), textarea, select"
    )
    filled = 0
    try:
        count = min(controls.count(), 55)
    except Exception:
        return 0

    for index in range(count):
        target = controls.nth(index)
        try:
            if not target.is_visible():
                continue
            tag = (target.evaluate("el => el.tagName") or "").lower()
            current = ""
            if tag != "select":
                current = (target.input_value() or "").strip()
            elif target.evaluate("el => el.selectedIndex"):
                current = (target.input_value() or "").strip()
            if current:
                continue
            answer = _resume_answer(_control_hint(target), profile)
            if not answer:
                continue
            if tag == "select":
                if _choose_dropdown(target, answer) or _select_answer(target, answer):
                    filled += 1
            else:
                target.fill(answer[:2000])
                filled += 1
        except Exception:
            continue

    if filled:
        logger.info("Filled %d fields from the parsed resume", filled)
    return filled


_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _parse_resume_date(value: str) -> tuple[str, str, bool]:
    text = (value or "").strip()
    current = bool(re.search(r"present|current|now|ongoing", text, re.I))
    year_match = re.search(r"(?:19|20)\d{2}", text)
    year = year_match.group(0) if year_match else ""
    month = ""
    for name in _MONTH_NAMES:
        if re.search(rf"\b{name[:3]}\w*\b", text, re.I):
            month = name
            break
    return month, year, current


def _fallback_year(profile: dict) -> str:
    for edu in profile.get("education") or []:
        if not isinstance(edu, dict):
            continue
        for key in ("end_date", "start_date"):
            match = re.search(r"(?:19|20)\d{2}", str(edu.get(key) or ""))
            if match:
                return match.group(0)
    return ""


def _extract_dates_from_merged_text(text: str) -> tuple[str, str, str]:
    """Split 'Acme Corp Jun 2024 – Present' into company + date span."""
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return "", "", ""
    range_match = re.search(
        r"^(.*?)\s+("
        r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        r"\.?\s*\d{4}\s*[-–—]\s*(?:Present|Current|Now|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z.]*\s*\d{4})"
        r")\s*$",
        raw,
        re.I,
    )
    if range_match:
        return range_match.group(1).strip(), range_match.group(2).strip(), ""
    single = re.search(
        r"^(.*?)\s+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z.]*\s*\d{4})\s*$",
        raw,
        re.I,
    )
    if single:
        return single.group(1).strip(), single.group(2).strip(), ""
    return raw, "", ""


def _normalize_experience_entry(exp: dict) -> dict:
    out = dict(exp)
    company = str(out.get("company") or "").strip()
    role = str(out.get("role") or "").strip()
    if company and re.search(r"\d{4}", company):
        co, start, _ = _extract_dates_from_merged_text(company)
        if co:
            out["company"] = co
        if start and not out.get("start_date"):
            out["start_date"] = start
    if role and len(role) > 48 and re.search(r"\d{4}", role):
        co, start, _ = _extract_dates_from_merged_text(role)
        if co:
            out["role"] = co
        if start and not out.get("start_date"):
            out["start_date"] = start
    return out


def _primary_experience(profile: dict) -> dict:
    items = [_normalize_experience_entry(item) for item in (profile.get("experience") or []) if isinstance(item, dict)]
    if items:
        return items[0]
    projects = [item for item in (profile.get("projects") or []) if isinstance(item, dict)]
    project = projects[0] if projects else {}
    roles = profile.get("preferred_roles") or []
    bullets = project.get("bullets") or []
    if project.get("description"):
        bullets = [project["description"], *bullets]
    return {
        "role": (roles[0] if roles else "") or project.get("name") or "",
        "company": project.get("name") or "",
        "location": profile.get("location") or "",
        "start_date": "",
        "end_date": "",
        "bullets": bullets,
    }


def _choose_dropdown(locator: Locator, label: str) -> bool:
    if not label or locator.count() == 0:
        return False
    target = locator.first
    try:
        tag = (target.evaluate("el => el.tagName") or "").lower()
    except Exception:
        tag = ""
    try:
        if tag == "select":
            return _select_native_option(target, label)
        target.click(timeout=2000)
        target.page.wait_for_timeout(350)
        listboxes = target.page.locator('[role="listbox"]:visible')
        for box_idx in range(min(listboxes.count(), 3)):
            listbox = listboxes.nth(box_idx)
            options = listbox.locator('[role="option"]')
            for i in range(min(options.count(), 40)):
                try:
                    text = (options.nth(i).inner_text() or "").strip()
                    if _option_matches(text, label):
                        options.nth(i).click(timeout=2000)
                        return True
                except Exception:
                    continue
        for alias in _answer_aliases(label):
            option = target.page.get_by_role("option", name=re.compile(re.escape(alias), re.I))
            if option.count() > 0:
                option.first.click(timeout=2000)
                return True
    except Exception as exc:
        logger.debug("Dropdown %r failed: %s", label, exc)
    return False


def fill_experience_editor(page: Page, profile: dict) -> int:
    """Fill LinkedIn Easy Apply 'Edit experience' from the parsed resume."""
    dialog = page.locator("div[role='dialog'], .jobs-easy-apply-modal")
    if dialog.count() == 0:
        return 0
    root = dialog.last
    try:
        body = (root.inner_text() or "").lower()
    except Exception:
        return 0
    if "your title" not in body and "edit experience" not in body:
        return 0

    exp = _primary_experience(profile)
    title = (exp.get("role") or "").strip()
    company = (exp.get("company") or "").strip()
    city = (exp.get("location") or profile.get("location") or "").strip()
    bullets = exp.get("bullets") or []
    description = "\n".join(str(b).strip() for b in bullets if str(b).strip())[:1800]

    start_month, start_year, _ = _parse_resume_date(str(exp.get("start_date") or ""))
    end_month, end_year, current = _parse_resume_date(str(exp.get("end_date") or ""))
    if not start_year:
        start_year = _fallback_year(profile)
    if re.search(r"present|current|now", str(exp.get("end_date") or ""), re.I):
        current = True

    filled = 0
    city = clean_location({"location": city, "name": profile.get("name") or ""}) or city
    text_fields = (
        (r"your title", title),
        (r"^company$", company),
        (r"^city$", city),
        (r"^description$", description),
    )
    for pattern, value in text_fields:
        if not value:
            continue
        try:
            loc = root.get_by_label(re.compile(pattern, re.I))
            if _try_fill(loc, value):
                filled += 1
        except Exception:
            pass

    if current:
        try:
            box = root.get_by_role("checkbox", name=re.compile(r"currently work here", re.I))
            if box.count() > 0 and not box.first.is_checked():
                box.first.check(timeout=2000)
                filled += 1
        except Exception:
            pass

    try:
        start_months = root.get_by_label(re.compile(r"from\s+month|^month$", re.I))
        start_years = root.get_by_label(re.compile(r"from\s+year|^year$", re.I))
        if start_month and _choose_dropdown(start_months.first, start_month):
            filled += 1
        if start_year and _choose_dropdown(start_years.first, start_year):
            filled += 1
        if not current:
            end_months = root.get_by_label(re.compile(r"to\s+month|end\s+month", re.I))
            end_years = root.get_by_label(re.compile(r"to\s+year|end\s+year", re.I))
            if end_month and end_months.count() > 0 and _choose_dropdown(end_months.first, end_month):
                filled += 1
            if end_year and end_years.count() > 0 and _choose_dropdown(end_years.first, end_year):
                filled += 1
    except Exception as exc:
        logger.debug("Experience dates not filled: %s", exc)

    try:
        save = root.get_by_role("button", name=re.compile(r"^save$", re.I))
        if filled and save.count() > 0 and save.first.is_visible() and not modal_has_validation_errors(root):
            save.first.click(timeout=3000)
            page.wait_for_timeout(800)
            logger.info("Saved Easy Apply experience entry from resume")
        elif filled and modal_has_validation_errors(root):
            fill_required_comboboxes(page, profile, scope=root)
    except Exception as exc:
        logger.debug("Experience save click failed: %s", exc)

    logger.info("Filled %d experience-editor fields", filled)
    return filled


def fill_grouped_questions(page: Page, profile: dict, scope: Locator | None = None) -> int:
    """Answer radio/checkbox groups using the question text, not global Yes/No."""
    dialog = page.locator("div[role='dialog'], .jobs-easy-apply-modal")
    root = scope or (dialog.last if dialog.count() > 0 else page)
    filled = 0

    for selector in ("fieldset", "[role='radiogroup']", ".jobs-easy-apply-form-section__group"):
        groups = root.locator(selector)
        try:
            count = min(groups.count(), 25)
        except Exception:
            continue
        for i in range(count):
            group = groups.nth(i)
            try:
                if not group.is_visible():
                    continue
                hint = _control_hint(group)
                answer = _screening_answer_for_hint(hint, profile)
                if answer and _click_answer_in_container(group, answer):
                    filled += 1
            except Exception:
                continue

    return filled


def fill_required_comboboxes(page: Page, profile: dict, scope: Locator | None = None) -> int:
    """Fill empty LinkedIn comboboxes (month/year/custom selects)."""
    dialog = page.locator("div[role='dialog'], .jobs-easy-apply-modal")
    root = scope or (dialog.last if dialog.count() > 0 else page)
    exp = _primary_experience(profile)
    start_month, start_year, _ = _parse_resume_date(str(exp.get("start_date") or ""))
    end_month, end_year, current = _parse_resume_date(str(exp.get("end_date") or ""))
    if not start_year:
        start_year = _fallback_year(profile)

    filled = 0
    boxes = root.locator('[role="combobox"]:visible, select:visible')
    try:
        count = min(boxes.count(), 20)
    except Exception:
        return 0

    for i in range(count):
        box = boxes.nth(i)
        try:
            if not box.is_visible():
                continue
            tag = (box.evaluate("el => el.tagName") or "").lower()
            if tag == "select":
                val = box.evaluate(
                    "el => el.selectedIndex >= 0 ? (el.options[el.selectedIndex].label || '').trim() : ''"
                )
                if val and not re.search(r"select|choose|month|year", val, re.I):
                    continue
            else:
                val = (box.inner_text() or "").strip()
                if val and not re.search(r"select|choose|month|year", val, re.I):
                    continue
            hint = _control_hint(box)
            pick = _select_repair_value(hint, profile, _profile_years(profile)) or resume_value_for_hint(
                hint, profile
            )
            if not pick:
                continue
            if tag == "select":
                if _select_native_option(box, pick):
                    filled += 1
            elif _choose_dropdown(box, pick):
                filled += 1
        except Exception:
            continue

    # Explicit experience date labels (LinkedIn Easy Apply editor)
    pairs = (
        (r"from\s+month", start_month),
        (r"from\s+year", start_year),
    )
    if not current:
        pairs = (
            *pairs,
            (r"to\s+month|end\s+month", end_month),
            (r"to\s+year|end\s+year", end_year),
        )
    for pattern, value in pairs:
        if not value:
            continue
        try:
            loc = root.get_by_label(re.compile(pattern, re.I))
            if loc.count() > 0 and _choose_dropdown(loc.first, value):
                filled += 1
        except Exception:
            pass

    if filled:
        logger.info("Filled %d combobox/select fields", filled)
    return filled


def fill_custom_questions(page: Page, profile: dict) -> int:
    """Fill labeled questions when the answer exists on the parsed resume."""
    filled = 0
    dialog = page.locator("div[role='dialog'], .jobs-easy-apply-modal")
    root = dialog.last if dialog.count() > 0 else page

    try:
        for inp in root.locator(
            "input[required]:not([type='hidden']):not([type='radio']):not([type='checkbox']), "
            "textarea[required], select[required]"
        ).all()[:12]:
            if not inp.is_visible() or not _field_is_empty(inp):
                continue
            hint = _control_hint(inp)
            answer = resume_value_for_hint(hint, profile)
            if not answer:
                continue
            tag = (inp.evaluate("el => el.tagName") or "").lower()
            if tag == "select":
                if _select_answer(inp, answer) or _choose_dropdown(inp, answer):
                    filled += 1
            else:
                inp.fill(answer[:2000])
                filled += 1
    except Exception:
        pass

    return filled


def fill_greenhouse_fields(page: Page, profile: dict) -> int:
    """Greenhouse uses predictable field names on boards.greenhouse.io."""
    filled = fill_standard_fields(page, profile)
    name = profile.get("name", "")
    email = profile.get("email", "")
    phone = phone_for_apply(profile)
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
    file_path = str(path.resolve())

    # Current LinkedIn Easy Apply shows a button ("Upload resume") that opens
    # the system file picker. There is no usable file input until that click.
    try:
        dialog = page.locator("div[role='dialog'], .jobs-easy-apply-modal").last
        root = dialog if dialog.count() > 0 else page
        button = root.get_by_role("button", name=re.compile(r"upload resume", re.I))
        if button.count() > 0 and button.first.is_visible():
            with page.expect_file_chooser(timeout=8000) as chooser_info:
                button.first.click(timeout=4000)
            chooser_info.value.set_files(file_path)
            page.wait_for_timeout(1200)
            logger.info("Uploaded resume via Upload resume button: %s", path.name)
            return True
    except Exception as exc:
        logger.warning("Upload resume button failed: %s", exc)

    try:
        radio = page.get_by_role("radio", name=re.compile(r"upload resume", re.I))
        if radio.count() > 0:
            radio.first.check(timeout=2000)
            page.wait_for_timeout(300)
    except Exception:
        pass

    scopes: list = []
    dialog = page.locator("div[role='dialog'], .jobs-easy-apply-modal")
    try:
        if dialog.count() > 0:
            scopes.append(dialog.last)
    except Exception:
        pass
    scopes.append(page)

    selectors = (
        "input[type='file'][id*='resume' i]",
        "input[type='file'][name*='resume' i]",
        "input[type='file'][id*='cv' i]",
        "input[type='file'][name*='cv' i]",
        "input[type='file'][accept*='pdf' i]",
        "input.jobs-document-upload__file-input",
        "input[type='file']",
    )
    for scope in scopes:
        for selector in selectors:
            try:
                loc = scope.locator(selector)
                count = min(loc.count(), 4)
            except Exception:
                continue
            for index in range(count):
                try:
                    loc.nth(index).set_input_files(file_path)
                    logger.info("Uploaded resume %s via %s", path.name, selector)
                    return True
                except Exception as exc:
                    logger.debug("Upload failed for %s[%s]: %s", selector, index, exc)

    logger.warning("No resume file input found for %s", path.name)
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
