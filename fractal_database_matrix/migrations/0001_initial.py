# Generated by Django 5.0 on 2023-12-14 16:18

import django.db.models.deletion
import fractal_database_matrix.representations
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
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
                ('name', models.CharField(max_length=255, unique=True)),
                ('enabled', models.BooleanField(default=True)),
                ('object_id', models.CharField(max_length=255)),
                ('primary', models.BooleanField(default=False)),
                ('metadata', models.JSONField(default=dict)),
                ('access_token', models.CharField(default=None, max_length=255)),
                ('homeserver', models.CharField(default=None, max_length=255)),
                ('content_type', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)s_content_type', to='contenttypes.contenttype')),
            ],
            options={
                'abstract': False,
            },
            bases=(models.Model, fractal_database_matrix.representations.MatrixSpace),
        ),
        migrations.AddConstraint(
            model_name='matrixreplicationtarget',
            constraint=models.UniqueConstraint(condition=models.Q(('primary', True)), fields=('content_type',), name='fractal_database_matrix_matrixreplicationtarget_unique_primary_per_database'),
        ),
    ]
