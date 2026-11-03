import pytest
from pacemaker_registry.names import matches_name


@pytest.mark.parametrize('query,name,expected', [
    ('Иван', 'Петров Иван', True), ('ив', 'Иванов Сергей', True),
    ('ИВАН ПЕТРОВ', 'Петров Иван', True), ('Петров Иван', 'Петров Иван', True),
    ('  Иван\t\nПетров ', 'Петров Иван', True), ('петр петров', 'Петров Пётр', True),
    ('Пётр Петров', 'Петров Иван', False), ('петров иван', 'Иванов Пётр', False),
    ('елкин артем', 'Ёлкин Артём', True), ('ЖИГАЛОВ СЕРГЕЙ', 'Жигалов Сергей', True),
    ('ÉLODIE', 'Durand Élodie', True), ('Петров Иван Иванович', 'Петров Иван Иванович', True),
    ('Иванович Петров Иван', 'Петров Иван Иванович', True),
    ('Несуществующий', 'Петров Иван', False), ('%','Петров Иван',False),
    ('_', 'Петров Иван', False), ("' OR 1=1 --", 'Петров Иван', False),
    ('   ', 'Петров Иван', True),
])
def test_matching_uses_distinct_name_parts_and_unicode(query, name, expected):
    assert matches_name(name, query) is expected
