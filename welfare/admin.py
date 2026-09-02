"""Django Admin per CRUD e manutenzione secondaria."""

from django.contrib import admin

from .models import (
    Convention,
    EmployeeBudget,
    EmployeeProfile,
    RequestAttachment,
    VoucherAllocation,
    VoucherDelivery,
    VoucherRequest,
    VoucherType,
    WelfareProgram,
)


@admin.register(EmployeeProfile)
class EmployeeProfileAdmin(admin.ModelAdmin):
    list_display = ("display_name", "employee_code", "active", "created_at")
    list_filter = ("active",)
    search_fields = ("user__username", "user__first_name", "user__last_name", "user__email", "employee_code")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(WelfareProgram)
class WelfareProgramAdmin(admin.ModelAdmin):
    list_display = ("name", "start_date", "end_date", "active")
    list_filter = ("active",)
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Convention)
class ConventionAdmin(admin.ModelAdmin):
    list_display = ("name", "active", "created_at")
    list_filter = ("active",)
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(VoucherType)
class VoucherTypeAdmin(admin.ModelAdmin):
    list_display = ("convention", "name", "unit_value", "active", "is_used")
    list_filter = ("active", "convention")
    search_fields = ("name", "convention__name")
    readonly_fields = ("created_at", "updated_at")

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.is_used:
            # Il valore unitario di un tipo voucher già allocato è immutabile.
            readonly += ["unit_value", "convention"]
        return readonly

    @admin.display(boolean=True, description="già allocato")
    def is_used(self, obj):
        return obj.is_used


@admin.register(EmployeeBudget)
class EmployeeBudgetAdmin(admin.ModelAdmin):
    list_display = ("employee", "welfare_program", "amount", "updated_at", "updated_by")
    list_filter = ("welfare_program",)
    search_fields = ("employee__user__username", "employee__user__last_name")
    readonly_fields = ("created_at", "updated_at", "created_by", "updated_by")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(VoucherAllocation)
class VoucherAllocationAdmin(admin.ModelAdmin):
    list_display = (
        "employee",
        "voucher_type",
        "quantity_assigned",
        "quantity_pending",
        "quantity_approved_waiting_delivery",
        "quantity_delivered",
        "quantity_available",
    )
    list_filter = ("welfare_program", "voucher_type__convention")
    search_fields = ("employee__user__username", "employee__user__last_name")
    readonly_fields = ("assigned_at", "updated_at", "assigned_by", "updated_by")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.assigned_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


class RequestAttachmentInline(admin.TabularInline):
    model = RequestAttachment
    extra = 0
    readonly_fields = ("uploaded_at", "uploaded_by", "original_filename")


@admin.register(VoucherRequest)
class VoucherRequestAdmin(admin.ModelAdmin):
    list_display = ("pk", "employee", "voucher_type", "quantity", "status", "requested_at")
    list_filter = ("status", "allocation__welfare_program")
    search_fields = ("allocation__employee__user__username", "allocation__employee__user__last_name")
    readonly_fields = ("requested_at", "processed_at", "processed_by")
    inlines = [RequestAttachmentInline]


@admin.register(VoucherDelivery)
class VoucherDeliveryAdmin(admin.ModelAdmin):
    list_display = ("pk", "allocation", "quantity", "delivered_at", "delivered_by", "is_direct")
    list_filter = ("allocation__welfare_program",)
    search_fields = ("allocation__employee__user__last_name",)
    readonly_fields = ("delivered_at", "delivered_by")

    @admin.display(boolean=True, description="consegna diretta")
    def is_direct(self, obj):
        return obj.is_direct

    def save_model(self, request, obj, form, change):
        if not change:
            obj.delivered_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(RequestAttachment)
class RequestAttachmentAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "request", "uploaded_at", "uploaded_by")
    readonly_fields = ("uploaded_at", "uploaded_by", "original_filename")


admin.site.site_header = "Welfare aziendale — amministrazione"
admin.site.site_title = "Welfare aziendale"
admin.site.index_title = "Manutenzione dati"
