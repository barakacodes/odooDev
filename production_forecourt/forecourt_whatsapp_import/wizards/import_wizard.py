# -*- coding: utf-8 -*-
import re
import io
import zipfile
import base64
from datetime import datetime
from odoo import models, fields, api
from odoo.exceptions import UserError


# WhatsApp invisible character cleaner
_WA_CHARS = ['\u200e', '\u200f', '\u200b', '\ufeff', '\u202a', '\u202c', '\u202d', '\u202e']


def _strip_wa(text):
    """Remove WhatsApp/Unicode invisible formatting characters."""
    if not text:
        return ''
    for c in _WA_CHARS:
        text = text.replace(c, '')
    return text


def _parse_number(raw):
    """Parse a numeric token tolerant of the typos seen in real reports.

    Handles:
      - '22,890' / '22890' / '22890.12'        (standard)
      - '3,155 .2'  → 3155.2                    (space before decimal)
      - '3,155 20'  → 3155.20                   (space instead of decimal)
    """
    if not raw:
        return 0.0
    raw = _strip_wa(str(raw)).strip()
    raw = re.sub(r'^[Kk]\s*', '', raw)
    m = re.match(r'([0-9,\.\s]+)', raw)
    if m:
        raw = m.group(1).strip()

    # Strategy 1 (preserves legacy behaviour): digit-space-digit → decimal
    # Example: '3155 20' → '3155.20'
    candidate = re.sub(r'(\d)\s+(\d)', r'\1.\2', raw)
    candidate = candidate.replace(',', '').rstrip('.')
    try:
        return float(candidate)
    except ValueError:
        pass

    # Strategy 2 (new): strip ALL whitespace
    # Example: '3155 .2' → '3155.2'
    candidate = re.sub(r'\s+', '', raw).replace(',', '').rstrip('.')
    try:
        return float(candidate)
    except ValueError:
        return 0.0


def _clean_num(s):
    """Parse a numeric value by stripping all non-numeric characters first.
    Handles spaces robustly by design."""
    if not s:
        return 0.0
    s = _strip_wa(str(s))
    s = re.sub(r'[^\d.,\-]', '', s.strip())
    s = s.replace(',', '').rstrip('.')
    if not s or s == '-':
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _robust_money_search(text, *keywords):
    """Bulletproof: find keyword anywhere, then grab FIRST K-number within 200 chars.
    Bypasses line-index issues entirely."""
    text = _strip_wa(text)
    for kw in keywords:
        pattern = re.compile(
            r'\b' + re.escape(kw) + r'\b[\s\S]{0,200}?[Kk]\s*([\d,]+\.?\d*)',
            re.IGNORECASE
        )
        m = pattern.search(text)
        if m:
            val = _parse_number(m.group(1))
            if val > 0:
                return val
    return 0.0


def get_k_value(line):
    line = _strip_wa(line)
    # Pattern 1: = K 22,890
    m = re.search(r'=\s*[Kk]\s*([0-9][0-9,.]*)', line)
    if m and m.group(1):
        return _parse_number(m.group(1))
    # Pattern 2: k22,890 (line starts with K)
    m = re.match(r'^\s*[Kk]\s*([0-9][0-9,.]*)', line.strip())
    if m:
        return _parse_number(m.group(1))
    # Pattern 3: Any K followed by number
    m = re.search(r'\b[Kk]\s*([0-9][0-9,.]*)', line)
    if m:
        return _parse_number(m.group(1))
    return 0.0


def _extract_field(body, *label_patterns):
    body = _strip_wa(body)
    for pat in label_patterns:
        m = re.search(pat + r'[^\n]*', body, re.IGNORECASE)
        if m:
            val = _clean_num(m.group(0))
            if val:
                return val
    return 0.0


def _extract_atg_received(text):
    """Parse the 'Received by atg' block from a KCM/Arcades style report."""
    text = _strip_wa(text)
    m = re.search(
        r'received\s+by\s+atg([\s\S]{0,250}?)(?=received\s+by|closing|loss|$|\*[a-z])',
        text, re.IGNORECASE,
    )
    if not m:
        return 0.0, 0.0
    block = m.group(1)

    ulp_m = re.search(r'ulp\s*=\s*([\d,]+\.?\d*)', block, re.IGNORECASE)
    lsd_m = re.search(r'lsd\s*=\s*([\d,]+\.?\d*)', block, re.IGNORECASE)
    if ulp_m or lsd_m:
        ulp = _parse_number(ulp_m.group(1)) if ulp_m else 0.0
        lsd = _parse_number(lsd_m.group(1)) if lsd_m else 0.0
        return ulp, lsd

    num_m = re.search(r'([\d,]+\.?\d*)', block)
    if num_m:
        return _parse_number(num_m.group(1)), 0.0
    return 0.0, 0.0


# === ROBUST CHILILABOMBWE TANK SUMMATION ===
def _sum_tank_volumes(text, section_label, fuel_label):
    text = _strip_wa(text)
    match = re.search(rf'{section_label}.*?(?=OPENING|TOTAL|CREDIT|CASH|RECEIVED|CLOSING|\Z)', text, re.IGNORECASE | re.DOTALL)
    if not match:
        return 0.0
    section_text = match.group(0)
    opening_volumes = re.findall(rf'TANK \d+ \((?:{fuel_label})\):\s*([\d,.]+)LT', section_text, re.IGNORECASE)
    closing_volumes = re.findall(rf'TANK \d+ \((?:{fuel_label})\):[\d.]+\s*=\s*([\d,.]+)LT', section_text, re.IGNORECASE)
    total = sum(_clean_num(v) for v in opening_volumes + closing_volumes)
    return total


