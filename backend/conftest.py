"""
Test-session setup.

Deliberately empty of behaviour.

── WHAT USED TO BE HERE, AND WHY IT IS GONE ────────────────────────────────────

A six-line shim replacing `django.template.context.BaseContext.__copy__`.
Django 5.1 wrote it as `duplicate = copy(super())`, which relied on `copy()`
unwrapping the super proxy to the instance behind it. Python 3.14 stopped
doing that, so copying a template context raised AttributeError — and Django's
own test client copies the context of every template rendered during a request,
which took out every test that sent an HTML email.

Its docstring said it would "disappear on its own when we move to a Django that
has fixed this upstream". Django 5.2 constructs a `BaseContext()` directly and
never touches the super proxy, so it has, and it did.

Left as a file rather than deleted: pytest resolves the project root from where
conftest.py sits, and removing it moves rootdir, which changes how every test
path in the suite and in CI is interpreted.
"""
