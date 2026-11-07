from django.urls import path

from catalog.views import (
    BOMLineDetailView,
    BOMLineListCreateView,
    BarcodeLookupView,
    ComponentDetailView,
    ComponentListCreateView,
    ComponentPricingView,
    ComponentPricePreviewView,
    ComponentQuickCreateView,
    CompanyProfileView,
    ProductPricingView,
    ProductPriceCreateView,
    ProductPricePreviewView,
    ScannedComponentReceiptView,
    ProductDetailView,
    ProductListCreateView,
    ProductRevisionDetailView,
    ProductRevisionListCreateView,
    TaxRateDetailView,
    TaxRateListCreateView,
)

urlpatterns = [
    path("company-profile/", CompanyProfileView.as_view(), name="company-profile"),
    path("barcodes/lookup/", BarcodeLookupView.as_view(), name="barcode-lookup"),
    path(
        "components/receive-scanned/",
        ScannedComponentReceiptView.as_view(),
        name="component-receive-scanned",
    ),
    path("products/", ProductListCreateView.as_view(), name="product-list"),
    path("products/pricing/", ProductPricingView.as_view(), name="product-pricing"),
    path(
        "products/<int:product_id>/prices/",
        ProductPriceCreateView.as_view(),
        name="product-price-create",
    ),
    path(
        "products/<int:product_id>/pricing/preview/",
        ProductPricePreviewView.as_view(),
        name="product-price-preview",
    ),
    path("products/<int:pk>/", ProductDetailView.as_view(), name="product-detail"),
    path("tax-rates/", TaxRateListCreateView.as_view(), name="tax-rate-list"),
    path("tax-rates/<int:pk>/", TaxRateDetailView.as_view(), name="tax-rate-detail"),
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
        "components/pricing/",
        ComponentPricingView.as_view(),
        name="component-pricing",
    ),
    path(
        "components/pricing/preview/",
        ComponentPricePreviewView.as_view(),
        name="component-price-preview",
    ),
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
