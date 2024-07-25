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
            name='MatrixHomeserver',
            fields=[
                ('service_ptr', models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to='fractal_database.service')),
                ('url', models.URLField(unique=True)),
                ('priority', models.PositiveIntegerField(blank=True, default=0, null=True)),
                ('registration_token', models.CharField(blank=True, max_length=255, null=True)),
            ],
            options={
                'abstract': False,
            },
            bases=('fractal_database.service',),
        ),
        migrations.CreateModel(
            name='MatrixCredentials',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('date_created', models.DateTimeField(auto_now_add=True)),
                ('date_modified', models.DateTimeField(auto_now=True)),
                ('deleted', models.BooleanField(default=False)),
                ('matrix_id', models.CharField(max_length=255)),
                ('password', models.CharField(blank=True, max_length=255, null=True)),
                ('access_token', models.CharField(max_length=255)),
                ('device', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='fractal_database.device')),
                ('homeserver', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='credentials', to='fractal_database_matrix.matrixhomeserver')),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.CreateModel(
            name='InMemoryMatrixCredentials',
            fields=[
            ],
            options={
                'proxy': True,
                'indexes': [],
                'constraints': [],
            },
            bases=('fractal_database_matrix.matrixcredentials',),
        ),
        migrations.CreateModel(
            name='MatrixReplicationChannel',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('date_created', models.DateTimeField(auto_now_add=True)),
                ('date_modified', models.DateTimeField(auto_now=True)),
                ('deleted', models.BooleanField(default=False)),
                ('object_version', models.PositiveIntegerField(default=0)),
                ('name', models.CharField(max_length=255)),
                ('enabled', models.BooleanField(default=True)),
                ('filter', models.CharField(blank=True, max_length=255, null=True)),
                ('target', models.BooleanField(default=True)),
                ('source', models.BooleanField(default=True)),
                ('metadata', models.JSONField(default=dict)),
                ('database_type', models.CharField(max_length=255)),
                ('database', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='fractal_database.database')),
                ('homeserver', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='channels', to='fractal_database_matrix.matrixhomeserver')),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