LUANSHYA_SECTION_HEADERS = [
    'TOTAL SALES', 'CREDIT SALE', 'TEST TRANSFER', 'CASH SALES',
    'TOTAL FUEL CASH', 'MOMO', 'FUEL CASH @ HAND', 'LUBES CASH',
    'LPG CASH', 'COOKER TOP', 'CASH AT HAND', 'RECEIVED STOCK',
    'OPENING STOCKS', 'CLOSING STOCKS',
]


def _section(body, label, window=300, headers=None):
    headers = headers or LUANSHYA_SECTION_HEADERS
    clean_label = label.strip('*').strip()
    other_headers = [h for h in headers if h.upper() != clean_label.upper()]
    boundary = '|'.join(re.escape(h) for h in other_headers)
    pattern = (
        r'\*?' + re.escape(clean_label) + r'\*?'
        + r'([\s\S]{0,%d}?)(?=\*?(?:%s)\*?|\Z)' % (window, boundary)
    )
    m = re.search(pattern, body, re.IGNORECASE)
    return m.group(1) if m else ''


def _parse_report(text):
    text = _strip_wa(text)
    lines = [l.strip() for l in text.strip().splitlines()]
    lines = [l for l in lines if l]
    result = {}

    SECTION_HEADERS = [
        'opening stock', 'total sales', 'credit sale', 'test transfer',
        'cash sales', 'total fuel cash', 'accessories', 'received stock',
        'closing stock', 'pos', 'fuel cash'
    ]

    def is_section_header(line):
        l = _strip_wa(line).lower().strip().lstrip('*').strip()
        return any(l.startswith(h) for h in SECTION_HEADERS)

    def find_line(pattern, start=0):
        for i in range(start, len(lines)):
            if re.search(pattern, lines[i], re.IGNORECASE):
                return i
        return -1

    def get_ulp_lsd(section_idx):
        """Read the ULP= / LSD= pair following a section header.

        Handles LSD variants: LSD, LSG, LSGO, Lsg, Lsgo, Lsd.
        Also tolerates a supervisor writing 'I Have' as an
        alternative phrasing under '*received stock*'.
        """
        ulp = lsd = 0.0
        found_ulp = found_lsd = False
        for i in range(section_idx + 1, min(section_idx + 10, len(lines))):
            line = lines[i]
            if is_section_header(line):
                break
            # ULP variants
            m = re.match(r'ulp\s*=\s*(.*)', line, re.IGNORECASE)
            if m:
                ulp = _parse_number(m.group(1))
                found_ulp = True
            # LSD variants — cover 'lsd' and 'lsg' with optional trailing 'o'
            m = re.match(r'ls[dg]o?\s*=\s*(.*)', line, re.IGNORECASE)
            if m:
                lsd = _parse_number(m.group(1))
                found_lsd = True
            # 'I Have' phrasing that appears in some reports
            m = re.match(r'^\s*i\s+have\s*$', line, re.IGNORECASE)
            if m:
                # Next non-empty line usually holds the value with an LSD label
                for j in range(i + 1, min(i + 3, len(lines))):
                    cand = lines[j]
                    m2 = re.match(r'ls[dg]o?\s*=\s*(.*)', cand, re.IGNORECASE)
                    if m2:
                        lsd = _parse_number(m2.group(1))
                        found_lsd = True
                        break
            if found_ulp and found_lsd:
                break
        return ulp, lsd

    # === SUPERVISOR ===
    _DATE_ANY = r'\d{1,2}/\d{1,2}/\d{2,4}'
    for i, line in enumerate(lines[:5]):
        if re.search(r'shift', line, re.IGNORECASE) and not re.search(_DATE_ANY, line):
            m = re.match(r'(.+?)[\'\']?s?\s+shift', line, re.IGNORECASE)
            if m:
                result['supervisor_name'] = m.group(1).strip()
                break
    if 'supervisor_name' not in result:
        result['supervisor_name'] = 'Unknown'

    # === DATE ===
    date_line_idx = find_line(r'\d{1,2}/\d{1,2}/\d{2,4}')
    if date_line_idx < 0:
        raise UserError("Invalid format: Cannot find date line (expected DD/MM/YYYY or DD/MM/YY).")
    date_line = lines[date_line_idx]
    date_match = re.search(r'(\d{1,2})/(\d{1,2})/(\d{2,4})', date_line)
    if not date_match:
        raise UserError(f"Invalid format: Date not found in line: {date_line}")
    try:
        day = int(date_match.group(1))
        month = int(date_match.group(2))
        year_str = date_match.group(3)
        year = int(year_str)
        if len(year_str) == 2:
            year = 2000 + year if year < 70 else 1900 + year
        result['date'] = datetime(year, month, day).date()
    except ValueError:
        raise UserError(f"Invalid date: {date_match.group(0)}")

    # === SHIFT TYPE ===
    if re.search(r'\bnight\b', date_line, re.IGNORECASE):
        result['shift_type'] = 'night'
    elif re.search(r'\bday\b', date_line, re.IGNORECASE):
        result['shift_type'] = 'day'
    else:
        shift_type_found = False
        for i in range(date_line_idx + 1, min(date_line_idx + 4, len(lines))):
            candidate = lines[i]
            if re.search(r'\bnight\s+shift\b', candidate, re.IGNORECASE):
                result['shift_type'] = 'night'
                shift_type_found = True
                break
            if re.search(r'\bday\s+shift\b', candidate, re.IGNORECASE):
                result['shift_type'] = 'day'
                shift_type_found = True
                break

        if not shift_type_found:
            result['shift_type'] = 'day'
            result['shift_type_defaulted'] = True

    # === OPENING STOCK ===
    opening_idx = find_line(r'opening\s+stock')
    if opening_idx < 0:
        raise UserError("Invalid format: Missing 'opening stock' section.")
    result['opening_ulp'], result['opening_lsd'] = get_ulp_lsd(opening_idx)

    # === TOTAL SALES ===
    total_sales_idx = find_line(r'total\s+sales')
    if total_sales_idx < 0:
        raise UserError("Invalid format: Missing 'total sales' section.")
    result['total_sales_ulp'], result['total_sales_lsd'] = get_ulp_lsd(total_sales_idx)

    # === CREDIT SALES ===
    credit_idx = find_line(r'credit\s+sale')
    if credit_idx < 0:
        raise UserError("Invalid format: Missing 'credit sale' section.")
    result['credit_ulp'], result['credit_lsd'] = get_ulp_lsd(credit_idx)

    # === COUPON SALES (optional, present on some shifts) ===
    # Coupons/vouchers count toward Total Sales but NOT toward Cash Sales.
    # Captured separately so the report-consistency check
    # (Total = Credit + Cash + Coupon) stays accurate.
    coupon_idx = find_line(r'coupon\s+sale')
    if coupon_idx >= 0:
        result['coupon_ulp'], result['coupon_lsd'] = get_ulp_lsd(coupon_idx)
    else:
        result['coupon_ulp'] = result['coupon_lsd'] = 0.0

    # === TEST TRANSFER ===
    test_idx = find_line(r'test\s+transfer')
    if test_idx >= 0:
        result['test_ulp'], result['test_lsd'] = get_ulp_lsd(test_idx)
    else:
        result['test_ulp'] = result['test_lsd'] = 0.0

    # === CASH SALES ===
    cash_sales_idx = find_line(r'cash\s+sales')
    if cash_sales_idx < 0:
        raise UserError("Invalid format: Missing 'cash sales' section.")
    result['cash_ulp'], result['cash_lsd'] = get_ulp_lsd(cash_sales_idx)

    # === FUEL CASH - ROBUST SEARCH FIRST ===
    result['fuel_cash_k'] = _robust_money_search(text, 'total fuel cash', 'fuel cash')
    if not result['fuel_cash_k']:
        fuel_cash_idx = find_line(r'total\s+fuel\s+cash')
        if fuel_cash_idx >= 0:
            result['fuel_cash_k'] = get_k_value(lines[fuel_cash_idx])
            if not result['fuel_cash_k'] and fuel_cash_idx + 1 < len(lines):
                result['fuel_cash_k'] = get_k_value(lines[fuel_cash_idx + 1])
    if not result['fuel_cash_k']:
        m = re.search(r'total\s+fuel\s+cash[\s\S]{0,50}?[Kk]\s*([\d,]+\.?\d*)', text, re.IGNORECASE)
        if m:
            result['fuel_cash_k'] = _parse_number(m.group(1))

    # === POS - ROBUST SEARCH FIRST ===
    result['pos_k'] = _robust_money_search(text, 'pos')
    if not result['pos_k']:
        pos_idx = find_line(r'^\*?\s*pos\s*\*?\s*$')
        if pos_idx >= 0 and pos_idx + 1 < len(lines):
            result['pos_k'] = get_k_value(lines[pos_idx + 1])
        else:
            pos_idx2 = find_line(r'^\*?\s*pos\s*\*?\s*[Kk]\s*=')
            result['pos_k'] = get_k_value(lines[pos_idx2]) if pos_idx2 >= 0 else 0.0

    # === CASH @ HAND - ROBUST SEARCH FIRST ===
    result['cash_at_hand_k'] = _robust_money_search(text, 'fuel cash @ hand', 'cash @ hand', 'cash at hand')
    if not result['cash_at_hand_k']:
        hand_idx = find_line(r'fuel\s+cash\s+@\s+hand')
        if hand_idx >= 0:
            result['cash_at_hand_k'] = get_k_value(lines[hand_idx])
            if not result['cash_at_hand_k'] and hand_idx + 1 < len(lines):
                result['cash_at_hand_k'] = get_k_value(lines[hand_idx + 1])
    if not result['cash_at_hand_k']:
        result['cash_at_hand_k'] = result['fuel_cash_k']

    # === ACCESSORIES (Lubes / LPG) ===
    acc_idx = find_line(r'accessories')
    result['lubes_k'] = result['lpg_k'] = 0.0
    if acc_idx >= 0:
        for i in range(acc_idx + 1, min(acc_idx + 4, len(lines))):
            line = lines[i]
            if re.search(r'lube', line, re.IGNORECASE):
                m = re.search(r'=\s*[Kk]?\s*([0-9][0-9,.]*)', line)
                result['lubes_k'] = _parse_number(m.group(1)) if m and m.group(1) else 0.0
            if re.search(r'lpg', line, re.IGNORECASE):
                m = re.search(r'=\s*[Kk]?\s*([0-9][0-9,.]*)', line)
                result['lpg_k'] = _parse_number(m.group(1)) if m and m.group(1) else 0.0

    # === RECEIVED STOCK ===
    recv_idx = find_line(r'received\s+stock')
    if recv_idx >= 0:
        result['recv_ulp'], result['recv_lsd'] = get_ulp_lsd(recv_idx)
    else:
        result['recv_ulp'] = result['recv_lsd'] = 0.0

    # === RECEIVED BY ATG — override when present ===
    atg_received_ulp, atg_received_lsd = _extract_atg_received(text)
    if atg_received_ulp > 0:
        result['recv_ulp'] = atg_received_ulp
    if atg_received_lsd > 0:
        result['recv_lsd'] = atg_received_lsd

    # === CLOSING STOCK ===
    close_idx = find_line(r'closing\s+stock')
    if close_idx < 0:
        raise UserError("Invalid format: Missing 'closing stocks' section.")
    result['closing_ulp'], result['closing_lsd'] = get_ulp_lsd(close_idx)

    # === ATG (Future proof) ===
    atg_close = re.search(r'atg\s*=\s*([\d,.]+)', text, re.IGNORECASE)
    result['atg_closing_stock_litres'] = _parse_number(atg_close.group(1)) if atg_close else 0.0

    # === LOSS ===
    loss_idx = find_line(r'loss\s*=')
    result['loss'] = 0.0
    if loss_idx >= 0:
        m = re.search(r'loss\s*=\s*([+-]?[0-9,.]+)', lines[loss_idx], re.IGNORECASE)
        if m:
            result['loss'] = _parse_number(m.group(1))

    return result


