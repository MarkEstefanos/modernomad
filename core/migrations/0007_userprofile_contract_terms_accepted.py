# Generated by Django 5.0.7 on 2024-11-03 15:57

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_remove_backing_subscription_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="contract_terms_accepted",
            field=models.BooleanField(default=False),
        ),
    ]