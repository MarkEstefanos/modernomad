import logging

from django.contrib.auth.models import User

from core.models import Booking
from core.views.occupancy import monthly_occupant_report

logger = logging.getLogger(__name__)


def monthly_occupant_report_console(location_slug, year, month):
    (occupants, messages) = monthly_occupant_report(location_slug, year, month)
    logger.debug(f"occupancy report for {month} {year}")
    logger.debug(
        "name, email, total_nights, total_value, total_comped, owing, reference_ids"
    )
    logger.debug("Residents")
    for v in occupants["residents"].values():
        logger.debug("%s, %s, %d" % (v["name"], v["email"], v["total_nights"]))
    logger.debug("Guests")
    for v in occupants["guests"].values():
        logger.debug(
            "%s, %s, %d, %d, %d, %s, %s"
            % (
                v["name"],
                v["email"],
                v["total_nights"],
                v["total_value"],
                v["total_comped"],
                " ".join(map(str, v["owing"])),
                " ".join(map(str, v["ids"])),
            )
        )

    for message in messages:
        logger.debug(message)


def people_with_bookings_longer_than(min_length):
    # JKS this should probably be a manager method Booking.objects.length(...)
    users = []
    bookings = Booking.objects.all()
    for r in bookings:
        length = r.nights_between(r.arrive, r.depart)
        if length >= min_length:
            users.append(r.user)
    # make sure the returned list has only unique users
    return list(set(users))


def repeat_guests(num_stays, location=None):
    users = []
    all_users = User.objects.all()
    for u in all_users:
        if location:
            at_loc = u.bookings.filter(location=location).filter(status="confirmed")
            if len(at_loc) >= num_stays:
                users.append(u)
        else:
            if u.bookings.filter(status="confirmed").count() >= num_stays:
                users.append(u)
    return users
