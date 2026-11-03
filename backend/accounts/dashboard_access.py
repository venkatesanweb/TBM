from rest_framework.exceptions import PermissionDenied

from accounts.models import User


SALES_OVERVIEW = "sales_overview"
PAYMENTS = "payments"
RETURNS = "returns"
INVENTORY = "inventory"
FINISHED_GOODS = "finished_goods"
PRODUCTION = "production"

DASHBOARD_SECTIONS = (
    SALES_OVERVIEW,
    PAYMENTS,
    RETURNS,
    INVENTORY,
    FINISHED_GOODS,
    PRODUCTION,
)

SECTION_LABELS = {
    SALES_OVERVIEW: "Sales overview",
    PAYMENTS: "Payments",
    RETURNS: "Returns",
    INVENTORY: "Inventory",
    FINISHED_GOODS: "Finished goods",
    PRODUCTION: "Production",
}

REPORT_SECTIONS = {
    "sales": SALES_OVERVIEW,
    "inventory": INVENTORY,
    "payments": PAYMENTS,
    "returns": RETURNS,
}


def has_dashboard_section(user, section):
    return user.role == User.Role.SUPER_ADMIN or section in user.dashboard_sections


def require_dashboard_section(user, section):
    if not has_dashboard_section(user, section):
        raise PermissionDenied("You do not have access to this dashboard section.")


def require_report_export(user):
    if user.role != User.Role.SUPER_ADMIN and not user.can_export_dashboard_reports:
        raise PermissionDenied("You do not have permission to export reports.")
