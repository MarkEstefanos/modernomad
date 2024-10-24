import json
import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.http import HttpResponse
from django.template import Context, Template, TemplateDoesNotExist
from django.template.loader import get_template
from django.urls import reverse
from django.utils import timezone, translation
from django.views.decorators.csrf import csrf_exempt

from core.emails.mailgun import mailgun_send
from core.models import (
    LocationEmailTemplate,
    Use,
    get_location,
)
from gather.models import Event, EventAdminGroup, EventNotifications
from gather.tasks import events_pending, published_events_today_local

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


def send_from_location_address(
    subject, text_content, html_content, recipient, location
):
    """a somewhat generic send function using mailgun that sends plaintext
    from the location's generic stay@ address."""
    mailgun_data = {
        "from": location.from_email(),
        "to": [
            recipient,
        ],
        "subject": subject,
        "text": text_content,
    }
    if html_content:
        mailgun_data["html"] = html_content
    return mailgun_send(mailgun_data)


def render_templates(context, location, email_key, language="en-us"):
    prev_language = translation.get_language()
    translation.activate(language)
    template_override = LocationEmailTemplate.objects.filter(
        location=location, key=email_key
    )
    text_content = None
    html_content = None

    if template_override and template_override.count() > 0:
        t = template_override[0]
        if t.text_body:
            text_content = Template(t.text_body).render(Context(context))
        if t.html_body:
            html_content = Template(t.html_body).render(Context(context))
    else:
        try:
            text_content = get_template(f"emails/{email_key}.txt").render(context)
            html_content = get_template(f"emails/{email_key}.html").render(context)
        except TemplateDoesNotExist:
            logger.debug(f'There is no template for email key "{email_key}"')
            logger.debug("Exiting quietly")

    translation.activate(prev_language)
    return (text_content, html_content)


############################################
#            BOOKING EMAILS            #
############################################


def send_booking_receipt(booking, send_to=None):
    location = booking.use.location
    subject = f"[{location.email_subject_prefix}] Receipt for Booking {str(booking.use.arrive)} - {str(booking.use.depart)}"
    recipient = [send_to] if send_to else [booking.use.user.email]
    c = {
        "today": timezone.localtime(timezone.now()),
        "user": booking.use.user,
        "location": location,
        "booking": booking,
        "booking_url": "https://"
        + Site.objects.get_current().domain
        + booking.get_absolute_url(),
    }
    text_content, html_content = render_templates(
        c, location, LocationEmailTemplate.RECEIPT
    )
    if text_content or html_content:
        return send_from_location_address(
            subject, text_content, html_content, recipient, location
        )
    else:
        return False


def send_invoice(booking):
    """Trigger a reminder email to the guest about payment."""

    # TODO: Implement send_comp_invoice
    # if booking.is_comped():
    #     return send_comp_invoice(booking)

    location = booking.use.location
    subject = f"[{location.email_subject_prefix}] Thanks for Staying with us!"
    recipient = [
        booking.use.user.email,
    ]
    c = {
        "today": timezone.localtime(timezone.now()),
        "user": booking.use.user,
        "location": location,
        "booking": booking,
        "domain": Site.objects.get_current().domain,
    }
    text_content, html_content = render_templates(
        c, location, LocationEmailTemplate.INVOICE
    )
    return send_from_location_address(
        subject, text_content, html_content, recipient, location
    )


