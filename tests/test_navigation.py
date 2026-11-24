import pytest
from starlette.requests import Request

from pacemaker_registry.main import templates, asset_url


@pytest.mark.parametrize('path,active', [
    ('/', '/'), ('/results/67', '/'),
    ('/events', '/events'), ('/events/1', '/events'),
    ('/about', '/about'), ('/add', '/add'), ('/results', '/add'),
])
def test_shared_navigation_has_logo_and_one_active_section(path, active):
    request = Request({'type': 'http', 'path': path, 'headers': [], 'scheme': 'https',
                       'server': ('test', 443), 'query_string': b''})
    html = templates.get_template('_navigation.html').render(request=request)
    assert html.count('aria-current="page"') == 1
    assert f'href="{active}" aria-current="page"' in html or (
        active == '/add' and 'href="/add" aria-label="Добавить результат" aria-current="page"' in html)
    assert f'class="brand-mark" src="{asset_url("apple-touch-icon.png")}"' in html
    assert '>P</span>' not in html
    assert 'aria-label="Главное меню"' in html
    assert '>Пейсмейкеры</a>' in html
    assert '>Соревнования</a>' in html


def test_about_page_has_formula_anchor_and_worked_examples():
    from fastapi.testclient import TestClient

    from pacemaker_registry.main import app
    from pacemaker_registry.rating import RANKING_FORMULAS

    response = TestClient(app).get('/about')
    assert response.status_code == 200
    assert 'id="rating-formulas"' in response.text
    assert 'href="/about" aria-current="page"' in response.text
    for key, (label, _) in RANKING_FORMULAS.items():
        assert f'<h3>{label}</h3>' in response.text
        assert f'href="/?formula={key}"' in response.text
    assert '9,909' in response.text
    assert '7,412' in response.text
