# -*- coding: utf-8 -*-
# Generated by Django 1.10.2 on 2016-12-28 02:19
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pinax_stripe', '0007_auto_20161227_1818'),
    ]

    operations = [
        migrations.AlterField(
            model_name='account',
            name='display_name',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='account',
            name='email',
            field=models.TextField(blank=True, null=True),
        ),
    ]
