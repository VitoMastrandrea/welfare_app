"""URL dell'applicazione welfare."""

from django.urls import path

from . import views

urlpatterns = [
    # Area dipendente
    path("", views.dashboard, name="dashboard"),
    path("i-miei-voucher/", views.my_vouchers, name="my_vouchers"),
    path("catalogo/", views.catalog, name="catalog"),
    path("le-mie-richieste/", views.my_requests, name="my_requests"),
    path("richieste/<int:pk>/", views.request_detail, name="request_detail"),
    path(
        "voucher/<int:allocation_id>/richiedi/",
        views.request_voucher,
        name="request_voucher",
    ),
    path(
        "voucher/<int:allocation_id>/riepilogo/",
        views.request_summary_partial,
        name="request_summary_partial",
    ),
    path("allegati/<int:pk>/download/", views.attachment_download, name="attachment_download"),
    # Area amministrazione welfare
    path("amministrazione/", views.admin_dashboard, name="admin_dashboard"),
    path("amministrazione/dipendenti/", views.admin_employees, name="admin_employees"),
    path(
        "amministrazione/dipendenti/<int:pk>/",
        views.admin_employee_detail,
        name="admin_employee_detail",
    ),
    path(
        "amministrazione/dipendenti/<int:pk>/budget/",
        views.admin_employee_budget,
        name="admin_employee_budget",
    ),
    path(
        "amministrazione/dipendenti/<int:pk>/assegna-voucher/",
        views.admin_allocate,
        name="admin_allocate_employee",
    ),
    path(
        "amministrazione/dipendenti/<int:pk>/consegna-diretta/",
        views.admin_direct_delivery,
        name="admin_direct_delivery",
    ),
    path("amministrazione/assegna-voucher/", views.admin_allocate, name="admin_allocate"),
    path("amministrazione/allocazioni/", views.admin_allocations, name="admin_allocations"),
    path("amministrazione/richieste/", views.admin_requests, name="admin_requests"),
    path(
        "amministrazione/richieste/<int:pk>/",
        views.admin_request_detail,
        name="admin_request_detail",
    ),
    path(
        "amministrazione/richieste/<int:pk>/approva/",
        views.admin_request_approve,
        name="admin_request_approve",
    ),
    path(
        "amministrazione/richieste/<int:pk>/rifiuta/",
        views.admin_request_reject,
        name="admin_request_reject",
    ),
    path(
        "amministrazione/richieste/<int:pk>/consegna/",
        views.admin_request_deliver,
        name="admin_request_deliver",
    ),
    path("amministrazione/consegne/", views.admin_deliveries, name="admin_deliveries"),
    path("amministrazione/convenzioni/", views.admin_conventions, name="admin_conventions"),
    path(
        "amministrazione/convenzioni/nuova/",
        views.admin_convention_form,
        name="admin_convention_create",
    ),
    path(
        "amministrazione/convenzioni/<int:pk>/modifica/",
        views.admin_convention_form,
        name="admin_convention_edit",
    ),
    path(
        "amministrazione/tipi-voucher/nuovo/",
        views.admin_voucher_type_form,
        name="admin_voucher_type_create",
    ),
    path(
        "amministrazione/tipi-voucher/<int:pk>/modifica/",
        views.admin_voucher_type_form,
        name="admin_voucher_type_edit",
    ),
    path("amministrazione/programmi/", views.admin_programs, name="admin_programs"),
    path(
        "amministrazione/programmi/nuovo/",
        views.admin_program_form,
        name="admin_program_create",
    ),
    path(
        "amministrazione/programmi/<int:pk>/modifica/",
        views.admin_program_form,
        name="admin_program_edit",
    ),
    # Frammenti HTMX amministrazione
    path(
        "amministrazione/htmx/riepilogo-allocazione/",
        views.admin_allocation_summary_partial,
        name="admin_allocation_summary_partial",
    ),
    path(
        "amministrazione/htmx/riepilogo-consegna/",
        views.admin_delivery_summary_partial,
        name="admin_delivery_summary_partial",
    ),
]