def new_booking_notify(booking):
    house_admins = booking.use.location.house_admins.all()
    domain = Site.objects.get_current().domain
    location = booking.use.location

    subject = f"[{location.email_subject_prefix}] Booking Request, {booking.use.user.first_name} {booking.use.user.last_name}, {str(booking.use.arrive)} - {str(booking.use.depart)}"
    location.from_email()
    recipients = []
    for admin in house_admins:
        recipients.append(admin.email)

    c = {
        "location": location.name,
        "status": booking.use.status,
        "user_image": settings.MEDIA_URL + str(booking.use.user.profile.image_thumb),
        "first_name": booking.use.user.first_name,
        "last_name": booking.use.user.last_name,
        "room_name": booking.use.resource.name,
        "arrive": str(booking.use.arrive),
        "depart": str(booking.use.depart),
        "purpose": booking.use.purpose,
        "comments": booking.comments,
        "bio": booking.use.user.profile.bio,
        "referral": booking.use.user.profile.referral,
        "projects": booking.use.user.profile.projects,
        "sharing": booking.use.user.profile.sharing,
        "discussion": booking.use.user.profile.discussion,
        "admin_url": "https://"
        + domain
        + reverse(
            "booking_manage",
            args=(
                location.slug,
                booking.id,
            ),
        ),
    }
    text_content, html_content = render_templates(
        c, location, LocationEmailTemplate.NEW_BOOKING
    )

    return send_from_location_address(
        subject, text_content, html_content, recipients, booking.use.location
    )


def updated_booking_notify(booking):
    reverse(
        "booking_manage",
        args=(
            booking.use.location.slug,
            booking.id,
        ),
    )
    text_content = "Howdy,\n\nA booking has been updated and requires your review."
    text_content += "\n\nManage this booking at {domain}{admin_path}."

    recipients = []
    for admin in booking.use.location.house_admins.all():
        if admin.email not in recipients:
            recipients.append(admin.email)
    subject = f"[{booking.use.location.email_subject_prefix}] Booking Updated, {booking.use.user.first_name} {booking.use.user.last_name}, {str(booking.use.arrive)} - {str(booking.use.depart)}"
    mailgun_data = {
        "from": booking.use.location.from_email(),
        "to": recipients,
        "subject": subject,
        "text": text_content,
    }
    return mailgun_send(mailgun_data)


def goodbye_email(use):
    """Send guest a departure email"""
    # this is split out by location because each location has a timezone that affects the value of 'today'
    domain = Site.objects.get_current().domain
    location = use.location

    c = {
        "first_name": use.user.first_name,
        "location": use.location,
        "booking_url": "https://"
        + domain
        + reverse(
            "booking_detail",
            args=(
                location.slug,
                use.booking.id,
            ),
        ),
        "new_booking_url": "https://"
        + domain
        + reverse("location_stay", args=(location.slug,)),
    }
    text_content, html_content = render_templates(
        c, location, LocationEmailTemplate.DEPARTURE
    )

    subject = f"[{location.email_subject_prefix}] Thank you for staying with us"
    mailgun_data = {
        "from": use.location.from_email(),
        "to": [use.user.email],
        "subject": subject,
        "text": text_content,
    }
    if html_content:
        mailgun_data["html"] = html_content

    return mailgun_send(mailgun_data)


def guest_welcome(use):
    """Send guest a welcome email"""
    # this is split out by location because each location has a timezone that affects the value of 'today'
    domain = Site.objects.get_current().domain
    location = use.location
    intersecting_uses = Use.objects.filter(arrive__gte=use.arrive).filter(
        depart__lte=use.depart
    )
    residents = location.residents()
    intersecting_events = (
        Event.objects.filter(location=location)
        .filter(start__gte=use.arrive)
        .filter(end__lte=use.depart)
    )
    day_of_week = weekday_number_to_name[use.arrive.weekday()]

    c = {
        "first_name": use.user.first_name,
        "day_of_week": day_of_week,
        "location": use.location,
        "use": use,
        "current_email": f"current@{location.slug}.mail.embassynetwork.com",
        "site_url": "https://"
        + domain
        + reverse("location_detail", args=(location.slug,)),
        "events_url": "https://"
        + domain
        + reverse("gather_upcoming_events", args=(location.slug,)),
        "profile_url": "https://"
        + domain
        + reverse("user_detail", args=(use.user.username,)),
        "booking_url": "https://"
        + domain
        + reverse(
            "booking_detail",
            args=(
                location.slug,
                use.booking.id,
            ),
        ),
        "intersecting_bookings": intersecting_uses,
        "intersecting_events": intersecting_events,
        "residents": residents,
    }
    text_content, html_content = render_templates(
        c, location, LocationEmailTemplate.WELCOME
    )

    mailgun_data = {
        "from": use.location.from_email(),
        "to": [use.user.email],
        "subject": f"[{use.location.email_subject_prefix}] See you on {day_of_week}",
        "text": text_content,
    }
    if html_content:
        mailgun_data["html"] = html_content

    return mailgun_send(mailgun_data)


