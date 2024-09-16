import fractal_database.fields
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('fractal_database_matrix', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='matrixhomeserver',
            name='local_url',
            field=fractal_database.fields.LocalURLField(blank=True, null=True),
        ),
    ]
