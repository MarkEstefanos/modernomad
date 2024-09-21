from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import user_passes_test
from django.http import HttpResponseRedirect

from core.models import get_location


def group_required(*group_names):
    """Requires user membership in at least one of the groups passed in."""

    def in_groups(u):
        return bool(
            u.is_authenticated
            and bool(u.groups.filter(name__in=group_names)) | u.is_superuser
        )

    return user_passes_test(in_groups)


def house_admin_required(original_func):
    @wraps(original_func)
    def decorator(request, location_slug, *args, **kwargs):
        location = get_location(location_slug)
        user = request.user
        if user.is_authenticated and location and user in location.house_admins.all():
            return original_func(request, location_slug, *args, **kwargs)
        elif request.user.is_authenticated:
            return HttpResponseRedirect("/")
        else:
            from django.contrib.auth.views import redirect_to_login

            path = request.get_full_path()
            login_url = settings.LOGIN_URL
            return redirect_to_login(path, login_url)

    return decorator


def resident_or_admin_required(original_func):
    @wraps(original_func)
    def decorator(request, location_slug, *args, **kwargs):
        location = get_location(location_slug)
        user = request.user
        if (
            user.is_authenticated
            and location
            and (
                user in location.residents()
                or user in location.house_admins.all()
                or user in location.readonly_admins.all()
            )
        ):
            return original_func(request, location_slug, *args, **kwargs)
        elif request.user.is_authenticated:
            return HttpResponseRedirect("/")
        else:
            from django.contrib.auth.views import redirect_to_login

            path = request.get_full_path()
            login_url = settings.LOGIN_URL
            return redirect_to_login(path, login_url)

    return decorator