############################################
#             LOCATION EMAILS              #
############################################


def guests_residents_daily_update(location):
    # this is split out by location because each location has a timezone that affects the value of 'today'
    today = timezone.localtime(timezone.now())
    arriving_today = (
        Use.objects.filter(location=location)
        .filter(arrive=today)
        .filter(status="confirmed")
    )
    departing_today = (
        Use.objects.filter(location=location)
        .filter(depart=today)
        .filter(status="confirmed")
    )
    events_today = published_events_today_local(location=location)

    if not arriving_today and not departing_today and not events_today:
        logger.debug(
            f"Nothing happening today at {location.name}, skipping daily email"
        )
        return

    subject = f"[{location.email_subject_prefix}] Events, Arrivals and Departures for {str(today.date())}"

    admin_emails = []
    for admin in location.house_admins.all():
        if admin.email not in admin_emails:
            admin_emails.append(admin.email)

    to_emails = []
    for r in Use.objects.confirmed_on_date(today, location):
        if (r.user.email not in admin_emails) and (r.user.email not in to_emails):
            to_emails.append(r.user.email)

    # Add all the non-admin residents at this location (admins get a different
    # email)
    for r in location.residents():
        if (r.email not in admin_emails) and (r.email not in to_emails):
            to_emails.append(r.email)

    if len(to_emails) == 0:
        logger.debug("No non-admins to send daily update to")
        return None

    c = {
        "today": today,
        "domain": Site.objects.get_current().domain,
        "location": location,
        "arriving": arriving_today,
        "departing": departing_today,
        "events_today": events_today,
    }
    text_content, html_content = render_templates(
        c, location, LocationEmailTemplate.GUEST_DAILY
    )

    mailgun_data = {
        "from": location.from_email(),
        "to": to_emails,
        "subject": subject,
        "text": text_content,
    }
    if html_content:
        mailgun_data["html"] = html_content

    return mailgun_send(mailgun_data)


def admin_daily_update(location):
    # this is split out by location because each location has a timezone that affects the value of 'today'
    today = timezone.localtime(timezone.now()).date()
    arriving_today = (
        Use.objects.filter(location=location)
        .filter(arrive=today)
        .filter(status="confirmed")
    )
    maybe_arriving_today = (
        Use.objects.filter(location=location)
        .filter(arrive=today)
        .filter(status="approved")
    )
    pending_now = Use.objects.filter(location=location).filter(status="pending")
    approved_now = Use.objects.filter(location=location).filter(status="approved")
    departing_today = (
        Use.objects.filter(location=location)
        .filter(depart=today)
        .filter(status="confirmed")
    )
    events_today = published_events_today_local(location=location)
    pending_or_feedback = events_pending(location=location)

    if (
        not arriving_today
        and not departing_today
        and not events_today
        and not maybe_arriving_today
        and not pending_now
        and not approved_now
    ):
        logger.debug(
            f"Nothing happening today at {location.name}, skipping daily email"
        )
        return

    subject = f"[{location.email_subject_prefix}] {today} Events and Guests"

    admins_emails = []
    for admin in location.house_admins.all():
        if admin.email not in admins_emails:
            admins_emails.append(admin.email)
    if len(admins_emails) == 0:
        logger.debug(f"{location.slug}: No admins to send to")
        return None

    c = {
        "today": today,
        "domain": Site.objects.get_current().domain,
        "location": location,
        "arriving": arriving_today,
        "maybe_arriving": maybe_arriving_today,
        "pending_now": pending_now,
        "approved_now": approved_now,
        "departing": departing_today,
        "events_today": events_today,
        "events_pending": pending_or_feedback["pending"],
        "events_feedback": pending_or_feedback["feedback"],
    }
    text_content, html_content = render_templates(
        c, location, LocationEmailTemplate.ADMIN_DAILY
    )

    mailgun_data = {
        "from": location.from_email(),
        "to": admins_emails,
        "subject": subject,
        "text": text_content,
    }
    if html_content:
        mailgun_data["html"] = html_content

    return mailgun_send(mailgun_data)


