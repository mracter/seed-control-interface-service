# -*- coding: utf-8 -*-
# Generated by Django 1.9.1 on 2016-09-22 16:34
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboards', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='widget',
            name='nulls',
            field=models.CharField(blank=True, choices=[(b'omit', b'Omit'), (b'zeroize', b'Zeroize')], max_length=20, null=True),
        ),
    ]