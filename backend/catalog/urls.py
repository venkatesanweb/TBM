from django.urls import path

from catalog.views import (
    BOMLineDetailView,
    BOMLineListCreateView,
    ComponentDetailView,
    ComponentListCreateView,
    ComponentQuickCreateView,
    ProductDetailView,
    ProductListCreateView,
    ProductRevisionDetailView,
    ProductRevisionListCreateView,
)

urlpatterns = [
    path("products/", ProductListCreateView.as_view(), name="product-list"),
    path("products/<int:pk>/", ProductDetailView.as_view(), name="product-detail"),
    path(
        "products/<int:product_id>/revisions/",
        ProductRevisionListCreateView.as_view(),
        name="product-revision-list",
    ),
    path(
        "revisions/<int:pk>/",
        ProductRevisionDetailView.as_view(),
        name="product-revision-detail",
    ),
    path(
        "revisions/<int:revision_id>/bom/",
        BOMLineListCreateView.as_view(),
        name="bom-line-list",
    ),
    path("bom-lines/<int:pk>/", BOMLineDetailView.as_view(), name="bom-line-detail"),
    path("components/", ComponentListCreateView.as_view(), name="component-list"),
    path(
        "components/quick-create/",
        ComponentQuickCreateView.as_view(),
        name="component-quick-create",
    ),
    path(
        "components/<int:pk>/",
        ComponentDetailView.as_view(),
        name="component-detail",
    ),
]