############################################
#              EMAIL ENDPOINTS             #
############################################


@csrf_exempt
def current(request, location_slug):
    """email all residents, guests and admins who are current or currently at this location."""
    from_address = request.POST.get("from")
    if not User.objects.filter(email=from_address).exists():
        # You should only be able to send email if you are an already registered
        # user.
        return

    # fail gracefully if location does not exist
    try:
        location = get_location(location_slug)
    except Exception:
        # XXX TODO reject and bounce back to sender?
        logger.error("location not found")
        return HttpResponse(status=200)
    logger.debug(f"current@ for location: {location}")
    today = timezone.localtime(timezone.now())

    # we think that message_headers is a list of strings
    header_txt = request.POST.get("message-headers")
    message_headers = json.loads(header_txt)
    message_header_keys = [item[0] for item in message_headers]

    # make sure this isn't an email we have already forwarded (cf. emailbombgate 2014)
    # A List-Id header will only be present if it has been added manually in
    # this function, ie, if we have already processed this message.
    if request.POST.get("List-Id") or "List-Id" in message_header_keys:
        # mailgun requires a code 200 or it will continue to retry delivery
        logger.debug("List-Id header was found! Dropping message silently")
        return HttpResponse(status=200)

    # if 'Auto-Submitted' in message_headers or message_headers['Auto-Submitted'] != 'no':
    if "Auto-Submitted" in message_header_keys:
        logger.info("message appears to be auto-submitted. reject silently")
        return HttpResponse(status=200)

    recipient = request.POST.get("recipient")
    logger.debug(f"from: {from_address}")
    sender = request.POST.get("sender")
    logger.debug(f"sender: {sender}")
    subject = request.POST.get("subject")
    body_plain = request.POST.get("body-plain")
    body_html = request.POST.get("body-html")

    # Add any current bookings
    current_emails = []
    for r in Use.objects.confirmed_on_date(today, location):
        current_emails.append(r.user.email)

    # Add all the residents at this location
    for r in location.residents():
        current_emails.append(r.email)

    # Add the house admins
    for a in location.house_admins.all():
        current_emails.append(a.email)

    # Now loop through all the emails and build the bcc list we will use.
    # This makes sure there are no duplicate emails.
    bcc_list = []
    for email in current_emails:
        if email not in bcc_list:
            bcc_list.append(email)
    logger.debug(f"bcc list: {bcc_list}")

    # Make sure this person can post to our list
    # if not sender in bcc_list:
    #    # TODO - This shoud possibly send a response so they know they were blocked
    #    logger.warn("Sender (%s) not allowed.  Exiting quietly." % sender)
    #    return HttpResponse(status=200)
    if sender in bcc_list:
        bcc_list.remove(sender)

    # Include attachements
    attachments = []
    for attachment in request.FILES.values():
        attachments.append(("attachment", attachment))

    # prefix subject, but only if the prefix string isn't already in the
    # subject line (such as a reply)
    if subject.find(location.email_subject_prefix) < 0:
        prefix = (
            "[" + location.email_subject_prefix + "] [Current Guests and Residents] "
        )
        subject = prefix + subject
    logger.debug(f"subject: {subject}")

    # add in footer
    text_footer = f"""\n\n-------------------------------------------\nYou are receiving this email because you are a current guest or resident at {location.name}. This list is used to share questions, ideas and activities with others currently at this location. Feel free to respond."""
    body_plain = body_plain + text_footer
    if body_html:
        html_footer = f"""<br><br>-------------------------------------------<br>You are receiving this email because you are a current guest or resident at {location.name}. This list is used to share questions, ideas and activities with others currently at this location. Feel free to respond."""
        body_html = body_html + html_footer

    # send the message
    list_address = f"current@{location.slug}.{settings.LIST_DOMAIN}"
    mailgun_data = {
        "from": from_address,
        "to": [
            recipient,
        ],
        "bcc": bcc_list,
        "subject": subject,
        "text": body_plain,
        "html": body_html,
        # attach some headers: LIST-ID, REPLY-TO, MSG-ID, precedence...
        # Precedence: list - helps some out of office auto responders know not to send their auto-replies.
        "h:List-Id": list_address,
        "h:Precedence": "list",
        # Reply-To: list email apparently has some religious debates
        # (http://www.gnu.org/software/mailman/mailman-admin/node11.html) but seems
        # to be common these days
        "h:Reply-To": list_address,
    }
    return mailgun_send(mailgun_data, attachments)


