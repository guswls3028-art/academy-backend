"""Tenant-scoped active instructor directory shared with student workflows."""
from .models import Teacher


def active_teachers(tenant):
    return Teacher.objects.filter(tenant=tenant, is_active=True)
