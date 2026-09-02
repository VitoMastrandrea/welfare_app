"""Filtri di presentazione (formattazione italiana degli importi)."""

from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def euro(value) -> str:
    """Formatta un importo in stile italiano: 5000 -> «5.000,00»."""
    if value is None or value == "":
        return "0,00"
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    formatted = f"{amount:,.2f}"
    return formatted.replace(",", "§").replace(".", ",").replace("§", ".")


@register.filter
def get_item(mapping, key):
    """Accesso a un dizionario per chiave dinamica nei template."""
    if hasattr(mapping, "get"):
        return mapping.get(key)
    return None


@register.filter
def bs_class(bound_field, css: str = "form-control"):
    """Applica una classe Bootstrap al widget se non ne ha già una."""
    widget = bound_field.field.widget
    attrs = dict(widget.attrs)
    if not attrs.get("class"):
        attrs["class"] = css
    return bound_field.as_widget(attrs=attrs)