def _parse_luanshya_report(text):
    body = _strip_wa(text)
    if not re.search(r'PUMA\s+LUANSHYA', body, re.IGNORECASE):
        raise UserError("Invalid format: Missing 'PUMA LUANSHYA' header")

    result = {'supervisor_name': 'Unknown'}

    m = re.search(r'Date\s*:\s*(\d{1,2})-(\d{1,2})-(\d{4})', body)
    if not m:
        raise UserError("Invalid format: Cannot find 'Date:DD-MM-YYYY' line.")
    try:
        result['date'] = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date()
    except ValueError:
        raise UserError(f"Invalid date: {m.group(0)}")

    m = re.search(r'Shift\s*:\s*([AB])', body, re.IGNORECASE)
    if not m:
        raise UserError("Invalid format: Cannot find 'Shift: A' or 'Shift: B'.")
    result['shift_type'] = 'day' if m.group(1).upper() == 'A' else 'night'
    result['luanshya_shift_label'] = m.group(1).upper()

    result['momo_ulp'] = _extract_field(_section(body, 'MOMO'), r'Ul?[Pp]')
    result['momo_lsd'] = _extract_field(_section(body, 'MOMO'), r'LS[DG]')
    result['pos_k'] = result['momo_ulp'] + result['momo_lsd']

    result['total_sales_ulp'] = _extract_field(_section(body, 'TOTAL SALES'), r'ULP')
    result['total_sales_lsd'] = _extract_field(_section(body, 'TOTAL SALES'), r'LSD')
    result['credit_ulp'] = _extract_field(_section(body, 'CREDIT SALE'), r'ULP')
    result['credit_lsd'] = _extract_field(_section(body, 'CREDIT SALE'), r'LSD')
    result['test_ulp'] = _extract_field(_section(body, 'TEST TRANSFER'), r'ULP')
    result['test_lsd'] = _extract_field(_section(body, 'TEST TRANSFER'), r'LSD')
    result['cash_ulp'] = _extract_field(_section(body, 'CASH SALES'), r'ULP')
    result['cash_lsd'] = _extract_field(_section(body, 'CASH SALES'), r'LSD')
    result['coupon_ulp'] = _extract_field(_section(body, 'COUPON SALE'), r'ULP')
    result['coupon_lsd'] = _extract_field(_section(body, 'COUPON SALE'), r'LSD')
    result['fuel_cash_k'] = _extract_field(_section(body, 'TOTAL FUEL CASH', 30), r'K')
    result['cash_at_hand_k'] = _extract_field(_section(body, 'FUEL CASH @ HAND', 30), r'K')
    result['lubes_k'] = _extract_field(_section(body, 'LUBES CASH', 20), r'[kK]')
    result['lpg_k'] = _extract_field(_section(body, 'LPG CASH', 20), r'K')
    result['recv_ulp'] = _extract_field(_section(body, 'RECEIVED STOCK'), r'ULP')
    result['recv_lsd'] = _extract_field(_section(body, 'RECEIVED STOCK'), r'LSD')
    result['opening_ulp'] = _extract_field(_section(body, 'OPENING STOCKS'), r'ULP')
    result['opening_lsd'] = _extract_field(_section(body, 'OPENING STOCKS'), r'LSD')
    result['closing_ulp'] = _extract_field(_section(body, 'CLOSING STOCKS'), r'ULP')
    result['closing_lsd'] = _extract_field(_section(body, 'CLOSING STOCKS'), r'LSD')
    result['loss'] = 0.0
    if not result['cash_at_hand_k']:
        result['cash_at_hand_k'] = result['fuel_cash_k']
    return result