@csrf_exempt
def unsubscribe(request, location_slug):
    """unsubscribe route"""
    # fail gracefully if location does not exist
    try:
        location = get_location(location_slug)
    except Exception:
        # XXX TODO reject and bounce back to sender?
        return HttpResponse(status=200)
    logger.debug(f"unsubscribe@ for location: {location}")
    logger.debug(request.POST)
    logger.debug(request.FILES)
    return HttpResponse(status=200)


@csrf_exempt
def test80085(request, location_slug):
    """test route"""
    # fail gracefully if location does not exist
    try:
        location = get_location(location_slug)
    except Exception:
        # XXX TODO reject and bounce back to sender?
        return HttpResponse(status=200)
    logger.debug(f"test80085@ for location: {location}")
    logger.debug(request.POST)
    logger.debug(request.FILES)

    # we think that message_headers is a list of strings
    header_txt = request.POST.get("message-headers")
    message_headers = json.loads(header_txt)
    message_header_keys = [item[0] for item in message_headers]

    # make sure this isn't an email we have already forwarded (cf. emailbombgate 2014)
    # A List-Id header will only be present if it has been added manually in
    # this function, ie, if we have already processed this message.
    if request.POST.get("List-Id") or "List-Id" in message_header_keys:
        # mailgun requires a code 200 or it will continue to retry delivery
        logger.debug("List-Id header was found! Dropping message silently")
        return HttpResponse(status=200)

    # if 'Auto-Submitted' in message_headers or message_headers['Auto-Submitted'] != 'no':
    if "Auto-Submitted" in message_header_keys:
        logger.info("message appears to be auto-submitted. reject silently")
        return HttpResponse(status=200)

    recipient = request.POST.get("recipient")
    request.POST.get("To")
    from_address = request.POST.get("from")
    logger.debug(f"from: {from_address}")
    sender = request.POST.get("sender")
    logger.debug(f"sender: {sender}")
    subject = request.POST.get("subject")
    body_plain = request.POST.get("body-plain")
    body_html = request.POST.get("body-html")

    # retrieve the current house admins for this location
    bcc_list = ["jsayles@gmail.com", "jessy@jessykate.com"]
    logger.debug(f"bcc list: {bcc_list}")

    # Make sure this person can post to our list
    # if not sender in bcc_list:
    #    # TODO - This shoud possibly send a response so they know they were blocked
    #    logger.warn("Sender (%s) not allowed.  Exiting quietly." % sender)
    #    return HttpResponse(status=200)

    # usually we would remove the sender from receiving the email but because
    # we're testing, let 'em have it.
    # if sender in bcc_list:
    #    bcc_list.remove(sender)

    # pass through attachments
    # logger.debug(request)
    # logger.debug(request.FILES)
    # for attachment in request.FILES.values():
    #     # JKS NOTE! this does NOT work with unicode-encoded data. i'm not
    #     # actually sure that we should *expect* to receive unicode-encoded
    #     # attachments, but it definitely breaks (which i disocvered because
    #     # mailgun sends its test POST with a unicode-encoded attachment).
    #     a_file = default_storage.save(attachment.name, ContentFile(attachment.read()))
    # attachments = {}
    # num = 0
    # for attachment in request.FILES.values():
    #     attachments["attachment-%d" % num] = (attachment.name, default_storage.open(attachment.name, 'rb').read())
    #     #default_storage.delete(attachment.name)
    #     num+= 1

    attachments = []
    for attachment in request.FILES.values():
        attachments.append(("inline", attachment))

    # prefix subject, but only if the prefix string isn't already in the
    # subject line (such as a reply)
    if subject.find("EN Test") < 0:
        prefix = "[EN Test!] "
        subject = prefix + subject
    logger.debug(f"subject: {subject}")

    # add in footer
    text_footer = """\n\n-------------------------------------------\nYou are receiving this email because someone at Embassy Network wanted to use you as a guinea pig. %mailing_list_unsubscribe_url%"""
    body_plain = body_plain + text_footer
    if body_html:
        html_footer = """<br><br>-------------------------------------------<br>You are receiving this email because someone at Embassy Network wanted to use you as a guinea pig."""
        body_html = body_html + html_footer

    # send the message
    list_address = "test80085@" + location.slug + ".mail.embassynetwork.com"
    mailgun_data = {
        "from": from_address,
        "to": [
            recipient,
        ],
        "bcc": bcc_list,
        "subject": subject,
        "text": body_plain,
        "html": body_html,
        # attach some headers: LIST-ID, REPLY-TO, MSG-ID, precedence...
        # Precedence: list - helps some out of office auto responders know not to send their auto-replies.
        "h:List-Id": list_address,
        "h:Precedence": "list",
        # Reply-To: list email apparently has some religious debates
        # (http://www.gnu.org/software/mailman/mailman-admin/node11.html) but seems
        # to be common these days
        "h:Reply-To": from_address,
    }
    return mailgun_send(mailgun_data, attachments)


