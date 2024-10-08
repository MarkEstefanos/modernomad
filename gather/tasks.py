import datetime
import logging
from itertools import chain

from django.contrib.sites.models import Site
from django.template.loader import get_template
from django.urls import reverse
from django.utils import timezone
from zoneinfo import ZoneInfo

from core.emails.mailgun import mailgun_send
from core.models import Location
from gather.models import Event, EventNotifications

logger = logging.getLogger(__name__)

weekday_number_to_name = {
    0: "Monday",
    1: "Tuesday",
    2: "Wednesday",
    3: "Thursday",
    4: "Friday",
    5: "Saturday",
    6: "Sunday",
}


def send_events_list(user, event_list, location):
    profile_url = reverse("user_detail", args=(user.username,))
    footer = f"You are receiving this email because your preferences for event reminders are on. To turn them off, visit {profile_url}"
    sender = location.from_email()
    subject = (
        "[" + location.email_subject_prefix + "]" + " Reminder of your events today"
    )
    current_tz = timezone.get_current_timezone()
    today_local = timezone.now().astimezone(current_tz).date()
    day_of_week = weekday_number_to_name[today_local.weekday()]
    plaintext = get_template("emails/events_today.txt")
    domain = Site.objects.get_current().domain
    c = {
        "user": user,
        "events": event_list,
        "location_name": location.name,
        "location": location,
        "domain": domain,
        "footer": footer,
        "day_of_week": day_of_week,
    }
    text_content = plaintext.render(c)
    mailgun_data = {
        "from": sender,
        "to": user.email,
        "subject": subject,
        "text": text_content,
    }
    return mailgun_send(mailgun_data)


def weekly_reminder_email(user, event_list, location):
    profile_url = reverse("user_detail", args=(user.username,))
    location_name = location.name
    current_tz = timezone.get_current_timezone()
    today_local = timezone.now().astimezone(current_tz).date()
    tomorrow_local = today_local + datetime.timedelta(days=1)
    week_name = tomorrow_local.strftime("%B %d, %Y")
    footer = f"You are receiving this email because you requested weekly updates of upcoming events from {location_name}. To turn them off, visit {profile_url}"
    sender = location.from_email()
    subject = (
        "["
        + location.email_subject_prefix
        + "]"
        + f" Upcoming events for the week of {week_name}"
    )
    current_tz = timezone.get_current_timezone()
    today_local = timezone.now().astimezone(current_tz).date()
    plaintext = get_template("emails/events_this_week.txt")
    htmltext = get_template("emails/events_this_week.html")
    domain = Site.objects.get_current().domain

    c_text = {
        "user": user,
        "events": event_list,
        "location_name": location_name,
        "location": location,
        "domain": domain,
        "footer": footer,
        "week_name": week_name,
    }
    text_content = plaintext.render(c_text)

    c_html = {
        "user": user,
        "events": event_list,
        "location_name": location_name,
        "location": location,
        "domain": domain,
        "footer": footer,
        "week_name": week_name,
    }
    html_content = htmltext.render(c_html)

    mailgun_data = {
        "from": sender,
        "to": user.email,
        "subject": subject,
        "text": text_content,
        "html": html_content,
    }
    return mailgun_send(mailgun_data)


def events_pending(location):
    # events seeking feeddback and waiting for review
    pending = (
        Event.objects.filter(location=location)
        .filter(start__gt=timezone.now())
        .filter(status="waiting for approval")
    )
    feedback = (
        Event.objects.filter(location=location)
        .filter(start__gt=timezone.now())
        .filter(status="seeking feedback")
    )
    ret = {"pending": pending, "feedback": feedback}
    return ret


