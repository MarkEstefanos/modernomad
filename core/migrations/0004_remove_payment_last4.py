# Generated by Django 5.0.6 on 2024-06-30 00:35

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_userprofile_stripe_payment_method_id"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="payment",
            name="last4",
        ),
    ]