@csrf_exempt
def stay(request, location_slug):
    """email all admins at this location."""
    # fail gracefully if location does not exist
    try:
        location = get_location(location_slug)
    except Exception:
        # XXX TODO reject and bounce back to sender?
        return HttpResponse(status=200)
    logger.debug(f"stay@ for location: {location}")
    logger.debug(request.POST)

    # we think that message_headers is a list of strings
    header_txt = request.POST.get("message-headers")
    message_headers = json.loads(header_txt)
    message_header_keys = [item[0] for item in message_headers]

    # make sure this isn't an email we have already forwarded (cf. emailbombgate 2014)
    # A List-Id header will only be present if it has been added manually in
    # this function, ie, if we have already processed this message.
    if request.POST.get("List-Id") or "List-Id" in message_header_keys:
        # mailgun requires a code 200 or it will continue to retry delivery
        logger.debug("List-Id header was found! Dropping message silently")
        return HttpResponse(status=200)

    # if 'Auto-Submitted' in message_headers or message_headers['Auto-Submitted'] != 'no':
    if "Auto-Submitted" in message_header_keys:
        logger.info("message appears to be auto-submitted. reject silently")
        return HttpResponse(status=200)

    recipient = request.POST.get("recipient")
    request.POST.get("To")
    from_address = request.POST.get("from")
    logger.debug(f"from: {from_address}")
    sender = request.POST.get("sender")
    logger.debug(f"sender: {sender}")
    subject = request.POST.get("subject")
    body_plain = request.POST.get("body-plain")
    body_html = request.POST.get("body-html")

    # retrieve the current house admins for this location
    location_admins = location.house_admins.all()
    bcc_list = []
    for person in location_admins:
        if person.email not in bcc_list:
            bcc_list.append(person.email)
    logger.debug(f"bcc list: {bcc_list}")

    # Make sure this person can post to our list
    # if not sender in bcc_list:
    #    # TODO - This shoud possibly send a response so they know they were blocked
    #    logger.warn("Sender (%s) not allowed.  Exiting quietly." % sender)
    #    return HttpResponse(status=200)
    if sender in bcc_list:
        bcc_list.remove(sender)

    # Include attachements
    attachments = []
    for attachment in request.FILES.values():
        attachments.append(("attachment", attachment))

    # prefix subject, but only if the prefix string isn't already in the
    # subject line (such as a reply)
    if subject.find(location.email_subject_prefix) < 0:
        prefix = "[" + location.email_subject_prefix + "] [Admin] "
        subject = prefix + subject
    logger.debug(f"subject: {subject}")

    # add in footer
    text_footer = f"""\n\n-------------------------------------------\nYou are receiving email to {recipient} because you are a location admin at {location.name}. Send mail to this list to reach other admins."""
    body_plain = body_plain + text_footer
    if body_html:
        html_footer = f"""<br><br>-------------------------------------------<br>You are receiving email to {recipient} because you are a location admin at {location.name}. Send mail to this list to reach other admins."""
        body_html = body_html + html_footer

    # send the message
    list_address = location.from_email()
    mailgun_data = {
        "from": from_address,
        "to": [
            recipient,
        ],
        "bcc": bcc_list,
        "subject": subject,
        "text": body_plain,
        "html": body_html,
        # attach some headers: LIST-ID, REPLY-TO, MSG-ID, precedence...
        # Precedence: list - helps some out of office auto responders know not to send their auto-replies.
        "h:List-Id": list_address,
        "h:Precedence": "list",
        # Reply-To: list email apparently has some religious debates
        # (http://www.gnu.org/software/mailman/mailman-admin/node11.html) but seems
        # to be common these days
        "h:Reply-To": from_address,
    }
    return mailgun_send(mailgun_data, attachments)


