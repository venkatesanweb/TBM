from django.urls import path

from sales.views import (
    SaleDetailView,
    SaleListCreateView,
    SaleReturnDecisionView,
    SaleReturnView,
    SaleScanLookupView,
    SaleStatusView,
    SalesPlatformDetailView,
    SalesPlatformListCreateView,
    SalesBulkStatusView,
)
from sales.dashboard import (
    DashboardReportView,
    FinishedGoodsDashboardView,
    InventoryDashboardView,
    PaymentsDashboardView,
    ProductionDashboardView,
    ReturnsDashboardView,
    SalesOverviewDashboardView,
)

urlpatterns = [
    path("dashboard/sales-overview/", SalesOverviewDashboardView.as_view(), name="dashboard-sales-overview"),
    path("dashboard/payments/", PaymentsDashboardView.as_view(), name="dashboard-payments"),
    path("dashboard/returns/", ReturnsDashboardView.as_view(), name="dashboard-returns"),
    path("dashboard/inventory/", InventoryDashboardView.as_view(), name="dashboard-inventory"),
    path("dashboard/finished-goods/", FinishedGoodsDashboardView.as_view(), name="dashboard-finished-goods"),
    path("dashboard/production/", ProductionDashboardView.as_view(), name="dashboard-production"),
    path("dashboard/reports/<str:report_name>/", DashboardReportView.as_view(), name="dashboard-report"),
    path("platforms/", SalesPlatformListCreateView.as_view(), name="sales-platform-list"),
    path(
        "platforms/<int:platform_id>/",
        SalesPlatformDetailView.as_view(),
        name="sales-platform-detail",
    ),
    path("sales/scan/", SaleScanLookupView.as_view(), name="sale-scan"),
    path("sales/bulk-status/", SalesBulkStatusView.as_view(), name="sale-bulk-status"),
    path("sales/", SaleListCreateView.as_view(), name="sale-list"),
    path(
        "sales/<int:sale_id>/status/",
        SaleStatusView.as_view(),
        name="sale-status",
    ),
    path(
        "sales/<int:sale_id>/return/",
        SaleReturnView.as_view(),
        name="sale-return",
    ),
    path(
        "sales/<int:sale_id>/return-decision/",
        SaleReturnDecisionView.as_view(),
        name="sale-return-decision",
    ),
    path("sales/<int:sale_id>/", SaleDetailView.as_view(), name="sale-detail"),
]
