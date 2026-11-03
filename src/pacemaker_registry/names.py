"""Read-only name matching; imported names retain their original spelling."""


def matches_name(full_name: str, query: str) -> bool:
    parts = full_name.casefold().replace('ё', 'е').split()
    terms = sorted(set(query.casefold().replace('ё', 'е').split()), key=len, reverse=True)
    if len(terms) > len(parts):
        return False

    def match(index, available):
        if index == len(terms):
            return True
        return any(terms[index] in part and match(index + 1, available[:i] + available[i + 1:])
                   for i, part in enumerate(available))

    # Distinct components: «Пётр Петров» must not match «Иван Петров» twice by surname.
    return match(0, parts)