CHILI_SECTION_HEADERS = [
    'OPENING STOCK', 'TOTAL SALES', 'CREDIT SALES', 'CASH SALES',
    'TOTAL FUEL CASH', 'FUEL CASH @ HAND', 'SWIPES CASH',
    'TEST TRANSFER (RTT)', 'LUBES CASH', 'LPG CASH',
    'RECEIVED STOCK', 'CLOSING STOCK',
]


def _parse_chililabombwe_report(text):
    body = _strip_wa(text)
    if not re.search(r'CHILILABOMBWE', body, re.IGNORECASE):
        raise UserError("Invalid format: Missing 'CHILILABOMBWE' header")

    result = {'supervisor_name': 'Unknown'}

    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', body)
    if not m:
        raise UserError("Invalid format: Cannot find date")
    try:
        result['date'] = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date()
    except ValueError:
        raise UserError(f"Invalid date: {m.group(0)}")

    result['shift_type'] = 'day'

    def sect(label):
        return _section(body, label, headers=CHILI_SECTION_HEADERS)

    result['opening_ulp'] = _sum_tank_volumes(body, 'OPENING STOCK', 'ULP')
    result['opening_lsd'] = _sum_tank_volumes(body, 'OPENING STOCK', 'LSG')
    result['closing_ulp'] = _sum_tank_volumes(body, 'CLOSING STOCK', 'ULP')
    result['closing_lsd'] = _sum_tank_volumes(body, 'CLOSING STOCK', 'LSG')

    result['total_sales_ulp'] = _extract_field(sect('TOTAL SALES'), r'ULP')
    result['total_sales_lsd'] = _extract_field(sect('TOTAL SALES'), r'LSG')
    result['credit_ulp'] = _extract_field(sect('CREDIT SALES'), r'ULP')
    result['credit_lsd'] = _extract_field(sect('CREDIT SALES'), r'LSG')
    result['cash_ulp'] = _extract_field(sect('CASH SALES'), r'ULP')
    result['cash_lsd'] = _extract_field(sect('CASH SALES'), r'LSG')
    result['coupon_ulp'] = _extract_field(sect('COUPON SALES'), r'ULP')
    result['coupon_lsd'] = _extract_field(sect('COUPON SALES'), r'LSG')
    result['test_ulp'] = _extract_field(sect('TEST TRANSFER (RTT)'), r'ULP')
    result['test_lsd'] = _extract_field(sect('TEST TRANSFER (RTT)'), r'LSG')
    result['recv_ulp'] = _extract_field(sect('RECEIVED STOCK'), r'ULP')
    result['recv_lsd'] = _extract_field(sect('RECEIVED STOCK'), r'LSG')

    result['fuel_cash_k'] = _extract_field(sect('TOTAL FUEL CASH'), r'K')
    result['cash_at_hand_k'] = _extract_field(sect('FUEL CASH @ HAND'), r'K')
    result['pos_k'] = _extract_field(sect('SWIPES CASH'), r'K')
    result['lubes_k'] = _extract_field(sect('LUBES CASH'), r'K')
    result['lpg_k'] = _extract_field(sect('LPG CASH'), r'K')

    genset_match = re.search(r'DIESEL \(GENSET\)\s*LSG:\s*([\d,.]+)LT', body, re.IGNORECASE)
    result['genset_lsd'] = _clean_num(genset_match.group(1)) if genset_match else 0.0

    result['loss'] = 0.0
    if not result['cash_at_hand_k']:
        result['cash_at_hand_k'] = result['fuel_cash_k']
    return result