def published_events_this_week_local(location):
    # we have to do a bunch of tomfoolery here because we want to gets events
    # that are "this week" in this week's timezone, but dates are stored in UTC which
    # is offset from the current timezone's hours by a certain amount.
    current_tz = timezone.get_current_timezone()
    today_local = timezone.now().astimezone(current_tz).date()
    tomorrow_local = today_local + datetime.timedelta(days=1)
    seven_days_from_now_local = today_local + datetime.timedelta(days=7)
    utc_tz = timezone("UTC")
    week_local_start_time = datetime.datetime(
        tomorrow_local.year, tomorrow_local.month, tomorrow_local.day, 0, 0
    )
    week_local_end_time = datetime.datetime(
        seven_days_from_now_local.year,
        seven_days_from_now_local.month,
        seven_days_from_now_local.day,
        23,
        59,
        59,
    )
    week_local_start_aware = timezone.make_aware(week_local_start_time, current_tz)
    week_local_end_aware = timezone.make_aware(week_local_end_time, current_tz)
    week_local_start_utc = utc_tz.normalize(week_local_start_aware.astimezone(utc_tz))
    week_local_end_utc = utc_tz.normalize(week_local_end_aware.astimezone(utc_tz))

    # get events happening today that are live
    starts_this_week_local = (
        Event.objects.filter(location=location)
        .filter(start__gte=week_local_start_utc)
        .filter(start__lte=week_local_end_utc)
        .filter(status="live")
        .filter(visibility=Event.PUBLIC)
    )
    ends_this_week_local = (
        Event.objects.filter(location=location)
        .filter(end__gte=week_local_start_utc)
        .filter(end__lte=week_local_end_utc)
        .filter(status="live")
        .filter(visibility=Event.PUBLIC)
    )
    across_this_week_local = (
        Event.objects.filter(location=location)
        .filter(start__lte=week_local_start_utc)
        .filter(end__gte=week_local_end_utc)
        .filter(status="live")
        .filter(visibility=Event.PUBLIC)
    )

    events_this_week_local = list(
        set(chain(starts_this_week_local, ends_this_week_local, across_this_week_local))
    )
    return events_this_week_local


def published_events_today_local(location):
    # we have to do a bunch of tomfoolery here because we want to gets events
    # that are "today" in today's timezone, but dates are stored in UTC which
    # is offset from the current timezone's hours by a certain amount.
    current_tz = timezone.get_current_timezone()
    today_local = timezone.now().astimezone(current_tz).date()
    today_local_start_time = datetime.datetime(
        today_local.year, today_local.month, today_local.day, 0, 0
    )
    today_local_end_time = datetime.datetime(
        today_local.year, today_local.month, today_local.day, 23, 59, 59
    )
    today_local_start_aware = timezone.make_aware(today_local_start_time, current_tz)
    today_local_end_aware = timezone.make_aware(today_local_end_time, current_tz)
    today_local_start_utc = today_local_start_aware.astimezone(ZoneInfo("UTC"))
    today_local_end_utc = today_local_end_aware.astimezone(ZoneInfo("UTC"))

    # get events happening today that are live
    starts_today_local = (
        Event.objects.filter(location=location)
        .filter(start__gte=today_local_start_utc)
        .filter(end__lte=today_local_end_utc)
        .filter(status="live")
    )
    ends_today_local = (
        Event.objects.filter(location=location)
        .filter(end__gte=today_local_start_utc)
        .filter(end__lte=today_local_end_utc)
        .filter(status="live")
    )
    across_today_local = (
        Event.objects.filter(location=location)
        .filter(start__lte=today_local_start_utc)
        .filter(end__gte=today_local_end_utc)
        .filter(status="live")
    )

    events_today_local = list(
        set(chain(starts_today_local, ends_today_local, across_today_local))
    )
    # returns all types of events - private, community and public.
    return events_today_local


def events_today_reminder():
    logger.info("Running task: events_today_reminder")
    locations = Location.objects.all()
    for location in locations:
        events_today_local = published_events_today_local(location)
        if len(events_today_local) == 0:
            continue
        # for each event,
        #    for each attendee or organizer
        #        if they want reminders, append this event to a list of reminders for today, for that person.
        reminders_per_person = {}
        for event in events_today_local:
            distinct_event_people = list(
                set(list(event.attendees.all()) + list(event.organizers.all()))
            )
            for user in distinct_event_people:
                if (
                    user.event_notifications.reminders
                    and user not in reminders_per_person
                ):
                    reminders_this_person = reminders_per_person.get(user, [])
                    reminders_this_person.append(event)
                    reminders_per_person[user] = reminders_this_person

        for user, events_today in reminders_per_person.iteritems():
            send_events_list(user, events_today, location)


def weekly_upcoming_events():
    logger.info("Running task: weekly_upcoming_events")
    # gets a list of events to send reminders about *for all locations* one by one.
    locations = Location.objects.all()
    for location in locations:
        events_this_week_at_location = published_events_this_week_local(location)
        if len(events_this_week_at_location) == 0:
            logger.debug(
                f"no events this week at {location.name}; skipping email notification"
            )
            continue
        # for each event,
        #    for each attendee or organizer
        #        if they want reminders, append this event to a list of reminders for today, for that person.
        weekly_notifications_on = EventNotifications.objects.filter(
            location_weekly=location
        )
        remindees_for_location = [notify.user for notify in weekly_notifications_on]

        for user in remindees_for_location:
            weekly_reminder_email(user, events_this_week_at_location, location)
