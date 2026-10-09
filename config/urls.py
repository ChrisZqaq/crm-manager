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
    path("reports/<slug:report_name>/export/", views.report_page_export, name="report-export"),
    path("reports/<slug:report_name>/", views.report_page, name="report-page"),
    path("receiving/", views.receiving_page, name="receiving"),
    path("opening/", views.opening_page, name="opening"),
      path("holds/", views.holds_page, name="holds"),
      path("adjustments/", views.adjustments_page, name="adjustments"),
    path("customers/", views.customers_page, name="customers"),
    path("customers/new/", views.customer_form_page, name="customer-new"),
    path("customers/<int:customer_id>/edit/", views.customer_form_page, name="customer-edit"),
    path("orders/", views.orders_page, name="orders"),
    path("orders/new/", views.order_form_page, name="order-new"),
    path("orders/<int:order_id>/", views.order_detail_page, name="order-detail"),
    path("orders/<int:order_id>/edit/", views.order_form_page, name="order-edit"),
    path("api/v1/products", views.products_api, name="api-products"),
    path("api/v1/product-languages", views.product_languages_api, name="api-product-languages"),
    path("api/v1/skus", views.skus_api, name="api-skus"),
    path("api/v1/skus/number-suggestion", views.sku_number_suggestion_api, name="api-sku-number-suggestion"),
    path("api/v1/warehouses", views.warehouses_api, name="api-warehouses"),
    path("api/v1/suppliers", views.suppliers_api, name="api-suppliers"),
    path("api/v1/inventory", views.inventory_api, name="api-inventory"),
    path("api/v1/reports/<slug:report_name>/export", views.reports_export_api, name="api-report-export"),
    path("api/v1/reports/<slug:report_name>", views.reports_api, name="api-report"),
    path("api/v1/holds", views.holds_api, name="api-holds"),
    path("api/v1/holds/<int:hold_id>/release", views.hold_release_api, name="api-hold-release"),
      path("api/v1/damaged-records", views.damaged_records_api, name="api-damaged-records"),
      path("api/v1/damaged-records/<int:damaged_record_id>/resolve", views.damage_resolution_api, name="api-damage-resolution"),
      path("api/v1/splits", views.inventory_split_api, name="api-inventory-split"),
      path("api/v1/gifts", views.inventory_gift_api, name="api-inventory-gift"),
      path("api/v1/stock-counts", views.stock_counts_api, name="api-stock-counts"),
      path("api/v1/stock-counts/<int:count_id>", views.stock_count_detail_api, name="api-stock-count-detail"),
      path("api/v1/stock-counts/<int:count_id>/update", views.stock_count_update_api, name="api-stock-count-update"),
      path("api/v1/stock-counts/<int:count_id>/approve", views.stock_count_approve_api, name="api-stock-count-approve"),
      path("api/v1/cost-corrections", views.cost_corrections_api, name="api-cost-corrections"),
      path("api/v1/cost-corrections/preview", views.cost_correction_preview_api, name="api-cost-correction-preview"),
      path("api/v1/cost-corrections/submit", views.cost_corrections_api, name="api-cost-correction-submit"),
      path("api/v1/cost-corrections/<int:correction_id>", views.cost_correction_detail_api, name="api-cost-correction-detail"),
      path("api/v1/cost-corrections/<int:correction_id>/approve", views.cost_correction_approve_api, name="api-cost-correction-approve"),
      path("api/v1/cost-corrections/<int:correction_id>/reject", views.cost_correction_reject_api, name="api-cost-correction-reject"),
    path("api/v1/opening/preview", views.opening_preview_api, name="api-opening-preview"),
    path("api/v1/opening/confirm", views.opening_confirm_api, name="api-opening-confirm"),
    path("api/v1/opening/control", views.opening_control_api, name="api-opening-control"),
    path("api/v1/receipts/preview", views.receipt_preview_api, name="api-receipt-preview"),
    path("api/v1/receipts/confirm", views.receipt_confirm_api, name="api-receipt-confirm"),
    path("api/v1/settings/low-stock", views.low_stock_settings_api, name="api-low-stock-settings"),
    path("api/v1/skus/<int:sku_id>/low-stock", views.sku_low_stock_update_api, name="api-sku-low-stock"),
    path("api/v1/images", views.receipt_image_upload_api, name="api-receipt-image-upload"),
    path("api/v1/images/<int:image_id>", views.receipt_image_api, name="api-receipt-image"),
    path("api/v1/regions", views.regions_api, name="api-regions"),
    path("api/v1/channels", views.channels_api, name="api-channels"),
    path("api/v1/payment-accounts", views.payment_accounts_api, name="api-payment-accounts"),
    path("api/v1/customers", views.customers_api, name="api-customers"),
    path("api/v1/customers/<int:customer_id>", views.customer_detail_api, name="api-customer-detail"),
    path("api/v1/customers/<int:customer_id>/archive", views.customer_archive_api, name="api-customer-archive"),
    path("api/v1/customers/<int:customer_id>/transfer", views.customer_transfer_api, name="api-customer-transfer"),
    path("api/v1/orders", views.orders_api, name="api-orders"),
    path("api/v1/orders/preview", views.order_preview_api, name="api-order-preview"),
    path("api/v1/orders/<int:order_id>", views.order_detail_api, name="api-order-detail"),
    path("api/v1/orders/<int:order_id>/amend/preview", views.order_amend_preview_api, name="api-order-amend-preview"),
    path("api/v1/orders/<int:order_id>/amend", views.order_amend_api, name="api-order-amend"),
    path("api/v1/orders/<int:order_id>/cancel", views.order_cancel_api, name="api-order-cancel"),
    path("api/v1/orders/<int:order_id>/payment", views.order_payment_api, name="api-order-payment"),
    path("api/v1/orders/<int:order_id>/verify-payment", views.order_payment_verify_api, name="api-order-payment-verify"),
    path("api/v1/orders/<int:order_id>/ship", views.order_ship_api, name="api-order-ship"),
    path("api/v1/orders/<int:order_id>/deliver-on-site", views.order_deliver_on_site_api, name="api-order-deliver-on-site"),
    path("api/v1/orders/<int:order_id>/receive", views.order_receive_api, name="api-order-receive"),
    path("api/v1/orders/<int:order_id>/freight", views.order_freight_api, name="api-order-freight"),
    path("api/v1/orders/<int:order_id>/review-commission", views.order_commission_review_api, name="api-order-commission-review"),
    path("api/v1/commission-adjustments/<int:adjustment_id>/complete", views.commission_adjustment_complete_api, name="api-commission-adjustment-complete"),
]