def _parse_report_for_branch(branch, text):
    if branch == 'luanshya':
        return _parse_luanshya_report(text)
    if branch == 'chililabombwe':
        return _parse_chililabombwe_report(text)
    return _parse_report(text)


_MSG_START = re.compile(
    r'^\u200e?\[(\d{1,2}/\d{1,2}/\d{4}), (\d{1,2}:\d{2}(?::\d{2})?\s?[AP]M)\] ([^:]+): ?(.*)$'
)


def _split_whatsapp_messages(raw_text):
    raw_text = _strip_wa(raw_text)
    lines = raw_text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    messages = []
    current = None
    for line in lines:
        m = _MSG_START.match(line)
        if m:
            if current:
                messages.append(current)
            date_str, time_str, sender, body = m.groups()
            current = {'date': date_str, 'time': time_str, 'sender': sender.strip(), 'body': body}
        else:
            if current is not None:
                current['body'] += '\n' + line
    if current:
        messages.append(current)
    return messages


def _looks_like_shift_report(branch, body):
    if branch == 'luanshya':
        return bool(re.search(r'PUMA\s+LUANSHYA', body, re.IGNORECASE))
    return bool(
        re.search(r'SHIFT', body, re.IGNORECASE)
        and re.search(r'opening stock', body, re.IGNORECASE)
        and re.search(r'kwacha copper mines', body, re.IGNORECASE)
    )


def _extract_file_text(filename, filedata):
    if filename.lower().endswith('.zip'):
        with zipfile.ZipFile(io.BytesIO(filedata)) as zf:
            txt_names = [n for n in zf.namelist() if n.lower().endswith('.txt')]
            if not txt_names:
                raise UserError('No .txt file found inside the uploaded zip.')
            with zf.open(txt_names[0]) as f:
                return f.read().decode('utf-8', errors='replace')
    return filedata.decode('utf-8', errors='replace')


def _get_branch_company(env, branch):
    branch_label = dict([
        ('arcades', 'Arcades'), ('luanshya', 'Luanshya'), ('chililabombwe', 'Chililabombwe'),
    ]).get(branch, branch)
    warehouse = env['stock.warehouse'].sudo().search([
        ('is_forecourt_branch', '=', True),
        ('name', 'ilike', branch_label),
    ], limit=1)
    company = warehouse.company_id if warehouse else env.company
    return warehouse, company


