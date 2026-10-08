import pytest
from starlette.requests import Request

from pacemaker_registry.main import templates


@pytest.mark.parametrize('path,active', [
    ('/', '/'), ('/results/67', '/'),
    ('/events', '/events'), ('/events/1', '/events'),
    ('/add', '/add'), ('/results', '/add'),
])
def test_shared_navigation_has_logo_and_one_active_section(path, active):
    request = Request({'type': 'http', 'path': path, 'headers': [], 'scheme': 'https',
                       'server': ('test', 443), 'query_string': b''})
    html = templates.get_template('_navigation.html').render(request=request)
    assert html.count('aria-current="page"') == 1
    assert f'href="{active}" aria-current="page"' in html or (
        active == '/add' and 'href="/add" aria-label="Добавить результат" aria-current="page"' in html)
    assert 'class="brand-mark" src="/static/apple-touch-icon.png?v=equal-flags"' in html
    assert '>P</span>' not in html
    assert 'aria-label="Главное меню"' in html
    assert '>Пейсмейкеры</a>' in html
    assert '>Соревнования</a>' in html
