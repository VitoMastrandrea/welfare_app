"""Helper condivisi dai test."""

from decimal import Decimal

from django.contrib.auth.models import Group, User

from welfare import services
from welfare.models import Convention, EmployeeProfile, VoucherType, WelfareProgram
from welfare.permissions import WELFARE_MANAGERS_GROUP

PASSWORD = "test-password-123"


def create_user(username: str, manager: bool = False, **kwargs) -> User:
    user = User.objects.create_user(
        username=username,
        password=PASSWORD,
        first_name=kwargs.pop("first_name", username.capitalize()),
        last_name=kwargs.pop("last_name", "Test"),
        email=kwargs.pop("email", f"{username}@example.com"),
        **kwargs,
    )
    if manager:
        group, _ = Group.objects.get_or_create(name=WELFARE_MANAGERS_GROUP)
        user.groups.add(group)
    return user


def create_employee(username: str, manager: bool = False, **kwargs) -> EmployeeProfile:
    user = create_user(username, manager=manager, **kwargs)
    return EmployeeProfile.objects.create(user=user, employee_code=username.upper())


def create_program(name: str = "Piano Welfare") -> WelfareProgram:
    return WelfareProgram.objects.create(name=name, active=True)


def create_voucher_type(
    convention_name: str = "Muraglia Srlrs",
    name: str = "Buono spesa",
    unit_value: str = "100.00",
) -> VoucherType:
    convention, _ = Convention.objects.get_or_create(name=convention_name)
    voucher_type, _ = VoucherType.objects.get_or_create(
        convention=convention, name=name, unit_value=Decimal(unit_value)
    )
    return voucher_type


def setup_allocation(
    *,
    employee: EmployeeProfile,
    program: WelfareProgram,
    voucher_type: VoucherType,
    quantity: int,
    budget: str = "5000.00",
    actor: User | None = None,
):
    actor = actor or employee.user
    services.set_employee_budget(
        employee=employee, program=program, amount=Decimal(budget), actor=actor
    )
    return services.set_allocation_quantity(
        employee=employee,
        program=program,
        voucher_type=voucher_type,
        quantity=quantity,
        actor=actor,
    )