def _create_shift_records(env, branch, data):
    from datetime import datetime, time, timedelta
    shift_date = data['date']
    supervisor = env['res.users'].search(
        [('name', 'ilike', data['supervisor_name'])], limit=1
    ) or env.user

    warehouse, company = _get_branch_company(env, branch)

    if data['shift_type'] == 'day':
        start_dt = datetime.combine(shift_date, time(6, 0))
        end_dt = datetime.combine(shift_date, time(18, 0))
    else:
        start_dt = datetime.combine(shift_date, time(18, 0))
        end_dt = datetime.combine(shift_date + timedelta(days=1), time(6, 0))

    shift_vals = {
        'branch': branch,
        'shift_type': data['shift_type'],
        'date_start': start_dt,
        'date_end': end_dt,
        'supervisor_id': supervisor.id,
        'company_id': company.id,
        'state': 'closed',
        'source': 'whatsapp',
        'cash_on_hand_kwacha': data['cash_at_hand_k'],
        'pos_account_kwacha': data['pos_k'],
        'lube_sales_kwacha': data['lubes_k'],
        'lpg_sales_kwacha': data['lpg_k'],
        'supervisor_name_text': data.get('supervisor_name') or '',
    }
    if warehouse:
        shift_vals['branch_id'] = warehouse.id

    shift = env['forecourt.shift'].create(shift_vals)

    Rate = env['forecourt.fuel.rate']
    p_rate, d_rate = Rate.sudo().get_rate_for(
        shift_date, company, branch=warehouse or None,
    )

    env['forecourt.shift.fuel.entry'].create({
        'shift_id': shift.id, 'fuel_type': 'petrol',
        'opening_stock_litres': data['opening_ulp'],
        'received_stock_litres': data['recv_ulp'],
        'total_sold_litres': data['total_sales_ulp'],
        'credit_litres_sold': data['credit_ulp'],
        'coupon_litres_sold': data.get('coupon_ulp', 0.0),
        'cash_sales_litres': data['cash_ulp'],
        'test_stock_litres': data['test_ulp'],
        'closing_stock_litres': data['closing_ulp'],
        'atg_closing_stock_litres': data.get('atg_closing_stock_litres', 0.0),
        'rate': p_rate, 'cash_sales_value': data['cash_ulp'] * p_rate,
    })
    env['forecourt.shift.fuel.entry'].create({
        'shift_id': shift.id, 'fuel_type': 'diesel',
        'opening_stock_litres': data['opening_lsd'],
        'received_stock_litres': data['recv_lsd'],
        'total_sold_litres': data['total_sales_lsd'],
        'credit_litres_sold': data['credit_lsd'],
        'coupon_litres_sold': data.get('coupon_lsd', 0.0),
        'cash_sales_litres': data['cash_lsd'],
        'test_stock_litres': data['test_lsd'] + data.get('genset_lsd', 0),
        'closing_stock_litres': data['closing_lsd'],
        'atg_closing_stock_litres': data.get('atg_closing_stock_litres', 0.0),
        'rate': d_rate, 'cash_sales_value': data['cash_lsd'] * d_rate,
    })
    return shift


