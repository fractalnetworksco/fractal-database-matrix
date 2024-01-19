# Generated by Django 5.0 on 2024-01-19 16:35

import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('fractal_database', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='MatrixReplicationTarget',
            fields=[
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('date_created', models.DateTimeField(auto_now_add=True)),
                ('date_modified', models.DateTimeField(auto_now=True)),
                ('deleted', models.BooleanField(default=False)),
                ('object_version', models.PositiveIntegerField(default=0)),
                ('name', models.CharField(max_length=255)),
                ('enabled', models.BooleanField(default=True)),
                ('primary', models.BooleanField(default=False)),
                ('metadata', models.JSONField(default=dict)),
                ('registration_token', models.CharField(blank=True, max_length=255, null=True)),
                ('access_token', models.CharField(blank=True, max_length=255, null=True)),
                ('homeserver', models.CharField(max_length=255)),
                ('database', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='fractal_database.database')),
                ('databases', models.ManyToManyField(related_name='%(app_label)s_%(class)s_target_type', to='fractal_database.database')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='MatrixCredentials',
            fields=[
                ('uuid', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('date_created', models.DateTimeField(auto_now_add=True)),
                ('date_modified', models.DateTimeField(auto_now=True)),
                ('deleted', models.BooleanField(default=False)),
                ('matrix_id', models.CharField(max_length=255)),
                ('password', models.CharField(blank=True, max_length=255, null=True)),
                ('access_token', models.CharField(max_length=255)),
                ('device', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='fractal_database.device')),
                ('target', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to='fractal_database_matrix.matrixreplicationtarget')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.AddConstraint(
            model_name='matrixreplicationtarget',
            constraint=models.UniqueConstraint(condition=models.Q(('primary', True)), fields=('database',), name='fractal_database_matrix_matrixreplicationtarget_unique_primary_per_database'),
        ),
    ]