# XXX TODO there is a lot of duplication in these email endpoints. should be
# able to pull out this code into some common reuseable functions.
@csrf_exempt
def residents(request, location_slug):
    """email all residents at this location."""

    # fail gracefully if location does not exist
    try:
        location = get_location(location_slug)
    except Exception:
        # XXX TODO reject and bounce back to sender?
        logger.error("location not found")
        return HttpResponse(status=200)
    logger.debug(f"residents@ for location: {location}")

    # we think that message_headers is a list of strings
    header_txt = request.POST.get("message-headers")
    message_headers = json.loads(header_txt)
    message_header_keys = [item[0] for item in message_headers]

    # make sure this isn't an email we have already forwarded (cf. emailbombgate 2014)
    # A List-Id header will only be present if it has been added manually in
    # this function, ie, if we have already processed this message.
    if request.POST.get("List-Id") or "List-Id" in message_header_keys:
        # mailgun requires a code 200 or it will continue to retry delivery
        logger.debug("List-Id header was found! Dropping message silently")
        return HttpResponse(status=200)

    # if 'Auto-Submitted' in message_headers or message_headers['Auto-Submitted'] != 'no':
    if "Auto-Submitted" in message_header_keys:
        logger.info("message appears to be auto-submitted. reject silently")
        return HttpResponse(status=200)

    recipient = request.POST.get("recipient")
    from_address = request.POST.get("from")
    logger.debug(f"from: {from_address}")
    sender = request.POST.get("sender")
    logger.debug(f"sender: {sender}")
    subject = request.POST.get("subject")
    body_plain = request.POST.get("body-plain")
    body_html = request.POST.get("body-html")

    # Add all the residents at this location
    resident_emails = []
    for r in location.residents():
        resident_emails.append(r.email)

    # Now loop through all the emails and build the bcc list we will use.
    # This makes sure there are no duplicate emails.
    bcc_list = []
    for email in resident_emails:
        if email not in bcc_list:
            bcc_list.append(email)
    logger.debug(f"bcc list: {bcc_list}")

    # Make sure this person can post to our list
    # if not sender in bcc_list:
    #    # TODO - This shoud possibly send a response so they know they were blocked
    #    logger.warn("Sender (%s) not allowed.  Exiting quietly." % sender)
    #    return HttpResponse(status=200)
    if sender in bcc_list:
        bcc_list.remove(sender)

    # Include attachements
    attachments = []
    for attachment in request.FILES.values():
        attachments.append(("attachment", attachment))

    # prefix subject, but only if the prefix string isn't already in the
    # subject line (such as a reply)
    if subject.find(location.email_subject_prefix) < 0:
        prefix = "[" + location.email_subject_prefix + "] "
        subject = prefix + subject
    logger.debug(f"subject: {subject}")

    # add in footer
    text_footer = f"""\n\n-------------------------------------------\n*~*~*~* {location.name} residents email list *~*~*~* """
    body_plain = body_plain + text_footer

    if body_html:
        html_footer = f"""<br><br>-------------------------------------------<br>*~*~*~* {location.name} residents email list *~*~*~* """
        body_html = body_html + html_footer

    # send the message
    list_address = f"residents@{location.slug}.{settings.LIST_DOMAIN}"
    mailgun_data = {
        "from": from_address,
        "to": [
            recipient,
        ],
        "bcc": bcc_list,
        "subject": subject,
        "text": body_plain,
        "html": body_html,
        # attach some headers: LIST-ID, REPLY-TO, MSG-ID, precedence...
        # Precedence: list - helps some out of office auto responders know not to send their auto-replies.
        "h:List-Id": list_address,
        "h:Precedence": "list",
        # Reply-To: list email apparently has some religious debates
        # (http://www.gnu.org/software/mailman/mailman-admin/node11.html) but seems
        # to be common these days
        "h:Reply-To": list_address,
    }
    return mailgun_send(mailgun_data, attachments)