class ForecourtImportWizard(models.TransientModel):
    _name = 'forecourt.import.wizard'
    _description = 'Import WhatsApp Shift Report'

    branch = fields.Selection([
        ('arcades', 'Arcades'),
        ('luanshya', 'Luanshya'),
        ('chililabombwe', 'Chililabombwe'),
    ], string='Branch', required=True)

    bulk_mode = fields.Boolean(string='Bulk Import (multiple reports)', default=False)
    report_text = fields.Text(string='Paste Report')
    report_file = fields.Binary(string='Upload WhatsApp Export (.txt or .zip)')
    report_filename = fields.Char(string='Filename')
    preview_lines = fields.Text(string='Preview / Validation', readonly=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('validated', 'Validated'),
        ('error', 'Error'),
    ], default='draft')
    parsed_data = fields.Text(string='Parsed Data (JSON)', readonly=True)
    show_original = fields.Boolean(string='Show Original', default=False)

    is_editing = fields.Boolean(string='Editing', default=False, copy=False)

    parsed_supervisor = fields.Char(string='Supervisor')
    parsed_date       = fields.Date(string='Date')
    parsed_shift_type = fields.Char(string='Shift Type')
    p_opening    = fields.Float(string='Opening Stock ULP (L)', digits=(16,3))
    p_received   = fields.Float(string='Received Stock ULP (L)', digits=(16,3))
    p_total_sold = fields.Float(string='Total Sold ULP (L)', digits=(16,3))
    p_credit     = fields.Float(string='Credit Sold ULP (L)', digits=(16,3))
    p_cash_sales = fields.Float(string='Cash Sales ULP (L)', digits=(16,3))
    p_closing    = fields.Float(string='Closing Stock ULP (L)', digits=(16,3))
    d_opening    = fields.Float(string='Opening Stock LSD (L)', digits=(16,3))
    d_received   = fields.Float(string='Received Stock LSD (L)', digits=(16,3))
    d_total_sold = fields.Float(string='Total Sold LSD (L)', digits=(16,3))
    d_credit     = fields.Float(string='Credit Sold LSD (L)', digits=(16,3))
    d_cash_sales = fields.Float(string='Cash Sales LSD (L)', digits=(16,3))
    d_closing    = fields.Float(string='Closing Stock LSD (L)', digits=(16,3))
    p_fuel_cash    = fields.Float(string='Fuel Cash (K)', digits=(16,2))
    p_pos          = fields.Float(string='POS / Card (K)', digits=(16,2))
    p_cash_at_hand = fields.Float(string='Cash @ Hand (K)', digits=(16,2))
    p_lubes        = fields.Float(string='Lube Sales (K)', digits=(16,2))
    p_lpg          = fields.Float(string='LPG Sales (K)', digits=(16,2))
    p_loss         = fields.Float(string='Loss / Gain (L)', digits=(16,2))

    def _get_effective_data(self):
        import json
        self.ensure_one()
        d = {}
        if self.parsed_data:
            try:
                d = json.loads(self.parsed_data)
            except Exception:
                d = {}
        test_ulp = float(d.get('test_ulp', 0) or 0)
        test_lsd = float(d.get('test_lsd', 0) or 0)
        coupon_ulp = float(d.get('coupon_ulp', 0) or 0)
        coupon_lsd = float(d.get('coupon_lsd', 0) or 0)
        return {
            'supervisor_name': self.parsed_supervisor or 'Unknown',
            'date': self.parsed_date,
            'shift_type': (self.parsed_shift_type or 'day').lower(),
            'opening_ulp': self.p_opening, 'recv_ulp': self.p_received,
            'total_sales_ulp': self.p_total_sold, 'credit_ulp': self.p_credit,
            'coupon_ulp': coupon_ulp,
            'cash_ulp': self.p_cash_sales, 'closing_ulp': self.p_closing,
            'opening_lsd': self.d_opening, 'recv_lsd': self.d_received,
            'total_sales_lsd': self.d_total_sold, 'credit_lsd': self.d_credit,
            'coupon_lsd': coupon_lsd,
            'cash_lsd': self.d_cash_sales, 'closing_lsd': self.d_closing,
            'test_ulp': test_ulp, 'test_lsd': test_lsd,
            'fuel_cash_k': self.p_fuel_cash, 'pos_k': self.p_pos,
            'cash_at_hand_k': self.p_cash_at_hand,
            'lubes_k': self.p_lubes, 'lpg_k': self.p_lpg,
            'loss': self.p_loss,
        }

    def _return_self(self):
        return {'type': 'ir.actions.act_window', 'res_model': 'forecourt.import.wizard',
                'res_id': self.id, 'view_mode': 'form', 'target': 'new'}

    def action_show_original(self):
        self.show_original = True
        return self._return_self()

    def action_hide_original(self):
        self.show_original = False
        return self._return_self()

    def action_edit_fields(self):
        self.is_editing = True
        return self._return_self()

    def action_lock_fields(self):
        self.is_editing = False
        return self._return_self()

    def action_revalidate(self):
        self.write({'state': 'draft', 'preview_lines': False, 'parsed_data': False, 'is_editing': False})
        return self._return_self()

    def action_validate(self):
        self.ensure_one()
        import json
        try:
            data = _parse_report_for_branch(self.branch, self.report_text)
            branch_label = dict(self._fields['branch'].selection).get(self.branch, self.branch)
            preview = (
                f"Supervisor: {data['supervisor_name']} | Branch: {branch_label} | "
                f"Date: {data['date']} | Shift: {data['shift_type'].upper()}"
            )
            if data.get('shift_type_defaulted'):
                preview += (
                    "\n\n⚠ Shift type not detected in report — defaulted to DAY. "
                    "If this is actually a NIGHT shift, click Edit and correct it "
                    "before clicking Import Shift."
                )
            self.write({
                'preview_lines': preview,
                'parsed_data': json.dumps({k: str(v) for k, v in data.items()}, default=str),
                'state': 'validated',
                'is_editing': False,
                'parsed_supervisor': data.get('supervisor_name', 'Unknown'),
                'parsed_date': data.get('date'),
                'parsed_shift_type': (data.get('shift_type') or 'day').upper(),
                'p_opening': float(data.get('opening_ulp', 0) or 0),
                'p_received': float(data.get('recv_ulp', 0) or 0),
                'p_total_sold': float(data.get('total_sales_ulp', 0) or 0),
                'p_credit': float(data.get('credit_ulp', 0) or 0),
                'p_cash_sales': float(data.get('cash_ulp', 0) or 0),
                'p_closing': float(data.get('closing_ulp', 0) or 0),
                'd_opening': float(data.get('opening_lsd', 0) or 0),
                'd_received': float(data.get('recv_lsd', 0) or 0),
                'd_total_sold': float(data.get('total_sales_lsd', 0) or 0),
                'd_credit': float(data.get('credit_lsd', 0) or 0),
                'd_cash_sales': float(data.get('cash_lsd', 0) or 0),
                'd_closing': float(data.get('closing_lsd', 0) or 0),
                'p_fuel_cash': float(data.get('fuel_cash_k', 0) or 0),
                'p_pos': float(data.get('pos_k', 0) or 0),
                'p_cash_at_hand': float(data.get('cash_at_hand_k', 0) or 0),
                'p_lubes': float(data.get('lubes_k', 0) or 0),
                'p_lpg': float(data.get('lpg_k', 0) or 0),
                'p_loss': float(data.get('loss', 0) or 0),
            })
        except UserError as e:
            sep = "=" * 48
            self.write({
                'preview_lines': f"VALIDATION FAILED\n{sep}\nERROR: {e.args[0]}",
                'state': 'error',
            })
        return self._return_self()

    def _check_duplicate_and_import(self, data):
        """Import a shift. Allow multiple shifts on the same date when the
        supervisor differs (e.g. two supervisors covering split night
        sub-shifts). Block only when the same supervisor submits the same
        shift twice — that's the true duplicate case."""
        shift_date = data['date']
        existing = self.env['forecourt.shift'].search([
            ('branch', '=', self.branch),
            ('shift_type', '=', data['shift_type']),
            ('date_start', '>=', str(shift_date) + ' 00:00:00'),
            ('date_start', '<=', str(shift_date) + ' 23:59:59'),
        ])

        if existing:
            new_sup = (data.get('supervisor_name') or '').strip().lower()
            same_supervisor = existing.filtered(
                lambda s: (s.supervisor_name_text or '').strip().lower() == new_sup
            )

            if same_supervisor:
                shift = same_supervisor[0]
                if shift.source == 'pos':
                    raise UserError(
                        f"A {data['shift_type']} shift for {self.branch} on "
                        f"{shift_date.strftime('%d/%m/%Y')} already exists ({shift.name}) "
                        f"from live POS sales. Import blocked -- this shift's sales "
                        f"were already captured at the till."
                    )
                raise UserError(
                    f"A {data['shift_type']} shift for {self.branch} on "
                    f"{shift_date.strftime('%d/%m/%Y')} already exists for "
                    f"supervisor {data.get('supervisor_name', 'Unknown')} ({shift.name}). "
                    f"Import blocked to prevent duplicate entry of the same report."
                )

        return _create_shift_records(self.env, self.branch, data)

    def action_import(self):
        self.ensure_one()
        if self.state != 'validated':
            raise UserError('Please validate the report first.')
        data = self._get_effective_data()
        shift = self._check_duplicate_and_import(data)
        return {
            'type': 'ir.actions.act_window',
            'name': f'Shift {shift.name} imported',
            'res_model': 'forecourt.shift',
            'res_id': shift.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_bulk_import(self):
        self.ensure_one()
        if not self.branch:
            raise UserError('Please select a branch.')
        import re as _re
        text = self.report_text.strip()
        blocks = _re.split(r'(?m)^(?=\S.*(?:zambia|petroleum|mines|limited|ltd))', text, flags=_re.IGNORECASE)
        if len(blocks) <= 1:
            blocks = _re.split(r'\n\s*\n\s*\n', text)
        if len(blocks) <= 1:
            blocks = _re.split(r'\n-{3,}\n', text)
        blocks = [b.strip() for b in blocks if b.strip()]
        results = []
        imported = skipped = errors = 0
        for i, block in enumerate(blocks, 1):
            try:
                data = _parse_report_for_branch(self.branch, block)
                shift = self._check_duplicate_and_import(data)
                results.append(f"IMPORTED: Report {i} ({data['supervisor_name']} | {data['date']} {data['shift_type'].upper()}) as {shift.name}")
                imported += 1
            except UserError as e:
                msg = e.args[0]
                if 'already exists' in msg:
                    results.append(f"SKIPPED: Report {i} — {msg}")
                    skipped += 1
                else:
                    results.append(f"FAILED: Report {i} — {msg}")
                    errors += 1
            except Exception as e:
                results.append(f"ERROR: Report {i} — {str(e)}")
                errors += 1
        summary = f"BULK IMPORT COMPLETE (paste mode)\nTotal: {len(blocks)} | Imported: {imported} | Skipped: {skipped} | Failed: {errors}\n\n"
        summary += "\n".join(results)
        self.write({'preview_lines': summary, 'state': 'validated' if errors == 0 else 'error'})
        return self._return_self()

    def action_bulk_import_file(self):
        self.ensure_one()
        if not self.branch:
            raise UserError('Please select a branch.')
        if not self.report_file:
            raise UserError('Please upload a WhatsApp export file (.txt or .zip).')
        if self.branch == 'chililabombwe':
            raise UserError(
                "Chililabombwe report format hasn't been configured yet — "
                "no sample report has been provided for this branch."
            )

        filedata = base64.b64decode(self.report_file)
        raw_text = _extract_file_text(self.report_filename or '', filedata)
        messages = _split_whatsapp_messages(raw_text)

        candidates = [m for m in messages if _looks_like_shift_report(self.branch, m['body'])]

        results = []
        imported = skipped = errors = 0
        for i, msg in enumerate(candidates, 1):
            try:
                data = _parse_report_for_branch(self.branch, msg['body'])
                shift = self._check_duplicate_and_import(data)
                results.append(
                    f"IMPORTED: [{msg['date']} {msg['time']}] {msg['sender']} — "
                    f"{data['date']} {data['shift_type'].upper()} as {shift.name}"
                )
                imported += 1
            except UserError as e:
                msg_txt = e.args[0]
                if 'already exists' in msg_txt:
                    results.append(f"SKIPPED: [{msg['date']} {msg['time']}] {msg['sender']} — {msg_txt}")
                    skipped += 1
                else:
                    results.append(f"FAILED: [{msg['date']} {msg['time']}] {msg['sender']} — {msg_txt}")
                    errors += 1
            except Exception as e:
                results.append(f"ERROR: [{msg['date']} {msg['time']}] {msg['sender']} — {str(e)}")
                errors += 1

        summary = (
            f"BULK IMPORT COMPLETE (file: {self.report_filename})\n"
            f"Total messages scanned: {len(messages)} | Report-like messages found: {len(candidates)}\n"
            f"Imported: {imported} | Skipped (duplicates): {skipped} | Failed: {errors}\n\n"
        )
        summary += "\n".join(results)
        self.write({'preview_lines': summary, 'state': 'validated' if errors == 0 else 'error'})
        return self._return_self()