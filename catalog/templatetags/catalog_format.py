from django import template

register = template.Library()


@register.filter
def vnd(value):
    try:
        return f"{value:,.0f}".replace(",", ".") + " đ"
    except (TypeError, ValueError):
        return value
