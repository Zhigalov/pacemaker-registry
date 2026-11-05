"""Profile values and contact links; imported names are never edited here."""
import re
from datetime import date
from urllib.parse import parse_qs, urlsplit, urlunsplit


CONTACT_TYPES = {
    'telegram': ('Telegram', '@username или https://t.me/username'),
    'instagram': ('Instagram', '@username или instagram.com/username'),
    'whatsapp': ('WhatsApp', '+7 999 123-45-67 или wa.me/79991234567'),
    'max': ('MAX', 'Ник или ссылка на профиль MAX'),
    'phone': ('Телефон', '+7 999 123-45-67'),
    'vk': ('ВКонтакте', 'Ник, id123456 или ссылка на профиль'),
    'facebook': ('Facebook', 'Ник или ссылка на профиль'),
    'strava': ('Strava', 'ID, ник или ссылка на профиль'),
    'other': ('Другое', 'Ссылка или текст'),
}
ACHIEVEMENTS = {'pshp_graduate': {
    'title': 'Выпускник Первой школы пейсмейкеров',
    'image': '/static/first-pacers-school.jpg',
}}
CONTACT_PREFIXES = {
    'strava': 'https://www.strava.com/athletes/',
    'telegram': 'https://t.me/', 'instagram': 'https://instagram.com/',
    'max': 'https://max.ru/', 'vk': 'https://vk.com/', 'facebook': 'https://facebook.com/',
}
SERVICE_HOSTS = {
    'strava': ('strava.com', 'strava.app.link'),
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


def _service_link(kind: str, value: str):
    """Accept only this service's URLs, including links pasted without a scheme."""
    hosts = SERVICE_HOSTS[kind]
    if value.lower().startswith(tuple(host + '/' for host in hosts)
                                + tuple('www.' + host + '/' for host in hosts)):
        value = 'https://' + value
    if '://' not in value:
        return None
    parsed = urlsplit(_web_url(value))
    if parsed.hostname.removeprefix('www.').lower() not in hosts:
        raise ValueError(f'{CONTACT_TYPES[kind][0]}: вставьте ссылку на профиль в этой сети или укажите ник.')
    return parsed


def normalize_social(kind: str, value: str) -> str:
    parsed = _service_link(kind, value)
    nickname = value.removeprefix('@')
    if parsed:
        nickname = parsed.path.strip('/')
        if kind == 'facebook' and nickname == 'profile.php':
            ids = parse_qs(parsed.query).get('id', [])
            if len(ids) != 1 or not ids[0].isdigit():
                raise ValueError('Facebook: проверьте ссылку на профиль — в ней должен быть ID.')
            return 'https://www.facebook.com/profile.php?id=' + ids[0]
    pattern = {'telegram': r'[A-Za-z0-9_]{5,32}', 'instagram': r'[A-Za-z0-9_.]{1,30}',
               'max': r'(?:u/)?[A-Za-z0-9_][A-Za-z0-9_.-]{0,199}'}.get(
                   kind, r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}')
    if not re.fullmatch(pattern, nickname):
        raise ValueError(f'{CONTACT_TYPES[kind][0]}: укажите ник (можно с @) или ссылку на профиль, без пробелов.')
    return CONTACT_PREFIXES[kind] + nickname


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
        if kind in {'phone', 'whatsapp'}:
            if kind == 'whatsapp':
                parsed = _service_link(kind, value)
                if parsed:
                    path = parsed.path.strip('/')
                    if parsed.hostname == 'api.whatsapp.com' and path == 'send':
                        numbers = parse_qs(parsed.query).get('phone', [])
                        value = numbers[0] if len(numbers) == 1 else ''
                    elif parsed.hostname in {'wa.me', 'www.wa.me'}:
                        value = path
                    else:
                        value = ''
                    value = '+' + value.lstrip('+')
            number = re.sub(r'[\s()\-]', '', value)
            if not re.fullmatch(r'\+[1-9][0-9]{6,14}', number):
                raise ValueError('Укажите телефон с кодом страны, например +7 999 123-45-67.')
            value = number if kind == 'phone' else 'https://wa.me/' + number[1:]
        elif kind == 'strava':
            value = normalize_strava(value)
        elif kind == 'other':
            if value.startswith(('https://', 'http://')):
                value = _web_url(value)
            elif re.match(r'(?i)^[a-z][a-z0-9+.-]*:', value):
                raise ValueError('Для ссылки «Другое» используйте http:// или https://.')
        else:
            value = normalize_social(kind, value)
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
    display_kind = kind
    if kind == 'other' and href and not href.startswith('tel:'):
        host = urlsplit(href).hostname.removeprefix('www.').lower()
        display_kind = next((service for service, hosts in SERVICE_HOSTS.items() if host in hosts), 'other')
    display_value = contact_input(dict(contact, type=display_kind)) if href else value
    if display_kind == 'facebook' and href and urlsplit(href).path == '/profile.php':
        display_value = parse_qs(urlsplit(href).query).get('id', [display_value])[0]
    if display_kind == 'strava' and href and urlsplit(href).hostname == 'strava.app.link':
        display_value = 'Профиль Strava'
    if display_kind in CONTACT_PREFIXES and display_value != 'Профиль Strava' and not display_value.startswith(('http://', 'https://')):
        display_value = '@' + display_value if '/' not in display_value else display_value
    return {'label': contact.get('label') or CONTACT_TYPES.get(display_kind, ('Другое', ''))[0],
            'value': value, 'display_value': display_value, 'href': href,
            'icon': display_kind if display_kind in CONTACT_TYPES else 'other'}


def profile_form_values(profile) -> dict:
    contacts = list(profile.contacts)
    rows = []
    for kind, (label, placeholder) in CONTACT_TYPES.items():
        if kind == 'other':
            continue
        matches = [c for c in contacts if c['type'] == kind]
        rows.extend([dict(c, value=contact_input(c)) for c in matches] or [{'type': kind, 'value': ''}])
    rows.extend(c for c in contacts if c['type'] == 'other')
    return {'about': profile.about or '', 'login': profile.login or '',
            'city': profile.city or '', 'club': profile.club or '',
            'personal_records': profile.personal_records or {}, 'contacts': rows,
            'achievements': list(profile.achievements), 'profile_version': profile.version}


def contact_input(contact: dict) -> str:
    """Existing canonical URLs become editable handles; no data migration needed."""
    kind, value = contact['type'], contact['value']
    if kind not in SERVICE_HOSTS or not value.startswith(('https://', 'http://')):
        return value
    parsed = urlsplit(value)
    if not parsed.hostname or parsed.hostname.removeprefix('www.').lower() not in SERVICE_HOSTS[kind]:
        return value
    path = parsed.path.strip('/')
    if kind == 'strava' and parsed.hostname == 'strava.app.link':
        return value
    if kind == 'whatsapp':
        number = parse_qs(parsed.query).get('phone', [path])[0]
        return '+' + number.lstrip('+')
    if kind == 'facebook' and path == 'profile.php':
        return value
    return path.removeprefix('athletes/') if kind == 'strava' else path


RECORD_DISTANCES = ('5', '10', '21.1', '42.2')


def normalize_profile_login(value: str) -> str | None:
    login = value.strip().lower()
    if login and not re.fullmatch(r'[a-z][a-z0-9_-]{2,29}', login):
        raise ValueError('Логин: от 3 до 30 латинских букв, цифр, _ или -. Начните с буквы.')
    return login or None


def normalize_profile_details(values: dict) -> dict:
    clean = dict(values)
    if len(clean['about']) > 5000:
        raise ValueError('Описание «О себе» — до 5000 символов.')
    clean['login'] = normalize_profile_login(clean['login'])
    for field, title in (('city', 'Город'), ('club', 'Беговой клуб')):
        clean[field] = ' '.join(clean[field].split())
        if len(clean[field]) > 120:
            raise ValueError(f'{title}: не больше 120 символов.')
    records = {}
    for distance in RECORD_DISTANCES:
        record = values['personal_records'].get(distance, {})
        time, year, url = (str(record.get(k, '')).strip() for k in ('time', 'year', 'url'))
        if not any((time, year, url)):
            continue
        match = re.fullmatch(r'(?:(\d{1,2}):)?([0-5]?\d):([0-5]\d)', time)
        if not match or not any(int(v or 0) for v in match.groups()):
            raise ValueError(f'Рекорд {distance} км: укажите время ЧЧ:ММ:СС или ММ:СС.')
        if not year.isdigit() or not 1900 <= int(year) <= date.today().year:
            raise ValueError(f'Рекорд {distance} км: укажите год от 1900 до {date.today().year}.')
        if not url or len(url) > 1000:
            raise ValueError(f'Рекорд {distance} км: добавьте ссылку на соревнование или трек.')
        records[distance] = {'time': ':'.join(f'{int(v or 0):02d}' for v in match.groups()),
                             'year': int(year), 'url': _web_url(url)}
    clean['personal_records'] = records
    return clean


def normalize_strava(value: str) -> str:
    if re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}', value.removeprefix('@')):
        return CONTACT_PREFIXES['strava'] + value.removeprefix('@')
    if value.lower().startswith(('strava.com/', 'www.strava.com/', 'strava.app.link/')):
        value = 'https://' + value
    urls = re.findall(r'https?://[^\s<>]+', value)
    if len(urls) == 1:
        url = _web_url(urls[0].rstrip('.,!;)'))
        parsed = urlsplit(url)
        if parsed.scheme == 'https':
            if parsed.hostname == 'strava.app.link' and re.fullmatch(r'/[A-Za-z0-9]+/?', parsed.path):
                return url
            if parsed.hostname in {'strava.com', 'www.strava.com'} and re.fullmatch(
                    r'/athletes/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}/?', parsed.path):
                return CONTACT_PREFIXES['strava'] + parsed.path.strip('/').removeprefix('athletes/')
    raise ValueError('Strava: укажите ID, ник или ссылку из «Поделиться профилем» (strava.app.link или strava.com/athletes/…).')
