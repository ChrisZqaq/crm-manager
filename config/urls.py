"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from crm import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", views.home, name="home"),
    path("catalog/", views.catalog_search, name="catalog-search"),
    path("inventory/", views.inventory_page, name="inventory"),
    path("receiving/", views.receiving_page, name="receiving"),
    path("opening/", views.opening_page, name="opening"),
    path("holds/", views.holds_page, name="holds"),
    path("api/v1/products", views.products_api, name="api-products"),
    path("api/v1/skus", views.skus_api, name="api-skus"),
    path("api/v1/warehouses", views.warehouses_api, name="api-warehouses"),
    path("api/v1/suppliers", views.suppliers_api, name="api-suppliers"),
    path("api/v1/inventory", views.inventory_api, name="api-inventory"),
    path("api/v1/holds", views.holds_api, name="api-holds"),
    path("api/v1/holds/<int:hold_id>/release", views.hold_release_api, name="api-hold-release"),
    path("api/v1/damaged-records", views.damaged_records_api, name="api-damaged-records"),
    path("api/v1/opening/preview", views.opening_preview_api, name="api-opening-preview"),
    path("api/v1/opening/confirm", views.opening_confirm_api, name="api-opening-confirm"),
    path("api/v1/receipts/preview", views.receipt_preview_api, name="api-receipt-preview"),
    path("api/v1/receipts/confirm", views.receipt_confirm_api, name="api-receipt-confirm"),
    path("api/v1/settings/low-stock", views.low_stock_settings_api, name="api-low-stock-settings"),
    path("api/v1/skus/<int:sku_id>/low-stock", views.sku_low_stock_update_api, name="api-sku-low-stock"),
    path("api/v1/images", views.receipt_image_upload_api, name="api-receipt-image-upload"),
    path("api/v1/images/<int:image_id>", views.receipt_image_api, name="api-receipt-image"),
]
