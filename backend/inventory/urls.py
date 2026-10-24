from django.urls import path

from inventory.views import (
    AdjustmentListCreateView,
    AdjustmentReviewView,
    ComponentStockDetailView,
    InventoryListView,
    LowStockAlertListView,
    ReceiveStockView,
)

urlpatterns = [
    path("inventory/", InventoryListView.as_view(), name="inventory-list"),
    path(
        "inventory/low-stock/",
        LowStockAlertListView.as_view(),
        name="inventory-low-stock",
    ),
    path(
        "inventory/receive/",
        ReceiveStockView.as_view(),
        name="inventory-receive",
    ),
    path(
        "inventory/adjustments/",
        AdjustmentListCreateView.as_view(),
        name="inventory-adjustments",
    ),
    path(
        "inventory/adjustments/<int:adjustment_id>/<str:decision>/",
        AdjustmentReviewView.as_view(),
        name="inventory-adjustment-review",
    ),
    path(
        "inventory/<int:component_id>/",
        ComponentStockDetailView.as_view(),
        name="inventory-component-stock",
    ),
]