@csrf_exempt
def announce(request, location_slug):
    """email all people signed up for event activity notifications at this location."""

    # fail gracefully if location does not exist
    try:
        location = get_location(location_slug)
    except Exception:
        # XXX TODO reject and bounce back to sender?
        logger.error("location not found")
        return HttpResponse(status=200)
    logger.debug(f"announce@ for location: {location}")

    # Make sure this person can post to our list
    sender = request.POST.get("from")
    location_event_admins = EventAdminGroup.objects.get(location=location).users.all()
    allowed_senders = [user.email for user in location_event_admins]
    # have to be careful here because the "sender" email address is likely a
    # substring of the entire 'from' field in the POST request, ie, "Jessy Kate
    # Schingler <jessy@jessykate.com>"
    this_sender_allowed = False
    for sender_email in allowed_senders:
        if sender_email in sender:
            this_sender_allowed = True
            break
    if not this_sender_allowed:
        # TODO - This could send a response so they know they were blocked
        logger.warn(f"Sender ({sender}) not allowed.  Exiting quietly.")
        return HttpResponse(status=200)

    weekly_notifications_on = EventNotifications.objects.filter(
        location_weekly=location
    )
    [notify.user for notify in weekly_notifications_on]

    # TESTING
    jessy = User.objects.get(id=1)
    for user in [
        jessy,
    ]:
        # for user in remindees_for_location:
        send_announce(request, user, location)

    return HttpResponse(status=200)


def send_announce(request, user, location):
    from_address = location.from_email()
    subject = request.POST.get("subject")
    body_plain = request.POST.get("body-plain")
    body_html = request.POST.get("body-html")

    # Include attachements
    attachments = []
    for attachment in request.FILES.values():
        attachments.append(("attachment", attachment))

    prefix = "[" + location.email_subject_prefix + "] "
    subject = prefix + subject
    logger.debug(f"subject: {subject}")

    # add in footer
    text_footer = f"""\n\n-------------------------------------------\n*~*~*~* {location.name} Announce *~*~*~* """
    body_plain = body_plain + text_footer

    if body_html:
        html_footer = f"""<br><br>-------------------------------------------<br>*~*~*~* {location.name} Announce *~*~*~* """
        body_html = body_html + html_footer

    # send the message
    mailgun_data = {
        "from": from_address,
        "to": [
            user.email,
        ],
        "subject": subject,
        "text": body_plain,
        "html": body_html,
    }
    return mailgun_send(mailgun_data, attachments)
