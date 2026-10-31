"""Profile values and contact links; imported names are never edited here."""
import re
from urllib.parse import urlsplit, urlunsplit


CONTACT_TYPES = {
    'telegram': ('Telegram', 'https://t.me/username'),
    'instagram': ('Instagram', 'https://instagram.com/username'),
    'whatsapp': ('WhatsApp', '+7 999 123-45-67'),
    'max': ('MAX', 'https://max.ru/…'),
    'phone': ('Телефон', '+7 999 123-45-67'),
    'vk': ('ВКонтакте', 'https://vk.com/username'),
    'facebook': ('Facebook', 'https://facebook.com/username'),
    'other': ('Другое', 'Ссылка или текст'),
}
ACHIEVEMENTS = {'pshp_graduate': 'Выпускник Первой школы пейсмейкеров'}
SERVICE_HOSTS = {
    'telegram': ('t.me', 'telegram.me'),
    'instagram': ('instagram.com',),
    'max': ('max.ru',),
    'vk': ('vk.com', 'vk.ru'),
    'facebook': ('facebook.com', 'fb.com'),
    'whatsapp': ('wa.me', 'api.whatsapp.com'),
}


def _web_url(value: str) -> str:
    if any(c.isspace() or ord(c) < 32 for c in value) or '\\' in value:
        raise ValueError('Проверьте ссылку: в ней есть пробелы или недопустимые символы.')
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError('Проверьте формат ссылки.') from None
    if (parsed.scheme not in {'https', 'http'} or not parsed.hostname
            or parsed.username or parsed.password or port not in {None, 80, 443}):
        raise ValueError('Укажите полную ссылку, начинающуюся с https:// или http://.')
    return urlunsplit(parsed)


def normalize_contacts(rows: list[dict]) -> list[dict[str, str]]:
    if len(rows) > 20:
        raise ValueError('Можно добавить не больше 20 контактов.')
    contacts = []
    for row in rows:
        kind, value = str(row.get('type', '')), str(row.get('value', '')).strip()
        label = str(row.get('label', '')).strip()
        if not value:
            continue
        if kind not in CONTACT_TYPES or len(value) > 500 or len(label) > 60:
            raise ValueError('Проверьте тип и длину контакта (до 500 символов, подпись — до 60).')
        if kind == 'phone' or (kind == 'whatsapp' and not value.startswith(('http://', 'https://'))):
            number = re.sub(r'[\s()\-]', '', value)
            if not re.fullmatch(r'\+[1-9][0-9]{6,14}', number):
                raise ValueError('Укажите телефон с кодом страны, например +7 999 123-45-67.')
            value = number if kind == 'phone' else 'https://wa.me/' + number[1:]
        elif kind == 'other':
            if value.startswith(('https://', 'http://')):
                value = _web_url(value)
            elif re.match(r'(?i)^[a-z][a-z0-9+.-]*:', value):
                raise ValueError('Для ссылки «Другое» используйте http:// или https://.')
        else:
            if kind == 'telegram' and re.fullmatch(r'@?[A-Za-z0-9_]{5,32}', value):
                value = 'https://t.me/' + value.lstrip('@')
            value = _web_url(value)
            hostname = urlsplit(value).hostname.removeprefix('www.').lower()
            if hostname not in SERVICE_HOSTS[kind]:
                raise ValueError(f'Укажите ссылку на {CONTACT_TYPES[kind][0]}.')
        contact = {'type': kind, 'value': value}
        if kind == 'other' and label:
            contact['label'] = label
        if contact not in contacts:
            contacts.append(contact)
    return contacts


def contact_view(contact: dict) -> dict:
    """Render only validated schemes, including for manually imported DB values."""
    kind, value = contact['type'], contact['value']
    href = None
    if kind == 'phone' and re.fullmatch(r'\+[1-9][0-9]{6,14}', value):
        href = 'tel:' + value
    elif value.startswith(('https://', 'http://')):
        try:
            href = _web_url(value)
        except ValueError:
            pass
    return {'label': contact.get('label') or CONTACT_TYPES.get(kind, ('Другое', ''))[0],
            'value': value, 'href': href}


def profile_form_values(profile) -> dict:
    contacts = list(profile.contacts)
    rows = []
    for kind, (label, placeholder) in CONTACT_TYPES.items():
        if kind == 'other':
            continue
        matches = [c for c in contacts if c['type'] == kind]
        rows.extend(matches or [{'type': kind, 'value': ''}])
    rows.extend(c for c in contacts if c['type'] == 'other')
    return {'motto': profile.motto or '', 'contacts': rows,
            'achievements': list(profile.achievements), 'profile_version': profile.version}
